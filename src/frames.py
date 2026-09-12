"""Bounded local video samples, retaining source identity and actual timestamps."""
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import time
import uuid
from .imports import database,now
from .kit import create_fact_table,insert_fact
from .transcripts import video_stream


def sampling_interval(duration):
    if not 0<duration<=21600:raise ValueError('Unsupported video duration')
    return max(5,duration/60)


class Sampler:
    def __init__(self):
        import av
        self.av=av
        self.previous_identity='video-samples:'+hashlib.sha256(json.dumps({'av':av.__version__,'version':1,'maximum':60,'minimum_interval':5,'decode':'keyframes'},sort_keys=True).encode()).hexdigest()

        self.identity='video-samples:'+hashlib.sha256(json.dumps({'previous':self.previous_identity,'missing_duration':'decoded-video-extent-v1'},sort_keys=True).encode()).hexdigest()
        duration_identity=self.identity
        self.identity='video-samples:'+hashlib.sha256(json.dumps({'previous':duration_identity,'metadata_errors':'surrogateescape'},sort_keys=True).encode()).hexdigest()
        encoding_identity=self.identity
        self.identity='video-samples:'+hashlib.sha256(json.dumps({'previous':encoding_identity,'maximum_container_duration':21600},sort_keys=True).encode()).hexdigest()
        self.compatible_models=[encoding_identity,duration_identity,self.previous_identity]
        self.retry_errors={'ValueError'}

    def decoded_extent(self,stream):
        """Require EOF and bounded timestamps; never infer duration from frame rate."""
        start=time.monotonic();end=0.;last=-1.;count=0
        with self.av.open(stream,metadata_errors='surrogateescape') as container:
            if not container.streams.video:raise ValueError('No video stream')
            video=container.streams.video[0];video.thread_count=2
            origin=(container.start_time or 0)/self.av.time_base
            for frame in container.decode(video):
                count+=1
                if count>100000 or time.monotonic()-start>60:raise TimeoutError('Duration scan exceeded bounds')
                if frame.width*frame.height>80_000_000 or frame.time is None:raise ValueError('Unbounded video frame')
                timestamp=float(frame.time)-origin
                span=float(frame.duration*frame.time_base) if frame.duration and frame.time_base else 0.
                if not math.isfinite(timestamp) or not math.isfinite(span) or timestamp<last or timestamp<0 or span<0 or timestamp+span>7200:raise ValueError('Invalid decoded timeline')
                last=timestamp;end=max(end,timestamp+span)
        if not count or end<=0:raise ValueError('No measurable video extent')
        return end

    def extract(self,stream,root,digest):
        rows=[];start=time.monotonic();basis='container'
        with self.av.open(stream,metadata_errors='surrogateescape') as probe:
            declared=(probe.duration or 0)/self.av.time_base
        stream.seek(0)
        if declared<=0:
            declared=self.decoded_extent(stream);basis='decoded_video_extent';stream.seek(0)
        with self.av.open(stream,metadata_errors='surrogateescape') as container:
            if not container.streams.video:return []
            duration=declared
            interval=sampling_interval(duration)
            video=container.streams.video[0];video.thread_count=2;video.codec_context.skip_frame='NONKEY'
            origin=(container.start_time or 0)/self.av.time_base
            next_time=0
            for frame in container.decode(video):
                if time.monotonic()-start>120:raise TimeoutError('Frame sampling timed out')
                if frame.time is None:continue
                timestamp=max(0,float(frame.time)-origin)
                if not math.isfinite(timestamp) or timestamp>duration+1:continue
                if timestamp<next_time:continue
                if frame.width*frame.height>80_000_000:raise ValueError('Frame dimensions exceed decode limit')
                if shutil.disk_usage(root).free<12*1024**3:raise RuntimeError('Low Linux cache space')
                key=hashlib.sha256(f'{digest}:{self.identity}:{frame.pts}:{video.time_base}'.encode()).hexdigest()
                filename=key+'.'+uuid.uuid4().hex+'.jpg';path=root/filename
                scale=min(1,640/max(frame.width,frame.height));width=max(2,int(frame.width*scale)//2*2);height=max(2,int(frame.height*scale)//2*2)
                resized=frame.reformat(width=width,height=height,format='yuvj420p')
                with self.av.open(str(path),mode='w',format='image2') as output:
                    encoder=output.add_stream('mjpeg');encoder.width=width;encoder.height=height;encoder.pix_fmt='yuvj420p';encoder.thread_count=1
                    resized.pts=None
                    for packet in encoder.encode(resized):output.mux(packet)
                    for packet in encoder.encode():output.mux(packet)
                with path.open('rb') as image:sha=hashlib.file_digest(image,'sha256').hexdigest()
                rows.append({'frame_id':key,'timestamp':timestamp,'filename':filename,'image_hash':sha,'interval':interval,'duration':duration,'duration_basis':basis})
                if len(rows)>=60:break
                next_time=timestamp+interval
        return rows


def index(import_database,output,backend,seconds=300,limit=100,reader=video_stream):
    source=Path(import_database).resolve(strict=True);output=Path(output).resolve()
    if source==output:raise ValueError('Samples need a separate derived store')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row;roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0]);candidates=defaultdict(list)
        for row in conn.execute("SELECT * FROM occurrences WHERE present=1 AND kind='video' AND content_hash IS NOT NULL ORDER BY size,member!='',source,offset"):candidates[row['content_hash']].append(dict(row))
    processed=0;deadline=time.monotonic()+seconds
    with database(output,roots) as conn:
        root=output.parent/'video-samples';root.mkdir(exist_ok=True)
        create_fact_table(conn,'frame_facts',{'frame_id':'TEXT NOT NULL','content_hash':'TEXT NOT NULL','sampler':'TEXT NOT NULL','timestamp':'REAL NOT NULL','filename':'TEXT NOT NULL','image_hash':'TEXT NOT NULL','interval':'REAL NOT NULL','duration':'REAL NOT NULL'})
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS frame_identity ON frame_facts(frame_id)')
        conn.execute('CREATE TABLE IF NOT EXISTS frame_work(content_hash TEXT,sampler TEXT,frames INTEGER,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,sampler))')
        models=[backend.identity,*getattr(backend,'compatible_models',[])]
        marks=','.join('?' for _ in models)
        work={r[0]:r[1] for model in reversed(models) for r in conn.execute('SELECT content_hash,error FROM frame_work WHERE sampler=?',(model,))}
        current={r[0] for r in conn.execute('SELECT content_hash FROM frame_work WHERE sampler=?',(backend.identity,))}
        retry_errors=getattr(backend,'retry_errors',{'ValueError'})
        done=current|{digest for digest,error in work.items() if error not in retry_errors}
        # Retry only the failure families explicitly measured for this configuration.
        candidates=dict(sorted(candidates.items(),key=lambda pair:pair[0] not in work))
        for digest,occurrences in candidates.items():
            if processed>=limit or time.monotonic()>deadline:break
            if digest in done:continue
            frames=[];error='NoReadableSource'
            for row in occurrences:
                try:
                    with reader(row) as stream:frames=backend.extract(stream,root,digest)
                    error=None;break
                except Exception as exc:frames=[];error=type(exc).__name__
            with conn:
                for frame in frames:
                    frame=dict(frame);basis=frame.pop('duration_basis','container')
                    insert_fact(conn,'frame_facts',{**frame,'content_hash':digest,'sampler':backend.identity,'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset'],'timestamp':frame['timestamp'],'duration_basis':basis}),'extractor':'pyav-keyframe-samples','extractor_version':'1','confidence':1.0,'derived_at':now(),'tier':'personal'})
                conn.execute('INSERT INTO frame_work VALUES(?,?,?,?,?)',(digest,backend.identity,len(frames),error,now()))
            done.add(digest);work[digest]=error;processed+=1
            if processed%10==0:print(json.dumps({'phase':'frames','processed_this_run':processed,'processed_videos':len(done)}),flush=True)
        with conn:
            conn.execute("INSERT OR REPLACE INTO settings VALUES('frames_current',?)",(backend.identity,))
            conn.execute("INSERT OR REPLACE INTO settings VALUES('frames_compatible',?)",(json.dumps(models),))
        return {'processed_this_run':processed,'processed_videos':len(done),'remaining':len(set(candidates)-done),'frames':conn.execute('SELECT count(*) FROM frame_facts WHERE sampler IN ('+marks+')',models).fetchone()[0],'errors':sum(error is not None for error in work.values()),'model':backend.identity}


def coverage(conn):
    """Current compatible results, distinct from retained attempt history."""
    current=conn.execute("SELECT value FROM settings WHERE key='frames_current'").fetchone()
    compatible=conn.execute("SELECT value FROM settings WHERE key='frames_compatible'").fetchone()
    models=json.loads(compatible[0]) if compatible else ([current[0]] if current else [])
    work={};count=0
    if models:
        marks=','.join('?' for _ in models)
        for model in reversed(models):
            for row in conn.execute('SELECT content_hash,error FROM frame_work WHERE sampler=?',(model,)):
                work[row[0]]=row[1]
        count=conn.execute('SELECT count(*) FROM frame_facts WHERE sampler IN ('+marks+')',models).fetchone()[0]
    return {'contents':len(work),'frames':count,'errors':sum(error is not None for error in work.values()),
            'retained_attempts':conn.execute('SELECT count(*) FROM frame_work').fetchone()[0],
            'retained_error_records':conn.execute('SELECT count(*) FROM frame_work WHERE error IS NOT NULL').fetchone()[0]}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--imports',type=Path,required=True);p.add_argument('--database',type=Path,required=True);p.add_argument('--seconds',type=int,default=300);p.add_argument('--limit',type=int,default=100);a=p.parse_args()
    if min(a.seconds,a.limit)<1:p.error('Work limits must be positive')
    print(json.dumps(index(a.imports,a.database,Sampler(),a.seconds,a.limit)),flush=True)

if __name__=='__main__':main()
