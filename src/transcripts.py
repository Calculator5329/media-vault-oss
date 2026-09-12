"""Offline video speech and timestamp facts over verified source streams."""
import argparse
from collections import defaultdict
from contextlib import closing,contextmanager,ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import zipfile
from .imports import database,now,_source_stat
from .kit import create_fact_table,insert_fact


@contextmanager
def video_stream(row):
    if row['size']>4*1024**3:raise ValueError('Video exceeds bounded input size')
    before=_source_stat(row);digest=hashlib.sha256();total=0;start=time.monotonic()
    with ExitStack() as stack:
        if row['member']:
            archive=stack.enter_context(zipfile.ZipFile(row['source']));info=next(i for i in archive.infolist() if i.header_offset==row['offset'])
            if (info.filename,info.file_size,info.CRC)!=(row['member'],row['size'],row['crc']):raise ValueError('ZIP member changed')
            stream=stack.enter_context(archive.open(info))
        else:stream=stack.enter_context(Path(row['source']).open('rb'))
        while chunk:=stream.read(1024*1024):
            if time.monotonic()-start>180:raise TimeoutError('Verification timed out')
            total+=len(chunk)
            if total>row['size']:raise ValueError('Source grew')
            digest.update(chunk)
        if total!=row['size'] or digest.hexdigest()!=row['content_hash']:raise ValueError('Source identity mismatch')
        stream.seek(0);yield stream
        after=_source_stat(row)
        if before.st_ino!=after.st_ino:raise ValueError('Source changed')



@contextmanager
def transcript_stream(row,cache):
    if row['member']:
        from .video import verified_copy
        path=verified_copy(row,cache)
        with path.open('rb') as stream:yield stream
    else:
        with video_stream(row) as stream:yield stream


def validate(value):
    duration=value['duration']
    if not math.isfinite(duration) or not 0<=duration<=7200:raise ValueError('Unsupported duration')
    for segment in value['segments']:
        if not all(math.isfinite(segment[k]) for k in ('start','end','avg_logprob','no_speech_prob')) or not 0<=segment['start']<segment['end']<=duration+1:raise ValueError('Invalid timestamps')
        if not isinstance(segment['text'],str) or not segment['text'].strip() or len(segment['text'])>10000:raise ValueError('Invalid transcript')
    return value


class Whisper:
    chunk_long_videos=True
    retry_policy='encoding-1'
    retry_timestamps=True
    def __init__(self,path):
        os.environ.update(HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
        import onnxruntime as ort
        ort.disable_telemetry_events()
        import faster_whisper,ctranslate2
        root=Path(path);receipt=json.loads((root/'acquisition.json').read_text())
        for row in receipt['files']:
            with (root/row['file']).open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=row['sha256']:raise ValueError('Transcript model changed')
        self.identity='whisper-small:'+hashlib.sha256(json.dumps({'files':receipt['files'],'revision':receipt['revision'],'faster_whisper':faster_whisper.__version__,'ctranslate2':ctranslate2.__version__,'onnxruntime':ort.__version__,'compute':'cpu-int8','beam_size':5,'vad':True,'version':1},sort_keys=True).encode()).hexdigest()
        self.model=faster_whisper.WhisperModel(str(root),device='cpu',compute_type='int8',cpu_threads=4,num_workers=1,local_files_only=True)

    def transcribe(self,stream):
        return validate(self.transcribe_raw(stream))

    def transcribe_raw(self,stream):
        """Local inference payload; callers must validate before publication."""
        import av
        with av.open(stream,metadata_errors='surrogateescape') as container:
            if not container.streams.audio:return {'duration':0,'language':None,'segments':[],'status':'no_audio'}
            if not container.duration or container.duration/av.time_base>7200:raise ValueError('Video exceeds two hours')
        stream.seek(0)
        segments,info=self.model.transcribe(stream,beam_size=5,vad_filter=True,condition_on_previous_text=False)
        values=[];start=time.monotonic()
        for segment in segments:
            if time.monotonic()-start>900:raise TimeoutError('Transcription timed out')
            if segment.text.strip():values.append({k:getattr(segment,k) for k in ('start','end','text','avg_logprob','no_speech_prob')})
        return {'duration':info.duration,'language':info.language,'segments':values,'status':'complete' if values else 'no_speech'}



def timestamp_retry_candidates(conn,backend,candidates,frames_database):
    if not getattr(backend,'retry_timestamps',False) or not frames_database.is_file():return set()
    with closing(sqlite3.connect(frames_database.as_uri()+'?mode=ro',uri=True)) as frames:
        if not frames.execute("SELECT 1 FROM sqlite_master WHERE name='frame_facts'").fetchone():return set()
        durations={}
        for digest,duration in frames.execute('SELECT content_hash,duration FROM frame_facts'):
            durations.setdefault(digest,[]).append(duration)
    failed={r[0] for r in conn.execute("SELECT content_hash FROM transcript_work WHERE model=? AND error='ValueError' AND content_hash NOT IN(SELECT content_hash FROM transcript_attempt_history WHERE model=? AND policy='timestamp-recovery-1')",(backend.identity,backend.identity))}
    return {digest for digest in failed & set(candidates) if durations.get(digest)
            and all(isinstance(d,(int,float)) and math.isfinite(d) and 10<=d<=7200 for d in durations[digest])
            and any(row['size']<=4*1024**3 for row in candidates[digest])}


def index(import_database,output,backend,seconds=600,limit=5,reader=None,chunk_reader=None):
    source=Path(import_database).resolve(strict=True)
    if reader is None:reader=lambda row:transcript_stream(row,Path(output).resolve().parent/'playback')
    if source==Path(output).resolve():raise ValueError('Transcript store must be separate')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row;roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0]);candidates=defaultdict(list)
        for row in conn.execute("SELECT * FROM occurrences WHERE present=1 AND kind='video' AND content_hash IS NOT NULL ORDER BY size,member!='',source,offset"):candidates[row['content_hash']].append(dict(row))
    start=time.monotonic();processed=0
    with database(output,roots) as conn:
        create_fact_table(conn,'transcript_facts',{'content_hash':'TEXT NOT NULL','model':'TEXT NOT NULL','start_seconds':'REAL NOT NULL','end_seconds':'REAL NOT NULL','text':'TEXT NOT NULL','language':'TEXT','metrics_json':'TEXT NOT NULL'})
        conn.execute('CREATE INDEX IF NOT EXISTS transcript_by_content ON transcript_facts(content_hash,model)')
        conn.execute('CREATE TABLE IF NOT EXISTS transcript_work(content_hash TEXT,model TEXT,status TEXT,segments INTEGER,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model))')
        conn.execute('CREATE TABLE IF NOT EXISTS transcript_attempt_history(content_hash TEXT,model TEXT,policy TEXT,status TEXT,segments INTEGER,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model,policy))')
        # Exclusive database ownership proves any retained running attempt has no writer.
        with conn:conn.execute("UPDATE transcript_work SET status='error',error='InterruptedAttempt' WHERE model=? AND status='running'",(backend.identity,))
        done={r[0] for r in conn.execute('SELECT content_hash FROM transcript_work WHERE model=?',(backend.identity,))}
        policy=getattr(backend,'retry_policy',None)
        eligible={r[0] for r in conn.execute("SELECT content_hash FROM transcript_work WHERE model=? AND error='UnicodeDecodeError' AND content_hash NOT IN(SELECT content_hash FROM transcript_attempt_history WHERE model=? AND policy=?)",(backend.identity,backend.identity,policy))} & set(candidates) if policy=='encoding-1' else set()
        timestamp_retries=timestamp_retry_candidates(conn,backend,candidates,source.with_name('frames.db'))
        eligible.update(timestamp_retries)
        for digest,rows in sorted(candidates.items(),key=lambda pair:pair[0] not in eligible):
            if processed>=limit or time.monotonic()-start>=seconds:break
            retry=digest in eligible
            attempt_policy='timestamp-recovery-1' if digest in timestamp_retries else policy
            if digest in done and not retry:continue
            with conn:
                if retry:
                    conn.execute('INSERT INTO transcript_attempt_history SELECT content_hash,model,?,status,segments,error,derived_at FROM transcript_work WHERE content_hash=? AND model=?',(attempt_policy,digest,backend.identity))
                    conn.execute("UPDATE transcript_work SET status='running',segments=0,error=NULL,derived_at=? WHERE content_hash=? AND model=?",(now(),digest,backend.identity))
                else:conn.execute('INSERT INTO transcript_work VALUES(?,?,?,?,?,?)',(digest,backend.identity,'running',0,None,now()))
            value=None;error='NoReadableSource'
            for row in rows:
                try:
                    with reader(row) as stream:value=validate(backend.transcribe(stream))
                    error=None;break
                except Exception as exc:error=type(exc).__name__;value=None
            with conn:
                if value:
                    for segment in value['segments']:
                        insert_fact(conn,'transcript_facts',{'content_hash':digest,'model':backend.identity,'start_seconds':segment['start'],'end_seconds':segment['end'],'text':segment['text'].strip(),'language':value['language'],'metrics_json':json.dumps({k:segment[k] for k in ('avg_logprob','no_speech_prob')}),'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset'],'start_seconds':segment['start'],'end_seconds':segment['end']}),'extractor':'faster-whisper-small','extractor_version':'1','confidence':min(1,math.exp(min(0,segment['avg_logprob']))),'derived_at':now(),'tier':'personal'})
                conn.execute('UPDATE transcript_work SET status=?,segments=?,error=?,derived_at=? WHERE content_hash=? AND model=?',(value['status'] if value else 'error',len(value['segments']) if value else 0,error,now(),digest,backend.identity))
            done.add(digest);eligible.discard(digest);processed+=1
            print(json.dumps({'phase':'transcripts','processed_this_run':processed,'processed_videos':len(done),'status':value['status'] if value else 'error'}),flush=True)
        chunks={'chunks_this_run':0,'remaining_chunk_videos':0}
        if getattr(backend,'chunk_long_videos',False):
            from .transcript_chunks import run
            chunks=run(conn,backend,candidates,source.with_name('frames.db'),Path(output).resolve().parent,start+seconds,max(0,limit-processed),reader=chunk_reader)
        with conn:conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('transcripts_current',?)",(backend.identity,))
        return {'processed_this_run':processed,'processed_videos':len(done),'remaining':len(set(candidates)-done)+len(eligible)+chunks['remaining_chunk_videos'],'model':backend.identity,**chunks}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--imports',type=Path,required=True);p.add_argument('--database',type=Path,required=True);p.add_argument('--model',type=Path,required=True);p.add_argument('--seconds',type=int,default=600);p.add_argument('--limit',type=int,default=5);a=p.parse_args()
    if min(a.seconds,a.limit)<1:p.error('Work limits must be positive')
    print(json.dumps(index(a.imports,a.database,Whisper(a.model),a.seconds,a.limit)),flush=True)


if __name__=='__main__':main()
