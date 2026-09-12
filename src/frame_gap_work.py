"""Persistent finite gap requests, publishing only after source verification."""
from contextlib import contextmanager
import hashlib,json,math,os,sqlite3,time
from .imports import now,_source_stat
from .kit import create_fact_table,insert_fact


@contextmanager
def source_stream(row,cache):
    from .video import verified_copy
    before=_source_stat(row);path=verified_copy(row,cache)
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        if opened.st_size!=row['size'] or hashlib.file_digest(stream,'sha256').hexdigest()!=row['content_hash']:raise ValueError('Local verified copy changed')
        stream.seek(0);yield stream
        finished=os.fstat(stream.fileno())
        if (opened.st_size,opened.st_mtime_ns,opened.st_ino)!=(finished.st_size,finished.st_mtime_ns,finished.st_ino):raise ValueError('Local copy changed during decode')
    after=_source_stat(row)
    if before.st_ino!=after.st_ino:raise ValueError('Source changed')


def observations(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='frame_gap_work'").fetchone():return []
    current=conn.execute("SELECT value FROM settings WHERE key='frame_gap_decoder'").fetchone()
    if not current:return []
    cursor=conn.execute('''SELECT f.* FROM frame_gap_facts f JOIN frame_gap_work w
      ON f.content_hash=w.content_hash AND f.plan_revision=w.revision AND f.sampler=w.decoder
      WHERE w.active=1 AND w.decoder=?''',(current[0],))
    columns=[r[0] for r in cursor.description]
    return [dict(zip(columns,row)) for row in cursor]


def coverage(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='frame_gap_work'").fetchone():return None
    counts=dict(conn.execute('''SELECT r.status,count(*) FROM frame_gap_requests r JOIN frame_gap_work w
      ON r.content_hash=w.content_hash AND r.revision=w.revision AND r.decoder=w.decoder
      WHERE w.active=1 GROUP BY r.status'''))
    total=sum(len(json.loads(r[0])['targets']) for r in conn.execute('SELECT plan_json FROM frame_gap_work WHERE active=1'))
    return {'requested':total,'completed':counts.get('complete',0),'failed':counts.get('error',0),'pending':total-sum(counts.values())}


def setup(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS frame_gap_work(content_hash TEXT,revision TEXT,decoder TEXT,plan_json TEXT,next_index INTEGER,batch_end INTEGER,status TEXT,active INTEGER,derived_at TEXT,PRIMARY KEY(content_hash,revision,decoder))')
    conn.execute('CREATE TABLE IF NOT EXISTS frame_gap_requests(content_hash TEXT,revision TEXT,decoder TEXT,request_index INTEGER,status TEXT,frame_id TEXT,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,revision,decoder,request_index))')
    create_fact_table(conn,'frame_gap_facts',{'frame_id':'TEXT PRIMARY KEY','content_hash':'TEXT NOT NULL','sampler':'TEXT NOT NULL','timestamp':'REAL NOT NULL','filename':'TEXT NOT NULL','image_hash':'TEXT NOT NULL','interval':'REAL NOT NULL','duration':'REAL NOT NULL','requested_timestamp':'REAL NOT NULL','plan_revision':'TEXT NOT NULL'})


def outcomes(conn,key,start,end,error):
    for index in range(start,end):conn.execute('INSERT OR IGNORE INTO frame_gap_requests VALUES(?,?,?,?,?,?,?,?)',(*key,index,'error',None,error,now()))
    raw=conn.execute('SELECT plan_json FROM frame_gap_work WHERE content_hash=? AND revision=? AND decoder=?',key).fetchone()[0]
    conn.execute('UPDATE frame_gap_work SET next_index=?,status=?,derived_at=? WHERE content_hash=? AND revision=? AND decoder=?',(end,'complete' if end==len(json.loads(raw)['targets']) else 'pending',now(),*key))


def validate_result(result,plan,start,end,decoder,root):
    from .scenes import sample_path
    next_index=result['next_index']
    if isinstance(next_index,bool) or not isinstance(next_index,int) or not start<=next_index<=end:raise ValueError('Invalid gap cursor')
    requests=result['outcomes'];frames=result['frames']
    if [r['request_index'] for r in requests]!=list(range(start,next_index)):raise ValueError('Incomplete gap outcomes')
    by_index={f['request_index']:f for f in frames}
    if len(by_index)!=len(frames):raise ValueError('Duplicate gap frames')
    successful=set()
    for r in requests:
        if r['status']=='error':
            if not isinstance(r.get('error'),str) or not r['error']:raise ValueError('Missing request failure')
            continue
        if r['status']!='complete' or r['request_index'] not in by_index:raise ValueError('Invalid request result')
        f=by_index[r['request_index']];target=plan['targets'][r['request_index']]
        if f['frame_id']!=r['frame_id'] or f['decoder']!=decoder or f['plan_revision']!=plan['revision'] or f['requested_timestamp']!=target or f['duration']!=plan['duration_seconds']:raise ValueError('Gap provenance mismatch')
        if not isinstance(f['timestamp'],(int,float)) or not math.isfinite(f['timestamp']) or not target<=f['timestamp']<=min(target+1,plan['duration_seconds']):raise ValueError('Invalid actual timestamp')
        sample_path(root.parent,f);successful.add(r['request_index'])
    if set(by_index)!=successful:raise ValueError('Unattributed gap frames')


def run(conn,candidates,root,deadline,limit,backend=None,reader=source_stream):
    from .frames import gap_plans
    setup(conn)
    with conn:
        for digest,revision,decoder,start,end in conn.execute("SELECT content_hash,revision,decoder,next_index,batch_end FROM frame_gap_work WHERE status='running'").fetchall():outcomes(conn,(digest,revision,decoder),start,end,'InterruptedAttempt')
    stored=conn.execute("SELECT value FROM settings WHERE key='frames_compatible'").fetchone();models=json.loads(stored[0]) if stored else []
    cursor=conn.execute('SELECT * FROM frame_facts WHERE sampler IN ('+','.join('?' for _ in models)+')',models)
    columns=[r[0] for r in cursor.description];rows=[dict(zip(columns,r)) for r in cursor];plans=gap_plans(r for r in rows if r['content_hash'] in candidates)
    with conn:conn.execute('UPDATE frame_gap_work SET active=0 WHERE active=1')
    if backend is None:
        if not any(p['status']=='planned' for p in plans.values()):return {'processed':0,'remaining':0,'frames':0}
        from .gap_decode import GapDecoder
        backend=GapDecoder()
    with conn:
        conn.execute("INSERT OR REPLACE INTO settings VALUES('frame_gap_decoder',?)",(backend.identity,))
        for digest,plan in plans.items():
            if plan['status']!='planned':continue
            conn.execute('INSERT OR IGNORE INTO frame_gap_work VALUES(?,?,?,?,?,?,?,?,?)',(digest,plan['revision'],backend.identity,json.dumps(plan),0,0,'pending',1,now()))
            conn.execute('UPDATE frame_gap_work SET active=1 WHERE content_hash=? AND revision=? AND decoder=?',(digest,plan['revision'],backend.identity))
    processed=0
    work=conn.execute('SELECT content_hash,revision,decoder,plan_json,next_index FROM frame_gap_work WHERE active=1 ORDER BY content_hash').fetchall()
    for digest,revision,decoder,raw,start in work:
        plan=json.loads(raw);end=min(len(plan['targets']),start+64,start+limit-processed)
        if end<=start or time.monotonic()>=deadline:continue
        key=(digest,revision,decoder)
        with conn:conn.execute("UPDATE frame_gap_work SET status='running',batch_end=?,derived_at=? WHERE content_hash=? AND revision=? AND decoder=?",(end,now(),*key))
        result=None;error='NoReadableSource';source=None
        for source in candidates[digest]:
            try:
                with reader(source,root.parent/'playback') as stream:
                    seconds=min(120,deadline-time.monotonic())
                    if seconds<=0:result={'frames':[],'outcomes':[],'next_index':start}
                    else:result=backend.decode(stream,root,digest,plan,start_index=start,limit=end-start,seconds=seconds)
                validate_result(result,plan,start,end,decoder,root);error=None;break
            except Exception as exc:result=None;error=type(exc).__name__
        with conn:
            if error:outcomes(conn,key,start,end,error);next_index=end
            else:
                next_index=result['next_index']
                for f in result['frames']:
                    span={'member':source['member'],'offset':source['offset'],'requested_timestamp':f['requested_timestamp'],'timestamp':f['timestamp'],'pts':f['pts'],'time_base':f['time_base'],'plan_revision':revision,'decoder':decoder}
                    insert_fact(conn,'frame_gap_facts',{**{k:f[k] for k in ('frame_id','timestamp','filename','image_hash','duration','requested_timestamp')},'content_hash':digest,'sampler':decoder,'interval':plan['policy']['spacing_seconds'],'plan_revision':revision,'source_path':source['source'],'source_span':json.dumps(span),'extractor':'pyav-gap-snapshot','extractor_version':'1','confidence':1.,'derived_at':now(),'tier':'personal'})
                for r in result['outcomes']:conn.execute('INSERT INTO frame_gap_requests VALUES(?,?,?,?,?,?,?,?)',(*key,r['request_index'],r['status'],r.get('frame_id'),r.get('error'),now()))
            conn.execute('UPDATE frame_gap_work SET next_index=?,status=?,derived_at=? WHERE content_hash=? AND revision=? AND decoder=?',(next_index,'complete' if next_index==len(plan['targets']) else 'pending',now(),*key))
        processed+=next_index-start
    remaining=sum(len(json.loads(raw)['targets'])-index for raw,index in conn.execute('SELECT plan_json,next_index FROM frame_gap_work WHERE active=1'))
    return {'processed':processed,'remaining':remaining,'frames':len(observations(conn))}
