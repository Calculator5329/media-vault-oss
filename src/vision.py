"""Offline image/text similarity indexing over verified content identities.

Run with the isolated vision Python runtime. Models must already exist locally;
no download or inference service is invoked here. Scores rank visual similarity,
not certainty that an object is present. People identification is separate.
"""
import argparse
from contextlib import closing
from collections import defaultdict
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import zipfile

from .imports import database, _source_stat, now
from .kit import create_fact_table, insert_fact

VERSION = 'vision-1'
MAX_IMAGE_BYTES = 128 * 1024 * 1024


def unit(vector):
    values = [float(v) for v in vector]
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError('Invalid embedding')
    norm = math.sqrt(sum(v*v for v in values))
    if norm == 0:
        raise ValueError('Zero embedding')
    return [v/norm for v in values]


class Siglip:
    def __init__(self, path):
        os.environ['HF_HUB_OFFLINE']='1'
        os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
        os.environ['TRANSFORMERS_OFFLINE']='1'
        import torch
        from transformers import AutoModel, AutoProcessor
        self.torch = torch
        path = Path(path).resolve(strict=True)
        # Bind vector compatibility to the exact locally loaded weights/config.
        digest=hashlib.sha256()
        files=sorted(p for p in path.iterdir() if p.suffix in ('.json','.safetensors','.model','.txt'))
        if not any(p.suffix=='.safetensors' for p in files):
            raise ValueError('Local safetensors weights required')
        for file in files:
            digest.update(file.name.encode())
            with file.open('rb') as stream:
                while chunk:=stream.read(1024*1024):
                    digest.update(chunk)
        self.identity='siglip2:'+digest.hexdigest()
        self.device='cuda' if torch.cuda.is_available() else 'cpu'
        self.processor=AutoProcessor.from_pretrained(path,local_files_only=True,trust_remote_code=False)
        self.model=AutoModel.from_pretrained(path,local_files_only=True,trust_remote_code=False,use_safetensors=True).eval().to(self.device)

    def image(self,image):
        inputs=self.processor(images=image,return_tensors='pt').to(self.device)
        with self.torch.inference_mode():
            result=self.model.get_image_features(**inputs).pooler_output[0]
        return unit(result.cpu().tolist())

    def text(self,text):
        inputs=self.processor(text=[text],padding='max_length',truncation=True,return_tensors='pt').to(self.device)
        if inputs['input_ids'].max()>=self.model.config.text_config.vocab_size:
            raise ValueError('Tokenizer/model vocabulary mismatch')
        with self.torch.inference_mode():
            result=self.model.get_text_features(**inputs).pooler_output[0]
        return unit(result.cpu().tolist())


def read_image(row,max_side=1600,fast=False):
    """Decode a verified source image. fast=True lets JPEG decode at a reduced DCT scale
(no smaller than twice max_side) which is several times quicker for previews."""
    from PIL import Image,ImageOps
    before=_source_stat(row)
    if row['size']>MAX_IMAGE_BYTES:
        raise ValueError('Image exceeds bounded decode size')
    if row['member']:
        with zipfile.ZipFile(row['source']) as archive:
            info=next(i for i in archive.infolist() if i.header_offset==row['offset'])
            if (info.filename,info.file_size,info.CRC)!=(row['member'],row['size'],row['crc']):
                raise ValueError('ZIP member changed')
            with archive.open(info) as stream:
                raw=stream.read(MAX_IMAGE_BYTES+1)
    else:
        with Path(row['source']).open('rb') as stream:
            raw=stream.read(MAX_IMAGE_BYTES+1)
    after=_source_stat(row)
    if before.st_ino!=after.st_ino or len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['content_hash']:
        raise ValueError('Source bytes do not match content identity')
    with Image.open(io.BytesIO(raw)) as image:
        if image.width*image.height>80_000_000:
            raise ValueError('Image exceeds bounded decode dimensions')
        if fast and image.format=='JPEG':image.draft('RGB',(max_side*2,max_side*2))
        result=ImageOps.exif_transpose(image).convert('RGB')
    result.thumbnail((max_side,max_side))
    return result


def index(import_database, output, encoder, seconds=600, limit=1000, reader=read_image):
    import_database=Path(import_database).resolve(strict=True)
    if Path(output).resolve()==import_database:
        raise ValueError('Vision store must be separate from import state')
    with closing(sqlite3.connect(import_database.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
        candidates=defaultdict(list)
        for row in conn.execute("SELECT * FROM occurrences WHERE present=1 AND kind='photo' AND content_hash IS NOT NULL ORDER BY member!='',source,offset"):
            candidates[row['content_hash']].append(dict(row))
    deadline=time.monotonic()+seconds
    processed=0
    with database(output,roots) as conn:
        create_fact_table(conn,'visual_facts',{'content_hash':'TEXT NOT NULL','model':'TEXT NOT NULL','vector_json':'TEXT NOT NULL'})
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS visual_identity ON visual_facts(content_hash,model)')
        conn.execute('CREATE TABLE IF NOT EXISTS visual_errors(content_hash TEXT,model TEXT,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model))')
        done={r[0] for r in conn.execute('SELECT content_hash FROM visual_facts WHERE model=?',(encoder.identity,))}
        failed={r[0] for r in conn.execute('SELECT content_hash FROM visual_errors WHERE model=?',(encoder.identity,))}
        for content_hash,rows in candidates.items():
            if processed>=limit or time.monotonic()>=deadline:
                break
            if content_hash in done or content_hash in failed:
                continue
            image=None
            error='NoReadableSource'
            for row in rows:
                try:
                    image=reader(row)
                    break
                except (OSError,ValueError,RuntimeError,StopIteration,zipfile.BadZipFile) as exc:
                    error=type(exc).__name__
            if image is None:
                with conn:
                    conn.execute('INSERT INTO visual_errors VALUES(?,?,?,?)',(content_hash,encoder.identity,error,now()))
                failed.add(content_hash)
                continue
            try:
                vector=unit(encoder.image(image))
            finally:
                if hasattr(image,'close'):
                    image.close()
            with conn:
                insert_fact(conn,'visual_facts',{'content_hash':content_hash,'model':encoder.identity,'vector_json':json.dumps(vector),
                    'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset']}),
                    'extractor':'siglip2-image-embedding','extractor_version':VERSION,'confidence':1.0,
                    'derived_at':now(),'tier':'personal'})
            done.add(content_hash)
            processed+=1
            if processed%100==0:
                print(json.dumps({'phase':'vision-index','processed_this_run':processed,'indexed':len(done),'errors':len(failed)}),flush=True)
        return {'processed_this_run':processed,'indexed':len(done),'errors':len(failed),'verified_photo_candidates':len(candidates),
                'remaining':len(set(candidates)-done-failed),'model':encoder.identity}


def search(database_path,encoder,query,limit=20,allowed=None):
    if not query.strip() or len(query)>300 or not 1<=limit<=200:
        raise ValueError('Use a nonempty query up to 300 characters and limit 1 through 200')
    vector=unit(encoder.text(query))
    ranked=[]
    indexed=0
    with closing(sqlite3.connect(Path(database_path).resolve(strict=True).as_uri()+'?mode=ro',uri=True)) as conn:
        for content_hash,raw in conn.execute('SELECT content_hash,vector_json FROM visual_facts WHERE model=?',(encoder.identity,)):
            indexed+=1
            if allowed is not None and content_hash not in allowed:
                continue
            candidate=json.loads(raw)
            if len(candidate)!=len(vector):
                raise ValueError('Embedding dimension mismatch')
            ranked.append({'content_hash':content_hash,'similarity':sum(a*b for a,b in zip(vector,candidate))})
    ranked.sort(key=lambda r:(-r['similarity'],r['content_hash']))
    return {'results':ranked[:limit],'indexed_contents':indexed,'eligible_contents':len(ranked),'model':encoder.identity,
            'meaning':'Ranked visual similarity, not confirmed object labels or complete recall.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--imports',type=Path,default=Path('.catalog/imports.db'))
    parser.add_argument('--database',type=Path,default=Path('.catalog/vision.db'))
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--limit',type=int,default=1000)
    parser.add_argument('--query')
    args=parser.parse_args()
    if args.seconds<1 or args.limit<1:
        parser.error('Work bounds must be positive')
    encoder=Siglip(args.model)
    result=search(args.database,encoder,args.query,min(args.limit,200)) if args.query else index(args.imports,args.database,encoder,args.seconds,args.limit)
    print(json.dumps(result),flush=True)


class SearchIndex:
    """Keep the local vector matrix in memory between queries.

The writer only appends completed vectors. A new row count refreshes this read
snapshot. NumPy is in the vision runtime; stdlib callers retain the same results.
"""
    def __init__(self,path,encoder,refresh_after=60):
        self.path=Path(path);self.encoder=encoder;self.revision=None
        self.refresh_after=refresh_after;self.checked_at=0
        self.hashes=[];self.matrix=None

    def search(self,query,limit=200,allowed=None):
        if not isinstance(query,str) or not query.strip() or len(query)>300 or not 1<=limit<=200:
            raise ValueError('Use a description of 1 to 300 characters')
        try:import numpy as np
        except ImportError:return search(self.path,self.encoder,query,limit,allowed)
        if self.matrix is None or time.monotonic()-self.checked_at>=self.refresh_after:
            with closing(sqlite3.connect(self.path.resolve(strict=True).as_uri()+'?mode=ro',uri=True)) as conn:
                conn.execute('BEGIN')
                revision=conn.execute('SELECT count(*),max(derived_at) FROM visual_facts WHERE model=?',(self.encoder.identity,)).fetchone()
                if revision!=self.revision:
                    rows=conn.execute('SELECT content_hash,vector_json FROM visual_facts WHERE model=? ORDER BY content_hash',(self.encoder.identity,)).fetchall()
                    hashes=[r[0] for r in rows];matrix=np.asarray([json.loads(r[1]) for r in rows],dtype=np.float32)
                    self.hashes=hashes;self.matrix=matrix;self.revision=revision
            self.checked_at=time.monotonic()
        vector=np.asarray(unit(self.encoder.text(query)),dtype=np.float32)
        indices=np.asarray([i for i,h in enumerate(self.hashes) if allowed is None or h in allowed],dtype=int)
        if len(indices):
            if self.matrix.ndim!=2 or self.matrix.shape[1]!=len(vector):raise ValueError('Embedding dimension mismatch')
            scores=self.matrix[indices]@vector
            ranked=sorted(zip(indices,scores),key=lambda v:(-float(v[1]),self.hashes[v[0]]))[:limit]
        else:ranked=[]
        return {'results':[{'content_hash':self.hashes[i],'similarity':float(score)} for i,score in ranked],
                'indexed_contents':len(self.hashes),'eligible_contents':len(indices),'model':self.encoder.identity,
                'meaning':'Ranked visual similarity, not confirmed object labels or complete recall.'}


if __name__=='__main__':
    main()
