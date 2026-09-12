"""Local technical-quality suggestions, never aesthetic or personal-value verdicts."""
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sqlite3
import time
from .imports import database,now
from .kit import create_fact_table,insert_fact
from .vision import read_image

CHECKPOINT='musiq_koniq_ckpt-e95806b9.pth'
SHA256='e95806b9eae5f3814c410f574ba8e552362bd5bc63d758ed5b97860f5d6185aa'
MAX_SIDE=512


class Musiq:
    def __init__(self,root):
        import torch
        from pyiqa.archs.musiq_arch import MUSIQ
        from torchvision.transforms.functional import to_tensor
        checkpoint=Path(root)/CHECKPOINT
        with checkpoint.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=SHA256:raise ValueError('Quality checkpoint identity mismatch')
        if not torch.cuda.is_available():raise RuntimeError('Quality GPU unavailable')
        versions={name:importlib.metadata.version(name) for name in ('pyiqa','torch','torchvision','Pillow')}
        contract={'checkpoint':SHA256,'max_side':MAX_SIDE,'reader':'verified-rgb-exif-v1','versions':versions,'extractor':'quality-1'}
        self.identity='musiq-koniq:'+hashlib.sha256(json.dumps(contract,sort_keys=True).encode()).hexdigest()
        torch.set_num_threads(2);self.torch=torch;self.to_tensor=to_tensor
        self.model=MUSIQ(pretrained=False)
        self.model.load_state_dict(torch.load(checkpoint,map_location='cpu',weights_only=True),strict=True)
        self.model.eval().to('cuda')

    def score(self,image):
        with self.torch.inference_mode():
            return float(self.model(self.to_tensor(image).unsqueeze(0).to('cuda')).cpu().item())


def index(import_database,output,backend,seconds=300,limit=1000,reader=None):
    if min(seconds,limit)<1:raise ValueError('Work bounds must be positive')
    source=Path(import_database).resolve(strict=True)
    if source==Path(output).resolve():raise ValueError('Quality store must be separate')
    if reader is None:reader=lambda row:read_image(row,max_side=MAX_SIDE)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row;roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0]);candidates=defaultdict(list)
        for row in conn.execute("SELECT * FROM occurrences WHERE present=1 AND kind='photo' AND content_hash IS NOT NULL ORDER BY member!='',source,offset"):
            candidates[row['content_hash']].append(dict(row))
    if not all(Path(p).is_dir() for p in roots):raise RuntimeError('Quality sources offline')
    start=time.monotonic();processed=0
    with database(output,roots) as conn:
        create_fact_table(conn,'quality_facts',{'content_hash':'TEXT NOT NULL','model':'TEXT NOT NULL','score':'REAL NOT NULL'})
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS quality_identity ON quality_facts(content_hash,model)')
        conn.execute('CREATE TABLE IF NOT EXISTS quality_work(content_hash TEXT,model TEXT,status TEXT,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model))')
        with conn:
            conn.execute("UPDATE quality_work SET status='error',error='InterruptedAttempt',derived_at=? WHERE status='running'",(now(),))
        done={r[0] for r in conn.execute('SELECT content_hash FROM quality_facts WHERE model=?',(backend.identity,))}
        failed={r[0] for r in conn.execute("SELECT content_hash FROM quality_work WHERE model=? AND status='error'",(backend.identity,))}
        for digest,rows in candidates.items():
            if processed>=limit or time.monotonic()-start>=seconds:break
            if digest in done or digest in failed:continue
            with conn:conn.execute('INSERT INTO quality_work VALUES(?,?,?,?,?)',(digest,backend.identity,'running',None,now()))
            image=None;error='NoReadableSource';score=None
            for row in rows:
                try:image=reader(row);break
                except Exception as exc:error=type(exc).__name__
            if image is not None:
                try:
                    score=float(backend.score(image))
                    if not math.isfinite(score):raise ValueError('Nonfinite quality score')
                    error=None
                except Exception as exc:error=type(exc).__name__
                finally:
                    if hasattr(image,'close'):image.close()
            with conn:
                if error:failed.add(digest)
                else:
                    insert_fact(conn,'quality_facts',{'content_hash':digest,'model':backend.identity,'score':score,'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset'],'max_side':MAX_SIDE,'meaning':'model technical-quality score, not probability or owner judgment'}),'extractor':'musiq-koniq','extractor_version':'quality-1','confidence':1.0,'derived_at':now(),'tier':'personal'})
                    done.add(digest)
                conn.execute('UPDATE quality_work SET status=?,error=?,derived_at=? WHERE content_hash=? AND model=?',('error' if error else 'complete',error,now(),digest,backend.identity))
            processed+=1
            if processed%100==0:print(json.dumps({'phase':'quality','processed_this_run':processed,'indexed':len(done),'errors':len(failed)}),flush=True)
        with conn:conn.execute("INSERT OR REPLACE INTO settings VALUES('quality_current',?)",(backend.identity,))
        from .quality_recovery import run,facts
        recovery=run(conn,backend,candidates,source.with_name('image-recovery.db'),start+seconds,max(0,limit-processed))
        recovered=facts(conn,backend.identity)
        return {'processed_this_run':processed+recovery['processed'],'indexed':len(done)+len(recovered),'errors':len(failed-done-set(recovered)),'remaining':len(set(candidates)-done-failed)+recovery['remaining'],'candidates':len(candidates),'model':backend.identity,**({'recovered':len(recovered)} if recovered else {})}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--imports',type=Path,required=True);p.add_argument('--database',type=Path,required=True);p.add_argument('--model',type=Path,required=True);p.add_argument('--seconds',type=int,default=300);p.add_argument('--limit',type=int,default=1000);a=p.parse_args()
    if min(a.seconds,a.limit)<1:p.error('Work bounds must be positive')
    print(json.dumps(index(a.imports,a.database,Musiq(a.model),a.seconds,a.limit)),flush=True)


if __name__=='__main__':main()
