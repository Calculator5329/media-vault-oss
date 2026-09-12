"""Offline image text with source identity, word boxes and recognition scores."""
import argparse
from collections import defaultdict
from contextlib import closing
import csv
import hashlib
import io
import json
import os
import shutil
from pathlib import Path
import sqlite3
import subprocess
import time
import zipfile

from .imports import database,now
from .kit import create_fact_table,insert_fact
from .vision import read_image


RETRY_POLICY='timeout-90s-v1'


def tessdata_english():
    """Locate ``eng.traineddata``: ``TESSDATA_PREFIX`` first, then the usual package and installer dirs."""
    candidates=[]
    prefix=os.environ.get('TESSDATA_PREFIX')
    if prefix:candidates.append(Path(prefix))
    binary=shutil.which('tesseract')
    if binary:candidates.append(Path(binary).resolve().parent/'tessdata')
    candidates+=[Path('/usr/share/tessdata'),Path('/usr/share/tesseract-ocr/5/tessdata'),Path('/usr/share/tesseract-ocr/4.00/tessdata'),
                 Path('/usr/local/share/tessdata'),Path('/opt/homebrew/share/tessdata'),Path(r'C:/Program Files/Tesseract-OCR/tessdata')]
    for directory in candidates:
        data=directory/'eng.traineddata'
        if data.is_file():return data
    raise FileNotFoundError('Tesseract English data (eng.traineddata) not found; install tesseract-data-eng or set TESSDATA_PREFIX')


class Tesseract:
    def __init__(self):
        self.version=subprocess.run(['tesseract','--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
        data=tessdata_english()
        self.identity='tesseract:'+hashlib.sha256(json.dumps({'version':self.version,'english_model':hashlib.sha256(data.read_bytes()).hexdigest(),'psm':11,'max_side':3200,'min_score':45,'extractor':'ocr-1'},sort_keys=True).encode()).hexdigest()

    def retry(self,image):
        return self.extract(image,timeout=90)

    def extract(self,image,timeout=45):
        stream=io.BytesIO();image.save(stream,format='PNG')
        process=subprocess.run(['tesseract','stdin','stdout','-l','eng','--psm','11','tsv'],input=stream.getvalue(),capture_output=True,timeout=timeout,env={**os.environ,'OMP_THREAD_LIMIT':'2'})
        if process.returncode:raise RuntimeError('OCR process failed')
        return parse_tsv(process.stdout.decode('utf-8'),image.width,image.height)


def parse_tsv(raw,width,height):
    words=[]
    for row in csv.DictReader(io.StringIO(raw),delimiter='\t',quoting=csv.QUOTE_NONE):
        if row.get('level')!='5' or not row.get('text','').strip():continue
        score=float(row['conf']);x=int(row['left']);y=int(row['top']);w=int(row['width']);h=int(row['height'])
        if not 0<=score<=100 or min(x,y,w,h)<0 or x+w>width or y+h>height:raise ValueError('Invalid OCR geometry')
        words.append({'text':row['text'],'score':score/100,'box':[x/width,y/height,(x+w)/width,(y+h)/height]})
    text=' '.join(word['text'] for word in words if word['score']>=.45)
    return {'text':text,'words':words,'language':'eng','minimum_search_score':.45}


def index(import_database,output,backend,seconds=600,limit=1000,reader=None):
    source=Path(import_database).resolve(strict=True)
    if source==Path(output).resolve():raise ValueError('OCR store must be separate')
    if reader is None:reader=lambda row:read_image(row,max_side=3200)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row;roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0]);candidates=defaultdict(list)
        for row in conn.execute("SELECT * FROM occurrences WHERE present=1 AND kind='photo' AND content_hash IS NOT NULL ORDER BY member!='',source,offset"):
            candidates[row['content_hash']].append(dict(row))
    start=time.monotonic();processed=0
    with database(output,roots) as conn:
        create_fact_table(conn,'ocr_facts',{'content_hash':'TEXT NOT NULL','model':'TEXT NOT NULL','text':'TEXT NOT NULL','words_json':'TEXT NOT NULL'})
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS ocr_identity ON ocr_facts(content_hash,model)')
        conn.execute('CREATE TABLE IF NOT EXISTS ocr_errors(content_hash TEXT,model TEXT,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model))')
        conn.execute('CREATE TABLE IF NOT EXISTS ocr_retry_work(content_hash TEXT,model TEXT,policy TEXT,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model,policy))')
        done={r[0] for r in conn.execute('SELECT content_hash FROM ocr_facts WHERE model=?',(backend.identity,))}
        failed={r[0] for r in conn.execute('SELECT content_hash FROM ocr_errors WHERE model=?',(backend.identity,))}
        retried={r[0] for r in conn.execute('SELECT content_hash FROM ocr_retry_work WHERE model=? AND policy=?',(backend.identity,RETRY_POLICY))}
        eligible={r[0] for r in conn.execute("SELECT content_hash FROM ocr_errors WHERE model=? AND error='TimeoutExpired'",(backend.identity,))}-done-retried if callable(getattr(backend,'retry',None)) else set()
        eligible.intersection_update(candidates)
        for digest,rows in sorted(candidates.items(),key=lambda pair:pair[0] not in eligible):
            if processed>=limit or time.monotonic()-start>=seconds:break
            retry=digest in eligible
            if digest in done or (digest in failed and not retry):continue
            image=None;error='NoReadableSource';result=None
            for row in rows:
                try:image=reader(row);break
                except (OSError,ValueError,RuntimeError,StopIteration,zipfile.BadZipFile) as exc:error=type(exc).__name__
            if image is not None:
                try:result=backend.retry(image) if retry else backend.extract(image);error=None
                except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:error=type(exc).__name__
                finally:
                    if hasattr(image,'close'):image.close()
            with conn:
                if error:
                    if not retry:conn.execute('INSERT INTO ocr_errors VALUES(?,?,?,?)',(digest,backend.identity,error,now()))
                    failed.add(digest)
                else:
                    scores=[w['score'] for w in result['words'] if w['score']>=.45]
                    insert_fact(conn,'ocr_facts',{'content_hash':digest,'model':backend.identity,'text':result['text'],'words_json':json.dumps(result),
                        'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset'],**({'retry_policy':RETRY_POLICY,'timeout_seconds':90} if retry else {})}),'extractor':'tesseract-english','extractor_version':'ocr-1','confidence':sum(scores)/len(scores) if scores else 0,'derived_at':now(),'tier':'personal'})
                    done.add(digest)
                if retry:
                    conn.execute('INSERT INTO ocr_retry_work VALUES(?,?,?,?,?)',(digest,backend.identity,RETRY_POLICY,error,now()))
                    eligible.discard(digest)
            processed+=1
            if processed%100==0:print(json.dumps({'phase':'ocr','processed_this_run':processed,'indexed':len(done),'errors':len(failed-done)}),flush=True)
        with conn:conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('ocr_current',?)",(backend.identity,))
        return {'processed_this_run':processed,'indexed':len(done),'errors':len(failed-done),'remaining':len(set(candidates)-done-failed)+len(eligible),'model':backend.identity}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--imports',type=Path,required=True);p.add_argument('--database',type=Path,required=True);p.add_argument('--seconds',type=int,default=600);p.add_argument('--limit',type=int,default=1000);a=p.parse_args()
    if a.seconds<1 or a.limit<1:p.error('Work bounds must be positive')
    print(json.dumps(index(a.imports,a.database,Tesseract(),a.seconds,a.limit)),flush=True)


if __name__=='__main__':main()
