"""Local visual retrieval over sampled, source-linked video moments."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from .imports import database,now
from .kit import create_fact_table,insert_fact
from .vision import unit


def observations(path):
    path=Path(path).resolve()
    if not path.is_file():return [],[]
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
        current=conn.execute("SELECT value FROM settings WHERE key='frames_current'").fetchone()
        compatible=conn.execute("SELECT value FROM settings WHERE key='frames_compatible'").fetchone()
        models=json.loads(compatible[0]) if compatible else ([current[0]] if current else [])
        rows=[dict(r) for r in conn.execute('SELECT * FROM frame_facts WHERE sampler IN ('+','.join('?' for _ in models)+')',models)] if models else []
        from .frame_gap_work import observations as gap_observations
        return rows+gap_observations(conn),roots


def sample_path(directory,row):
    if not re.fullmatch(r'[a-f0-9]{64}\.[a-f0-9]{32}\.jpg',row['filename']):raise ValueError('Invalid sample filename')
    path=Path(directory)/'video-samples'/row['filename']
    with path.open('rb') as stream:
        if path.stat().st_size>5*1024**2 or hashlib.file_digest(stream,'sha256').hexdigest()!=row['image_hash']:raise ValueError('Video sample changed')
    return path


def index(import_database,output,encoder,seconds=300,limit=1000):
    from PIL import Image
    source=Path(import_database).resolve(strict=True);output=Path(output).resolve();frame_database=source.parent/'frames.db'
    if output in (source,frame_database):raise ValueError('Embeddings need a separate derived store')
    rows,roots=observations(frame_database)
    if not rows:return {'remaining':0,'indexed':0,'processed_this_run':0,'waiting_for_samples':True}
    processed=0;deadline=time.monotonic()+seconds
    with database(output,roots) as conn:
        create_fact_table(conn,'visual_facts',{'content_hash':'TEXT NOT NULL','model':'TEXT NOT NULL','vector_json':'TEXT NOT NULL'})
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS scene_identity ON visual_facts(content_hash,model)')
        conn.execute('CREATE TABLE IF NOT EXISTS visual_errors(content_hash TEXT,model TEXT,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model))')
        done={r[0] for r in conn.execute('SELECT content_hash FROM visual_facts WHERE model=?',(encoder.identity,))};failed={r[0] for r in conn.execute('SELECT content_hash FROM visual_errors WHERE model=?',(encoder.identity,))}
        for row in rows:
            if processed>=limit or time.monotonic()>deadline:break
            key=row['frame_id']
            if key in done or key in failed:continue
            try:
                path=sample_path(source.parent,row)
                with Image.open(path) as im:
                    if im.width*im.height>640*640:raise ValueError('Unexpected frame dimensions')
                    rgb=im.convert('RGB')
                    try:vector=unit(encoder.image(rgb))
                    finally:rgb.close()
                with conn:insert_fact(conn,'visual_facts',{'content_hash':key,'model':encoder.identity,'vector_json':json.dumps(vector),'source_path':row['source_path'],'source_span':json.dumps({'video_content_hash':row['content_hash'],'frame_id':key,'sample_sha256':row['image_hash'],'sampler':row['sampler'],'original':json.loads(row['source_span'])}),'extractor':'siglip2-video-sample-embedding','extractor_version':'1','confidence':1.0,'derived_at':now(),'tier':'personal'})
                done.add(key)
            except (OSError,ValueError,RuntimeError) as exc:
                with conn:conn.execute('INSERT INTO visual_errors VALUES(?,?,?,?)',(key,encoder.identity,type(exc).__name__,now()))
                failed.add(key)
            processed+=1
        return {'processed_this_run':processed,'indexed':len(done),'errors':len(failed),'remaining':len({r['frame_id'] for r in rows}-done-failed),'model':encoder.identity}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--imports',type=Path,required=True);p.add_argument('--database',type=Path,required=True);p.add_argument('--model',type=Path,required=True);p.add_argument('--seconds',type=int,default=300);p.add_argument('--limit',type=int,default=1000);a=p.parse_args()
    if min(a.seconds,a.limit)<1:p.error('Work limits must be positive')
    from .vision import Siglip
    print(json.dumps(index(a.imports,a.database,Siglip(a.model),a.seconds,a.limit)),flush=True)

if __name__=='__main__':main()
