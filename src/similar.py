"""Perceptual hashes for near-duplicate and burst stacking.

Each verified photo gets a 64-bit difference hash, stored with the kit's provenance
columns. Stacking itself is a pure function over hashes and capture times so the
grid can propose stacks now and recompute them for free after every correction.
Proposals never hide anything permanently: a stack is a view, and the owner's
confirmed stacks and "keep separate" verdicts live in the corrections log.
"""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import zipfile

from .imports import database, now
from .kit import create_fact_table, insert_fact
from .vision import read_image

VERSION='dhash-1'
NEAR=6          # hamming distance treated as the same picture at any time on the same day
BURST=14        # looser distance allowed when shots are seconds apart
BURST_SECONDS=90


def dhash(image):
    """64-bit difference hash of a PIL image as 16 hex characters."""
    from PIL import Image
    small=image.convert('L').resize((9,8),Image.Resampling.LANCZOS)
    pixels=list(small.getdata());bits=0
    for row in range(8):
        for col in range(8):
            bits=(bits<<1)|(pixels[row*9+col]<pixels[row*9+col+1])
    return format(bits,'016x')


def hamming(a,b):return bin(int(a,16)^int(b,16)).count('1')


def _reader(row):return read_image(row,max_side=320)


def index(import_database,output,seconds=600,limit=1000,reader=_reader,hasher=dhash):
    source=Path(import_database).resolve(strict=True)
    if Path(output).resolve()==source:raise ValueError('Hash store must be separate')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
        candidates=defaultdict(list)
        for row in conn.execute("SELECT * FROM occurrences WHERE present=1 AND kind='photo' AND content_hash IS NOT NULL ORDER BY member!='',source,offset"):
            candidates[row['content_hash']].append(dict(row))
    start=time.monotonic();processed=0
    with database(output,roots) as conn:
        create_fact_table(conn,'image_hashes',{'content_hash':'TEXT NOT NULL','model':'TEXT NOT NULL','hash_hex':'TEXT NOT NULL','width':'INTEGER','height':'INTEGER'})
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS image_hashes_identity ON image_hashes(content_hash,model)')
        conn.execute('CREATE TABLE IF NOT EXISTS similar_work(content_hash TEXT,model TEXT,status TEXT,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model))')
        done={r[0] for r in conn.execute('SELECT content_hash FROM similar_work WHERE model=?',(VERSION,))}
        for digest,rows in candidates.items():
            if processed>=limit or time.monotonic()-start>=seconds:break
            if digest in done:continue
            fact=None;error='NoReadableSource'
            for row in rows:
                try:
                    image=reader(row)
                    try:
                        fact={'content_hash':digest,'model':VERSION,'hash_hex':hasher(image),'width':getattr(image,'width',None),'height':getattr(image,'height',None),
                              'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset']}),
                              'extractor':'pillow-dhash','extractor_version':VERSION,'confidence':1.0,'derived_at':now(),'tier':'personal'}
                    finally:
                        if hasattr(image,'close'):image.close()
                    error=None;break
                except (OSError,ValueError,RuntimeError,StopIteration,zipfile.BadZipFile) as exc:error=type(exc).__name__
                except Exception as exc:error=type(exc).__name__
            with conn:
                if fact:insert_fact(conn,'image_hashes',fact)
                conn.execute('INSERT INTO similar_work VALUES(?,?,?,?,?)',(digest,VERSION,'error' if error else 'complete',error,now()))
            done.add(digest);processed+=1
            if processed%200==0:print(json.dumps({'phase':'image-hashes',**summary(conn),'processed_this_run':processed}),flush=True)
        return {**summary(conn),'processed_this_run':processed,'candidates':len(candidates),'remaining':len(set(candidates)-done),'model':VERSION}


def summary(conn):
    row=conn.execute("SELECT count(*),sum(status='error') FROM similar_work WHERE model=?",(VERSION,)).fetchone()
    return {'indexed':row[0] or 0,'errors':row[1] or 0}


def stack_id(contents):return hashlib.sha256('\n'.join(sorted(contents)).encode()).hexdigest()[:32]


def _seconds(value):
    try:return datetime.fromisoformat(value[:19]).timestamp()
    except (TypeError,ValueError):return None


def propose(items,hashes,near=NEAR,burst=BURST,burst_seconds=BURST_SECONDS):
    """Group items sharing a day (or all undated) whose hashes sit within `near`, plus
same-day shots within `burst_seconds` inside the looser `burst` distance.

`items` are dicts with content_hash, day, date, width, height. `hashes` maps content
hash to (hash_hex, width, height). Returns stacks sorted by day: {'id','contents','top'}.
Near candidates come from identical 8-bit bands, exhaustive up to 7 differing bits; burst
candidates come from a sliding time window, so neither pass compares every pair."""
    rows=[]
    for item in items:
        entry=hashes.get(item['content_hash'])
        if not entry:continue
        stamp=_seconds((item.get('date') or {}).get('value')) if item.get('day') else None
        rows.append({'hash':item['content_hash'],'bits':entry[0],'day':item.get('day') or '','at':stamp,'pixels':(item.get('width') or entry[1] or 0)*(item.get('height') or entry[2] or 0)})
    parent={r['hash']:r['hash'] for r in rows}
    def find(x):
        while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
        return x
    def join(x,y):parent[find(x['hash'])]=find(y['hash'])
    # Near matches: identical 8-bit bands find every pair within 7 bits, which covers `near`.
    bands=defaultdict(list)
    for i,r in enumerate(rows):
        for band in range(8):bands[(r['day'],band,r['bits'][band*2:band*2+2])].append(i)
    seen=set()
    for members in bands.values():
        if len(members)<2:continue
        for a in range(len(members)):
            for b in range(a+1,len(members)):
                pair=(members[a],members[b])
                if pair in seen:continue
                seen.add(pair);x=rows[pair[0]];y=rows[pair[1]]
                if hamming(x['bits'],y['bits'])<=near:join(x,y)
    # Bursts: a time window over dated shots, compared with the looser limit.
    timed=sorted((r for r in rows if r['at'] is not None),key=lambda r:r['at'])
    for i,x in enumerate(timed):
        for y in timed[i+1:]:
            if y['at']-x['at']>burst_seconds:break
            if x['day']==y['day'] and hamming(x['bits'],y['bits'])<=burst:join(x,y)
    groups=defaultdict(list)
    for r in rows:groups[find(r['hash'])].append(r)
    stacks=[]
    for members in groups.values():
        if len(members)<2:continue
        top=sorted(members,key=lambda r:(-r['pixels'],r['at'] if r['at'] is not None else float('inf'),r['hash']))[0]
        contents=sorted(r['hash'] for r in members)
        stacks.append({'id':stack_id(contents),'contents':contents,'top':top['hash'],'day':members[0]['day'] or None})
    stacks.sort(key=lambda s:(s['day'] is None,s['day'] or '',s['id']))
    return stacks


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--imports',type=Path,required=True);p.add_argument('--database',type=Path,required=True)
    p.add_argument('--seconds',type=int,default=600);p.add_argument('--limit',type=int,default=1000)
    a=p.parse_args()
    print(json.dumps(index(a.imports,a.database,a.seconds,a.limit)))


if __name__=='__main__':main()
