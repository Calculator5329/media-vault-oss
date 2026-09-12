"""Fresh visual fingerprints for direct similar-version candidates, never merging."""
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from .imports import database,now
from .kit import create_fact_table,insert_fact
from .vision import read_image


class Fingerprint:
    def __init__(self):
        import PIL
        contract={'Pillow':PIL.__version__,'reader':'verified RGB EXIF max512','gray':'LANCZOS8x8 mean64','color':'LANCZOS4x4 RGB','version':1}
        self.identity='visual-fingerprint:'+hashlib.sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest()

    def describe(self,image):
        from PIL import Image
        with image.convert('RGB') as rgb:
            with rgb.convert('L') as gray,gray.resize((8,8),Image.Resampling.LANCZOS) as tiny:
                samples=list(tiny.get_flattened_data());average=sum(samples)/64
                bits=sum(1<<i for i,v in enumerate(samples) if v>average)
            with rgb.resize((4,4),Image.Resampling.LANCZOS) as tiny:
                colors=[v/255 for pixel in tiny.get_flattened_data() for v in pixel]
            return {'gray_hash':f'{bits:016x}','gray_range':max(samples)-min(samples),'color_json':json.dumps(colors),'aspect_ratio':rgb.width/rgb.height}


def compare(left,right):
    """Conservative experimental pair test. No chaining, identity or taste verdict."""
    try:
        for row in (left,right):
            if not re.fullmatch('[0-9a-f]{16}',row['gray_hash']) or not 8<=row['gray_range']<=255:return None
        distance=(int(left['gray_hash'],16)^int(right['gray_hash'],16)).bit_count()
        if distance>8:return None
        ratios=[r['aspect_ratio'] for r in (left,right)]
        if any(not math.isfinite(r) or r<=0 for r in ratios) or abs(math.log(ratios[0]/ratios[1]))>.03:return None
        a,b=(json.loads(r['color_json']) for r in (left,right))
        if any(len(v)!=48 or any(not isinstance(x,(int,float)) or not math.isfinite(x) or not 0<=x<=1 for x in v) for v in (a,b)):return None
        color=math.sqrt(sum((x-y)**2 for x,y in zip(a,b))/48)
        return {'hash_distance':distance,'color_rmse':color} if distance<=8 and color<=.08 else None
    except (KeyError,TypeError,ValueError,OverflowError):return None


def index(import_database,output,backend,seconds=300,limit=1000,reader=None):
    if min(seconds,limit)<1:raise ValueError('Work bounds must be positive')
    source=Path(import_database).resolve(strict=True)
    if source==Path(output).resolve():raise ValueError('Fingerprint store must be separate')
    if reader is None:reader=lambda row:read_image(row,max_side=512)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row;roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0]);candidates=defaultdict(list)
        for row in conn.execute("SELECT * FROM occurrences WHERE present=1 AND kind='photo' AND content_hash IS NOT NULL ORDER BY member!='',source,offset"):
            candidates[row['content_hash']].append(dict(row))
    if not all(Path(p).is_dir() for p in roots):raise RuntimeError('Fingerprint sources offline')
    start=time.monotonic();processed=0
    with database(output,roots) as conn:
        create_fact_table(conn,'fingerprints_facts',{'content_hash':'TEXT NOT NULL','model':'TEXT NOT NULL','gray_hash':'TEXT NOT NULL','gray_range':'INTEGER NOT NULL','color_json':'TEXT NOT NULL','aspect_ratio':'REAL NOT NULL'})
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS fingerprints_identity ON fingerprints_facts(content_hash,model)')
        conn.execute('CREATE TABLE IF NOT EXISTS fingerprints_work(content_hash TEXT,model TEXT,status TEXT,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model))')
        with conn:
            conn.execute("UPDATE fingerprints_work SET status='error',error='InterruptedAttempt',derived_at=? WHERE status='running'",(now(),))
        done={r[0] for r in conn.execute('SELECT content_hash FROM fingerprints_facts WHERE model=?',(backend.identity,))}
        failed={r[0] for r in conn.execute("SELECT content_hash FROM fingerprints_work WHERE model=? AND status='error'",(backend.identity,))}
        for digest,rows in candidates.items():
            if processed>=limit or time.monotonic()-start>=seconds:break
            if digest in done or digest in failed:continue
            with conn:conn.execute('INSERT INTO fingerprints_work VALUES(?,?,?,?,?)',(digest,backend.identity,'running',None,now()))
            image=None;error='NoReadableSource';value=None
            for row in rows:
                try:image=reader(row);break
                except Exception as exc:error=type(exc).__name__
            if image is not None:
                try:
                    value=backend.describe(image)
                    error=None
                except Exception as exc:error=type(exc).__name__
                finally:
                    if hasattr(image,'close'):image.close()
            with conn:
                if error:failed.add(digest)
                else:
                    insert_fact(conn,'fingerprints_facts',{'content_hash':digest,'model':backend.identity,**value,'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset'],'max_side':512,'meaning':'visual fingerprint for direct candidate review, not content identity'}),'extractor':'visual-fingerprint','extractor_version':'fingerprints-1','confidence':1.0,'derived_at':now(),'tier':'personal'})
                    done.add(digest)
                conn.execute('UPDATE fingerprints_work SET status=?,error=?,derived_at=? WHERE content_hash=? AND model=?',('error' if error else 'complete',error,now(),digest,backend.identity))
            processed+=1
            if processed%100==0:print(json.dumps({'phase':'fingerprints','processed_this_run':processed,'indexed':len(done),'errors':len(failed)}),flush=True)
        with conn:conn.execute("INSERT OR REPLACE INTO settings VALUES('fingerprints_current',?)",(backend.identity,))
        from .fingerprint_recovery import run,facts
        recovery=run(conn,backend,candidates,source.with_name('image-recovery.db'),start+seconds,max(0,limit-processed))
        recovered=facts(conn,backend.identity)
        return {'processed_this_run':processed+recovery['processed'],'indexed':len(done)+len(recovered),'errors':len(failed-done-set(recovered)),'remaining':len(set(candidates)-done-failed)+recovery['remaining'],'candidates':len(candidates),'model':backend.identity,**({'recovered':len(recovered)} if recovered else {})}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--imports',type=Path,required=True);p.add_argument('--database',type=Path,required=True);p.add_argument('--seconds',type=int,default=300);p.add_argument('--limit',type=int,default=1000);a=p.parse_args()
    if min(a.seconds,a.limit)<1:p.error('Work bounds must be positive')
    print(json.dumps(index(a.imports,a.database,Fingerprint(),a.seconds,a.limit)),flush=True)


if __name__=='__main__':main()
