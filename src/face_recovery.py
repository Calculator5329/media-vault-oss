"""Finite, attributable face observations from verified recovered pixels."""
from contextlib import closing
import hashlib,json,sqlite3,time
from .imports import now
from .kit import insert_fact
from .image_recovery import load


def variant(base,recipe):
    return 'sface-recovered:'+hashlib.sha256(json.dumps({'base_model':base,'recovery_recipe':recipe,'pixels':'already-oriented RGB','max_side':1600,'policy':'face-recovery-1'},sort_keys=True).encode()).hexdigest()


def completed(conn,base):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='face_recovery_work'").fetchone():return {}
    rows=conn.execute('''SELECT r.content_hash,r.model,r.face_count FROM face_recovery_work r
      WHERE r.base_model=? AND r.status='complete' AND NOT EXISTS(
      SELECT 1 FROM face_work original WHERE original.content_hash=r.content_hash
      AND original.model=? AND original.status='complete') ORDER BY r.derived_at DESC''',(base,base))
    result={}
    for digest,model,count in rows:result.setdefault(digest,{'model':model,'face_count':count})
    return result


def observations(conn,base):
    selected=completed(conn,base);result=[]
    if not selected:return result
    rows=conn.execute('''SELECT f.* FROM face_observations f JOIN face_recovery_work r
      ON f.content_hash=r.content_hash AND f.model=r.model WHERE r.base_model=? AND r.status='complete' ''',(base,))
    columns=[r[0] for r in rows.description]
    for row in rows:
        fact=dict(zip(columns,row))
        if selected.get(fact['content_hash'],{}).get('model')==fact['model']:result.append(fact)
    return result


def run(conn,backend,candidates,recovery_database,deadline,limit):
    conn.execute('CREATE TABLE IF NOT EXISTS face_recovery_work(content_hash TEXT,base_model TEXT,model TEXT,recipe TEXT,artifact_sha256 TEXT,status TEXT,face_count INTEGER,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model))')
    with conn:conn.execute("UPDATE face_recovery_work SET status='error',error='InterruptedAttempt',derived_at=? WHERE base_model=? AND status='running'",(now(),backend.identity))
    if not recovery_database.is_file():return {'processed':0,'remaining':0}
    with closing(sqlite3.connect(recovery_database.as_uri()+'?mode=ro',uri=True)) as recovery:
        rows=recovery.execute("SELECT f.content_hash,f.recipe,f.artifact_sha256 FROM image_recovery_facts f JOIN image_recovery_work w ON f.content_hash=w.content_hash AND f.recipe=w.recipe WHERE w.status='complete' ORDER BY f.derived_at DESC").fetchall()
    eligible={r[0] for r in conn.execute("SELECT content_hash FROM face_work WHERE model=? AND status='error' AND error IN ('OSError','UnidentifiedImageError')",(backend.identity,))}
    seen=set();pending=[]
    for digest,recipe,artifact in rows:
        if digest in seen or digest not in candidates or digest not in eligible:continue
        seen.add(digest);model=variant(backend.identity,recipe)
        if not conn.execute('SELECT 1 FROM face_recovery_work WHERE content_hash=? AND model=?',(digest,model)).fetchone():pending.append((digest,recipe,artifact,model))
    processed=0
    for digest,recipe,artifact,model in pending:
        if processed>=limit or time.monotonic()>=deadline:break
        with conn:conn.execute('INSERT INTO face_recovery_work VALUES(?,?,?,?,?,?,?,?,?)',(digest,backend.identity,model,recipe,artifact,'running',0,None,now()))
        image=None
        try:
            loaded=load(recovery_database,digest)
            if loaded is None:raise ValueError('Recovery no longer available')
            image,recovery=loaded
            if recovery['recipe']!=recipe or recovery['artifact_sha256']!=artifact:raise ValueError('Recovery identity changed')
            from .vision import unit
            image.thumbnail((1600,1600));facts=[]
            for face in backend.detect(image):
                box=face['box'];vector=unit(face['vector']);score=float(face['score'])
                if len(vector)!=backend.embedding_dimension:raise ValueError('Recovered face dimension mismatch')
                if len(box)!=4 or not all(isinstance(v,(float,int)) and 0<=v<=1 for v in box) or not (box[0]<box[2] and box[1]<box[3]) or not 0<=score<=1:raise ValueError('Invalid face observation')
                geometry=json.dumps(box,separators=(',',':'));identity=hashlib.sha256((digest+model+artifact+geometry).encode()).hexdigest()
                span=json.loads(recovery['source_span']);span.update(base_model=backend.identity,recovery_recipe=recipe,artifact_sha256=artifact,max_side=1600,input_variant=model,box=box,meaning='Face suggestion from verified recovered pixels; identity requires owner confirmation')
                facts.append({'face_id':identity,'content_hash':digest,'model':model,'box_json':geometry,'vector_json':json.dumps(vector),'source_path':recovery['source_path'],'source_span':json.dumps(span),'extractor':'opencv-yunet-sface','extractor_version':'face-recovery-1','confidence':score,'derived_at':now(),'tier':'personal'})
            with conn:
                for fact in facts:insert_fact(conn,'face_observations',fact)
                conn.execute("UPDATE face_recovery_work SET status='complete',face_count=?,error=NULL,derived_at=? WHERE content_hash=? AND model=?",(len(facts),now(),digest,model))
        except Exception as exc:
            with conn:conn.execute("UPDATE face_recovery_work SET status='error',error=?,derived_at=? WHERE content_hash=? AND model=?",(type(exc).__name__,now(),digest,model))
        finally:
            if image is not None:image.close()
        processed+=1
    return {'processed':processed,'remaining':len(pending)-processed}
