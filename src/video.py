"""Verified local video copies and seekable playback derivatives."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from .imports import _source_stat,now
from .portable import assert_local_state

MAX_SOURCE=4*1024**3


def byte_range(header,size):
    if not header:return 0,size-1,False
    match=re.fullmatch(r'bytes=(\d*)-(\d*)',header)
    if not match or not any(match.groups()):raise ValueError('Unsupported range')
    first,last=match.groups()
    if not first:
        count=int(last)
        if count==0:raise ValueError('Empty suffix')
        return max(0,size-count),size-1,True
    start=int(first);end=min(int(last),size-1) if last else size-1
    if start>=size or end<start:raise ValueError('Range outside file')
    return start,end,True


def verified_copy(row,root):
    root=Path(root).resolve()
    assert_local_state(root,'Cache')
    root.mkdir(parents=True,exist_ok=True)
    if row['size']>MAX_SOURCE:raise ValueError('Video exceeds 4 GiB preparation limit')
    before=_source_stat(row)
    target=root/(row['content_hash']+'.source')
    if target.is_file() and target.stat().st_size==row['size']:
        with target.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()==row['content_hash']:return target
    if shutil.disk_usage(root).free<row['size']+12*1024**3:raise RuntimeError('Not enough Linux cache space')
    staging=root/(row['content_hash']+'.'+uuid.uuid4().hex+'.source')
    start=time.monotonic();digest=hashlib.sha256();total=0
    with ExitStack() as stack:
        if row['member']:
            archive=stack.enter_context(zipfile.ZipFile(row['source']))
            info=next(i for i in archive.infolist() if i.header_offset==row['offset'])
            if (info.filename,info.file_size,info.CRC)!=(row['member'],row['size'],row['crc']):raise ValueError('ZIP member changed')
            source=stack.enter_context(archive.open(info))
        else:source=stack.enter_context(Path(row['source']).open('rb'))
        output=stack.enter_context(staging.open('xb'))
        while chunk:=source.read(1024*1024):
            if time.monotonic()-start>180:raise TimeoutError('Source preparation timed out')
            total+=len(chunk)
            if total>row['size']:raise ValueError('Source grew')
            digest.update(chunk);output.write(chunk)
    after=_source_stat(row)
    if before.st_ino!=after.st_ino or total!=row['size'] or digest.hexdigest()!=row['content_hash']:raise ValueError('Source identity changed')
    if target.exists():target.rename(root/(target.name+'.retained-'+uuid.uuid4().hex))
    staging.rename(target);return target


class MissingDuration(ValueError):
    pass


def probe(path):
    result=subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_entries','format=duration:stream=codec_type','-of','json',str(path)],capture_output=True,timeout=30,check=True)
    data=json.loads(result.stdout)
    if not any(s.get('codec_type')=='video' for s in data.get('streams',[])):raise ValueError('No video stream')
    if not data.get('format',{}).get('duration'):raise MissingDuration('Container duration absent')
    duration=float(data['format']['duration'])
    if not 0<duration<=21600 or not any(s['codec_type']=='video' for s in data['streams']):raise ValueError('Unsupported video duration or stream')
    return duration


def copy_method(path):
    result=subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_entries','stream=codec_type,codec_name,pix_fmt','-of','json',str(path)],capture_output=True,timeout=30,check=True)
    streams=json.loads(result.stdout).get('streams',[])
    video=next((s for s in streams if s.get('codec_type')=='video'),{})
    audio=next((s for s in streams if s.get('codec_type')=='audio'),None)
    if video.get('codec_name')!='h264' or video.get('pix_fmt')!='yuv420p':return None
    if audio is None or audio.get('codec_name')=='aac':return 'stream-copy'
    if audio.get('codec_name')=='ac3':return 'video-copy-audio-transcode'
    return None


def prepare(rows,root,duration_hint=None):
    root=Path(root).resolve()
    assert_local_state(root,'Cache')
    root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root).free<12*1024**3:raise RuntimeError('Not enough Linux cache space')
    source=None
    for row in rows:
        try:source=verified_copy(row,root);break
        except (OSError,ValueError,RuntimeError,StopIteration,zipfile.BadZipFile):continue
    if source is None:raise RuntimeError('No verified video source within preparation limits')
    basis='container'
    try:duration=probe(source)
    except MissingDuration:
        if not duration_hint or duration_hint.get('content_hash')!=row['content_hash'] or duration_hint.get('basis')!='decoded_video_extent' or not 0<float(duration_hint.get('duration',0))<=7200:raise
        duration=float(duration_hint['duration']);basis='decoded_video_extent'
    method='transcode'
    if duration>7200:
        method=copy_method(source)
        if not method:raise ValueError('Long playback requires H.264 video and AAC or AC3 audio')
        audio_space=int(duration*16000) if method=='video-copy-audio-transcode' else 0
        if shutil.disk_usage(root).free<row['size']+audio_space+256*1024**2+12*1024**3:raise RuntimeError('Not enough Linux cache space for long playback')
    staging=root/(row['content_hash']+'.'+uuid.uuid4().hex+'.mp4')
    command=['ffmpeg','-v','error','-nostdin','-threads','2','-protocol_whitelist','file,pipe','-i',str(source),'-map','0:v:0','-map','0:a:0?','-map_metadata','-1','-vf',"scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2",'-c:v','libx264','-preset','fast','-crf','26','-maxrate','3M','-bufsize','6M','-pix_fmt','yuv420p','-threads','2','-c:a','aac','-b:a','128k','-movflags','+faststart','-n',str(staging)]
    if method!='transcode':
        command=['ffmpeg','-v','error','-nostdin','-protocol_whitelist','file,pipe','-i',str(source),'-map','0:v:0','-map','0:a:0?','-map_metadata','-1','-map_chapters','-1','-c','copy','-movflags','+faststart','-n',str(staging)]
        if method=='video-copy-audio-transcode':
            command[-1:-1]=['-c:a','aac','-b:a','128k','-ac','2','-threads','2']
    result=subprocess.run(command,capture_output=True,timeout={'stream-copy':180,'video-copy-audio-transcode':300,'transcode':600}[method])
    if result.returncode or not staging.is_file() or abs(probe(staging)-duration)>max(1,duration*.01):raise RuntimeError('Video preparation incomplete')
    if method!='transcode' and copy_method(staging)!='stream-copy':raise RuntimeError('Prepared video streams are incompatible')
    if method=='video-copy-audio-transcode':
        result=subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries','stream=codec_name,channels','-of','json',str(staging)],capture_output=True,timeout=30,check=True)
        audio=json.loads(result.stdout).get('streams',[])
        if not audio or audio[0].get('codec_name')!='aac' or audio[0].get('channels')!=2:raise RuntimeError('Prepared audio is incomplete')
    target=root/(row['content_hash']+'.mp4')
    if target.exists():target.rename(root/(target.name+'.retained-'+uuid.uuid4().hex))
    staging.rename(target)
    receipt={'content_hash':row['content_hash'],'duration':duration,'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset']}),'extractor':'ffmpeg-playback','extractor_version':'3','confidence':1,'derived_at':now(),'tier':'personal','duration_basis':basis,'duration_evidence':duration_hint if basis=='decoded_video_extent' else None,'method':method,'settings':{'stream-copy':'H.264/AAC stream copy, original dimensions; originals retained','video-copy-audio-transcode':'H.264 video copy, stereo AAC128k audio conversion; originals retained','transcode':'H.264/AAC, fit 1280x720, CRF26; originals retained'}[method]}
    (root/(row['content_hash']+'.json')).write_text(json.dumps(receipt,indent=2)+'\n')
    return target


class Playback:
    def __init__(self,root):
        self.root=Path(root);self.lock=threading.Lock();self.active=None;self.errors={}

    def status(self,digest):
        target=self.root/(digest+'.mp4');receipt=self.root/(digest+'.json')
        if target.is_file() and receipt.is_file():return {'state':'ready'}
        if self.active==digest:return {'state':'preparing'}
        if digest in self.errors:return {'state':'error','message':self.errors[digest]}
        return {'state':'pending'}

    def start(self,digest,rows,duration_hint=None):
        with self.lock:
            if self.status(digest)['state'] in ('ready','preparing'):return self.status(digest)
            if self.active:return {'state':'busy','message':'Another video is preparing. Try again shortly.'}
            self.active=digest;self.errors.pop(digest,None)
        def work():
            try:prepare(rows,self.root,duration_hint)
            except Exception:self.errors[digest]='Could not prepare this video within the local limits. The original is unchanged.'
            finally:
                with self.lock:self.active=None
        threading.Thread(target=work,daemon=True).start()
        return {'state':'preparing'}

    def file(self,digest):
        if self.status(digest)['state']!='ready':raise FileNotFoundError('Playback is not ready')
        return self.root/(digest+'.mp4')
