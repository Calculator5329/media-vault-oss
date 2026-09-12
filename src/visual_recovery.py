"""Finite visual embedding variants for explicitly recovered image inputs."""
from contextlib import closing
import hashlib,json,sqlite3,time
from .imports import now
from .kit import insert_fact
from .image_recovery import load


def variant(base,recipe):
    return 'siglip-recovered:'+hashlib.sha256(json.dumps({'base_model':base,'recovery_recipe':recipe,'pixels':'already-oriented RGB','max_side':1600,'policy':'visual-recovery-1'},sort_keys=True).encode()).hexdigest()


def facts(conn,base):
    """Current-base recovered fallbacks; original successful facts win."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='visual_recovery_work'").fetchone():return {}
    cursor=conn.execute('''SELECT f.*,r.base_model FROM visual_facts f JOIN visual_recovery_work r
      ON f.content_hash=r.content_hash AND f.model=r.model
      WHERE r.base_model=? AND r.status='complete' AND NOT EXISTS(
      SELECT 1 FROM visual_facts original WHERE original.content_hash=f.content_hash AND original.model=?)
      ORDER BY r.derived_at DESC''',(base,base))
    columns=[r[0] for r in cursor.description];result={}
    for values in cursor:
        row=dict(zip(columns,values))
        result.setdefault(row['content_hash'],row)
    return result


def run(conn,backend,candidates,recovery_database,deadline,limit):
    conn.execute('CREATE TABLE IF NOT EXISTS visual_recovery_work(content_hash TEXT,base_model TEXT,model TEXT,recipe TEXT,artifact_sha256 TEXT,status TEXT,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model))')
    with conn:conn.execute("UPDATE visual_recovery_work SET status='error',error='InterruptedAttempt',derived_at=? WHERE base_model=? AND status='running'",(now(),backend.identity))
    if not recovery_database.is_file():return {'processed':0,'remaining':0}
    with closing(sqlite3.connect(recovery_database.as_uri()+'?mode=ro',uri=True)) as recovery:
        rows=recovery.execute("SELECT f.content_hash,f.recipe,f.artifact_sha256 FROM image_recovery_facts f JOIN image_recovery_work w ON f.content_hash=w.content_hash AND f.recipe=w.recipe WHERE w.status='complete' ORDER BY f.derived_at DESC").fetchall()
    eligible={r[0] for r in conn.execute("SELECT content_hash FROM visual_errors WHERE model=? AND error IN ('OSError','UnidentifiedImageError')",(backend.identity,))}
    successful={r[0] for r in conn.execute('SELECT content_hash FROM visual_facts WHERE model=?',(backend.identity,))}
    seen=set();pending=[]
    for digest,recipe,artifact in rows:
        if digest in seen or digest not in candidates or digest not in eligible or digest in successful:continue
        seen.add(digest);model=variant(backend.identity,recipe)
        if not conn.execute('SELECT 1 FROM visual_recovery_work WHERE content_hash=? AND model=?',(digest,model)).fetchone():pending.append((digest,recipe,artifact,model))
    processed=0;dimension=None
    for digest,recipe,artifact,model in pending:
        if processed>=limit or time.monotonic()>=deadline:break
        with conn:conn.execute('INSERT INTO visual_recovery_work VALUES(?,?,?,?,?,?,?,?)',(digest,backend.identity,model,recipe,artifact,'running',None,now()))
        image=None
        try:
            loaded=load(recovery_database,digest)
            if loaded is None:raise ValueError('Recovery no longer available')
            image,recovery=loaded
            if recovery['recipe']!=recipe or recovery['artifact_sha256']!=artifact:raise ValueError('Recovery identity changed')
            from .vision import unit
            if dimension is None:dimension=len(unit(backend.text('a photo')))
            image.thumbnail((1600,1600));vector=unit(backend.image(image))
            if len(vector)!=dimension:raise ValueError('Recovered embedding dimension mismatch')
            with conn:
                span=json.loads(recovery['source_span']);span.update(base_model=backend.identity,recovery_recipe=recipe,artifact_sha256=artifact,max_side=1600,input_variant=model,embedding_dimension=dimension,meaning='Embedding of verified recovered pixels; same base encoder space, not an object-label verdict')
                insert_fact(conn,'visual_facts',{'content_hash':digest,'model':model,'vector_json':json.dumps(vector),'source_path':recovery['source_path'],'source_span':json.dumps(span),'extractor':'siglip2-image-embedding','extractor_version':'visual-recovery-1','confidence':1.,'derived_at':now(),'tier':'personal'})
                conn.execute("UPDATE visual_recovery_work SET status='complete',error=NULL,derived_at=? WHERE content_hash=? AND model=?",(now(),digest,model))
        except Exception as exc:
            with conn:conn.execute("UPDATE visual_recovery_work SET status='error',error=?,derived_at=? WHERE content_hash=? AND model=?",(type(exc).__name__,now(),digest,model))
        finally:
            if image is not None:image.close()
        processed+=1
    return {'processed':processed,'remaining':len(pending)-processed}
