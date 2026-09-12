"""Checkpoint bounded long-video speech windows in the existing transcript store."""
from contextlib import closing, contextmanager
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import uuid
import wave
from .imports import now
from .kit import insert_fact
from .portable import assert_local_state

POLICY='long-chunks-1'
RECOVERY='valid-segments-1'
WINDOW=600
SUCCESS=('complete','no_speech')


def setup(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS transcript_chunks(
        content_hash TEXT,model TEXT,policy TEXT,start REAL,end REAL,status TEXT,
        segments INTEGER,error TEXT,derived_at TEXT,
        PRIMARY KEY(content_hash,model,policy,start))''')



def recoverable(conn,backend,candidates):
    if not callable(getattr(backend,'transcribe_raw',None)):return
    conn.execute('CREATE TABLE IF NOT EXISTS transcript_chunk_attempt_history(content_hash TEXT,model TEXT,policy TEXT,start REAL,retry_policy TEXT,status TEXT,segments INTEGER,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model,policy,start,retry_policy))')
    rows=conn.execute("SELECT content_hash,start FROM transcript_chunks c WHERE model=? AND policy=? AND status='error' AND error='ValueError' AND segments=0 AND NOT EXISTS(SELECT 1 FROM transcript_chunk_attempt_history h WHERE h.content_hash=c.content_hash AND h.model=c.model AND h.policy=c.policy AND h.start=c.start AND h.retry_policy=?)",(backend.identity,POLICY,RECOVERY)).fetchall()
    for digest,start in rows:
        if digest not in candidates:continue
        with conn:
            conn.execute('INSERT INTO transcript_chunk_attempt_history SELECT content_hash,model,policy,start,?,status,segments,error,derived_at FROM transcript_chunks WHERE content_hash=? AND model=? AND policy=? AND start=?',(RECOVERY,digest,backend.identity,POLICY,start))
            conn.execute("UPDATE transcript_chunks SET status='pending',error=NULL,derived_at=? WHERE content_hash=? AND model=? AND policy=? AND start=?",(now(),digest,backend.identity,POLICY,start))
            publish_status(conn,backend.identity,digest)


def validated_chunk(value,window,source_remaining,partial=False):
    from .transcripts import validate
    validate({'duration':value['duration'],'segments':[]})
    if value['status'] not in SUCCESS or not 0<value['duration']<=window+.1:raise ValueError('Invalid chunk extent')
    if (value['status']=='complete')!=bool(value['segments']):raise ValueError('Inconsistent chunk status')
    accepted=[];rejected=0
    for segment in value['segments']:
        try:
            validate({'duration':value['duration'],'segments':[segment]})
            if not 0<=segment['start']<window or not segment['start']<segment['end']<=min(window+1,source_remaining+1):raise ValueError('Timestamp outside chunk')
        except (ValueError,TypeError,KeyError):
            if not partial:raise
            rejected+=1
        else:accepted.append(segment)
    return {**value,'segments':accepted,'status':'error' if rejected else value['status'],'rejected_segments':rejected,'validation_policy':RECOVERY if partial else 'whole-chunk-1'}


def plan(conn,model,candidates,frames_database):
    """Preserve the old failure before enrolling only consistent, bounded sources."""
    if not frames_database.is_file():return
    with closing(sqlite3.connect(frames_database.as_uri()+'?mode=ro',uri=True)) as frames:
        if not frames.execute("SELECT 1 FROM sqlite_master WHERE name='frame_facts'").fetchone():return
        durations={}
        for digest,duration in frames.execute('SELECT content_hash,duration FROM frame_facts'):
            durations.setdefault(digest,[]).append(duration)
    failed=conn.execute("SELECT content_hash FROM transcript_work WHERE model=? AND status='error' AND error='ValueError' AND content_hash NOT IN(SELECT content_hash FROM transcript_attempt_history WHERE model=? AND policy=?)",(model,model,POLICY)).fetchall()
    for (digest,) in failed:
        values=durations.get(digest,[])
        if not values or not all(isinstance(d,(int,float)) and math.isfinite(d) and 7200<d<=21600 for d in values):continue
        if max(values)-min(values)>1 or not any(r['size']<=4*1024**3 for r in candidates.get(digest,[])):continue
        duration=max(values)
        with conn:
            conn.execute('INSERT INTO transcript_attempt_history SELECT content_hash,model,?,status,segments,error,derived_at FROM transcript_work WHERE content_hash=? AND model=?',(POLICY,digest,model))
            for start in range(0,math.ceil(duration),WINDOW):
                conn.execute('INSERT INTO transcript_chunks VALUES(?,?,?,?,?,?,?,?,?)',(digest,model,POLICY,start,min(start+WINDOW,duration),'pending',0,None,now()))
            conn.execute("UPDATE transcript_work SET status='partial',error=NULL WHERE content_hash=? AND model=?",(digest,model))


def coverage(conn,model,digest):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='transcript_chunks'").fetchone():return None
    counts=dict(conn.execute('SELECT status,count(*) FROM transcript_chunks WHERE content_hash=? AND model=? AND policy=? GROUP BY status',(digest,model,POLICY)))
    if not counts:return None
    extra={}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='transcript_chunk_validation'").fetchone():
        rejected=conn.execute('SELECT coalesce(sum(rejected),0) FROM transcript_chunk_validation WHERE content_hash=? AND model=? AND policy=?',(digest,model,POLICY)).fetchone()[0]
        if rejected:extra['rejected_segments']=rejected
    return {**extra,'completed':sum(counts.get(s,0) for s in SUCCESS),'total':sum(counts.values()),'failed':counts.get('error',0),'pending':counts.get('pending',0)+counts.get('running',0),'window_seconds':WINDOW}


def publish_status(conn,model,digest):
    rows=conn.execute('SELECT status,segments FROM transcript_chunks WHERE content_hash=? AND model=? AND policy=?',(digest,model,POLICY)).fetchall()
    segments=sum(r[1] for r in rows);failed=any(r[0]=='error' for r in rows)
    if all(r[0] in SUCCESS for r in rows):status='complete' if segments else 'no_speech'
    else:status='partial' if segments or any(r[0]!='error' for r in rows) else 'error'
    conn.execute('UPDATE transcript_work SET status=?,segments=?,error=?,derived_at=? WHERE content_hash=? AND model=?',(status,segments,'ChunkFailure' if failed else None,now(),digest,model))


@contextmanager
def excerpt(row,start,end,duration,cache):
    """Keep each attempt's PCM and receipt; failed derivatives are never reused."""
    from .video import verified_copy,probe
    source=verified_copy(row,cache/'playback')
    actual=probe(source)
    if abs(actual-duration)>1:raise ValueError('Duration evidence changed')
    root=(cache/'transcript-chunks').resolve()
    assert_local_state(root,'Cache')
    root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root).free<12*1024**3+WINDOW*32000+1024**2:raise RuntimeError('Not enough Linux cache space')
    path=root/(row['content_hash']+'.'+str(int(start))+'.'+uuid.uuid4().hex+'.wav')
    receipt={'content_hash':row['content_hash'],'start':start,'end':end,'source_duration':duration,'policy':POLICY,'settings':'first audio, mono 16kHz PCM s16le','state':'extracting','derived_at':now()}
    record=path.with_suffix('.json');record.write_text(json.dumps(receipt)+'\n')
    try:
        subprocess.run(['ffmpeg','-v','error','-nostdin','-threads','2','-protocol_whitelist','file,pipe','-ss',str(start),'-i',str(source),'-t',str(end-start),'-map','0:a:0','-vn','-map_metadata','-1','-ac','1','-ar','16000','-c:a','pcm_s16le','-threads','2','-n',str(path)],capture_output=True,timeout=90,check=True)
        with wave.open(str(path),'rb') as audio:
            if (audio.getnchannels(),audio.getsampwidth(),audio.getframerate())!=(1,2,16000) or not 0<audio.getnframes()/16000<=end-start+.1:raise ValueError('Invalid PCM extent')
        if path.stat().st_size>WINDOW*32000+1024**2:raise ValueError('Oversized PCM')
        with path.open('rb') as stream:receipt.update(state='ready',sha256=hashlib.file_digest(stream,'sha256').hexdigest(),bytes=path.stat().st_size)
        record.write_text(json.dumps(receipt)+'\n')
    except Exception as exc:
        receipt.update(state='error',error=type(exc).__name__);record.write_text(json.dumps(receipt)+'\n');raise
    with path.open('rb') as stream:yield stream


def run(conn,backend,candidates,frames_database,cache,deadline,limit,reader=None):
    setup(conn);plan(conn,backend.identity,candidates,frames_database)
    conn.execute('CREATE TABLE IF NOT EXISTS transcript_chunk_validation(content_hash TEXT,model TEXT,policy TEXT,start REAL,rejected INTEGER,validation_policy TEXT,PRIMARY KEY(content_hash,model,policy,start))')
    recoverable(conn,backend,candidates)
    with conn:
        interrupted=[r[0] for r in conn.execute("SELECT DISTINCT content_hash FROM transcript_chunks WHERE model=? AND policy=? AND status='running'",(backend.identity,POLICY))]
        conn.execute("UPDATE transcript_chunks SET status='error',error='InterruptedAttempt' WHERE model=? AND policy=? AND status='running'",(backend.identity,POLICY))
        for digest in interrupted:publish_status(conn,backend.identity,digest)
    pending=conn.execute("SELECT content_hash,start,end FROM transcript_chunks WHERE model=? AND policy=? AND status='pending' ORDER BY start,content_hash",(backend.identity,POLICY)).fetchall()
    processed=0
    for digest,start,end in pending:
        if processed>=limit or time.monotonic()>=deadline:break
        if digest not in candidates:continue
        duration=conn.execute('SELECT max(end) FROM transcript_chunks WHERE content_hash=? AND model=? AND policy=?',(digest,backend.identity,POLICY)).fetchone()[0]
        key=(digest,backend.identity,POLICY,start)
        with conn:conn.execute("UPDATE transcript_chunks SET status='running',derived_at=? WHERE content_hash=? AND model=? AND policy=? AND start=?",(now(),*key))
        value=None;error='NoReadableSource'
        for row in candidates[digest]:
            try:
                raw=getattr(backend,'transcribe_raw',None)
                with (reader or excerpt)(row,start,end,duration,cache) as stream:
                    value=validated_chunk(raw(stream) if callable(raw) else backend.transcribe(stream),end-start,duration-start,partial=callable(raw))
                error='InvalidSegments' if value['rejected_segments'] else None;break
            except Exception as exc:value=None;error=type(exc).__name__
        with conn:
            if value:
                for segment in value['segments']:
                    first=start+segment['start'];last=start+segment['end']
                    insert_fact(conn,'transcript_facts',{'content_hash':digest,'model':backend.identity,'start_seconds':first,'end_seconds':last,'text':segment['text'].strip(),'language':value['language'],'metrics_json':json.dumps({k:segment[k] for k in ('avg_logprob','no_speech_prob')}),'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset'],'start_seconds':first,'end_seconds':last,'chunk_start':start,'chunk_end':end,'policy':POLICY,'validation_policy':value['validation_policy']}),'extractor':'faster-whisper-small','extractor_version':POLICY,'confidence':min(1,math.exp(min(0,segment['avg_logprob']))),'derived_at':now(),'tier':'personal'})
            conn.execute('UPDATE transcript_chunks SET status=?,segments=?,error=?,derived_at=? WHERE content_hash=? AND model=? AND policy=? AND start=?',(value['status'] if value else 'error',len(value['segments']) if value else 0,error,now(),*key))
            if value:conn.execute('INSERT OR REPLACE INTO transcript_chunk_validation VALUES(?,?,?,?,?,?)',(*key,value['rejected_segments'],value['validation_policy']))
            publish_status(conn,backend.identity,digest)
        processed+=1
        print(json.dumps({'phase':'transcript_chunks','chunks_this_run':processed,'status':value['status'] if value else 'error'}),flush=True)
    active={r[0] for r in conn.execute("SELECT DISTINCT content_hash FROM transcript_chunks WHERE model=? AND policy=? AND status='pending'",(backend.identity,POLICY))}
    return {'chunks_this_run':processed,'remaining_chunk_videos':len(active & set(candidates))}
