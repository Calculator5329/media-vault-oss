"""Verified local video copies and seekable playback derivatives."""
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from .imports import _source_stat,now
from .portable import assert_local_state

MAX_SOURCE=4*1024**3
ENCODERS=('h264_nvenc','libx264')  # GPU first when ffmpeg has it; software otherwise


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


class SourceUnavailable(RuntimeError):
    """Every recorded source of this content is unreachable right now: the archive drive is not mounted, or the file is gone."""


def sources_reachable(rows):
    """True when at least one recorded source file can be seen from this machine."""
    return any(Path(row['source']).is_file() for row in rows)


BROWSER_VIDEO={'h264'};BROWSER_AUDIO={'aac','mp3',None};BROWSER_CONTAINERS={'mov','mp4','m4a','3gp','3g2','mj2'}


def codecs(path):
    """Container, first video stream and first audio stream, as ffprobe reports them."""
    result=subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_entries','format=format_name:stream=codec_type,codec_name,pix_fmt','-of','json',str(path)],capture_output=True,timeout=30,check=True)
    data=json.loads(result.stdout);streams=data.get('streams',[])
    video=next((s for s in streams if s.get('codec_type')=='video'),None);audio=next((s for s in streams if s.get('codec_type')=='audio'),None)
    return {'container':set((data.get('format',{}).get('format_name') or '').split(',')),'video':video.get('codec_name') if video else None,'pix_fmt':video.get('pix_fmt') if video else None,'audio':audio.get('codec_name') if audio else None}


def browser_playable(path):
    """True when Chromium-class browsers play this file as-is: H.264 4:2:0 in an MP4/MOV container with AAC, MP3 or no audio."""
    try:facts=codecs(path)
    except (OSError,subprocess.SubprocessError,ValueError):return False
    return bool(facts['container']&BROWSER_CONTAINERS) and facts['video'] in BROWSER_VIDEO and (facts['pix_fmt'] or 'yuv420p') in ('yuv420p','yuvj420p') and facts['audio'] in BROWSER_AUDIO


def direct_source(rows):
    """An original the browser can stream unchanged: a plain file, still the size the catalog recorded, in a playable codec. Read only."""
    for row in rows:
        if row['member']:continue
        path=Path(row['source'])
        try:
            if path.stat().st_size!=row['size']:continue
        except OSError:continue
        if browser_playable(path):return path
    return None


def probe(path):
    result=subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_entries','format=duration:stream=codec_type','-of','json',str(path)],capture_output=True,timeout=30,check=True)
    data=json.loads(result.stdout)
    if not any(s.get('codec_type')=='video' for s in data.get('streams',[])):raise ValueError('No video stream')
    if not data.get('format',{}).get('duration'):raise MissingDuration('Container duration absent')
    duration=float(data['format']['duration'])
    if not 0<duration<=7200 or not any(s['codec_type']=='video' for s in data['streams']):raise ValueError('Unsupported video duration or stream')
    return duration


def prepare(rows,root,duration_hint=None):
    root=Path(root).resolve()
    assert_local_state(root,'Cache')
    root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root).free<12*1024**3:raise RuntimeError('Not enough Linux cache space')
    source=None;guard=None
    for row in rows:
        if not row['member'] and Path(row['source']).is_file() and row['size']<=MAX_SOURCE:
            # A plain file is read in place; identity is checked by inode, size and mtime before and after instead of a full copy and hash.
            try:guard=_source_stat(row)
            except (OSError,ValueError):continue
            if guard.st_size!=row['size']:guard=None;continue
            source=Path(row['source']);break
        try:source=verified_copy(row,root);break
        except (OSError,ValueError,RuntimeError,StopIteration,zipfile.BadZipFile):continue
    if source is None:
        if not sources_reachable(rows):raise SourceUnavailable('No recorded source of this video is reachable')
        raise RuntimeError('No verified video source within preparation limits')
    basis='container'
    try:duration=probe(source)
    except MissingDuration:
        if not duration_hint or duration_hint.get('content_hash')!=row['content_hash'] or duration_hint.get('basis')!='decoded_video_extent' or not 0<float(duration_hint.get('duration',0))<=7200:raise
        duration=float(duration_hint['duration']);basis='decoded_video_extent'
    staging=root/(row['content_hash']+'.'+uuid.uuid4().hex+'.mp4')
    def command(encoder):
        video=['-c:v','h264_nvenc','-preset','p4','-rc','vbr','-cq','28'] if encoder=='h264_nvenc' else ['-c:v','libx264','-preset','veryfast','-crf','26']
        return ['ffmpeg','-v','error','-nostdin','-protocol_whitelist','file,pipe','-i',str(source),'-map','0:v:0','-map','0:a:0?','-map_metadata','-1','-vf',"scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2",*video,'-maxrate','3M','-bufsize','6M','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-movflags','+faststart','-n',str(staging)]
    encoder=None
    for encoder in ENCODERS:
        result=subprocess.run(command(encoder),capture_output=True,timeout=600)
        if result.returncode==0 and staging.is_file():break
        staging.unlink(missing_ok=True)
    if guard is not None:
        after=_source_stat(row)
        if (after.st_ino,after.st_size,after.st_mtime_ns)!=(guard.st_ino,guard.st_size,guard.st_mtime_ns):staging.unlink(missing_ok=True);raise ValueError('Source identity changed')
    if result.returncode or not staging.is_file() or abs(probe(staging)-duration)>max(1,duration*.01):raise RuntimeError('Video preparation incomplete')
    target=root/(row['content_hash']+'.mp4')
    if target.exists():target.rename(root/(target.name+'.retained-'+uuid.uuid4().hex))
    staging.rename(target)
    receipt={'content_hash':row['content_hash'],'duration':duration,'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset']}),'extractor':'ffmpeg-playback','extractor_version':'2','encoder':encoder,'confidence':1,'derived_at':now(),'tier':'personal','duration_basis':basis,'duration_evidence':duration_hint if basis=='decoded_video_extent' else None,'settings':'H.264/AAC, fit 1280x720, CRF26; originals retained'}
    (root/(row['content_hash']+'.json')).write_text(json.dumps(receipt,indent=2)+'\n')
    return target


OFFLINE_MESSAGE='The archive drive is not connected, so the original cannot be read. Reconnect it, then choose Prepare playback. Nothing was changed.'


class Playback:
    """One preparation at a time; per-video outcomes stay in memory until the next attempt. A verdict taken while the archive drive was away is never kept."""
    def __init__(self,root):
        self.root=Path(root);self.lock=threading.Lock();self.active=None;self.errors={};self.offline=set();self._direct={}

    def status(self,digest,rows=None):
        target=self.root/(digest+'.mp4');receipt=self.root/(digest+'.json')
        if target.is_file() and receipt.is_file():return {'state':'ready'}
        if rows is not None:
            if digest in self.offline and sources_reachable(rows):self.offline.discard(digest);self.errors.pop(digest,None)
            if self.direct(digest,rows):return {'state':'ready','direct':True,'message':'Playing the original as it is; nothing was prepared.'}
        if self.active==digest:return {'state':'preparing'}
        if digest in self.errors:return {'state':'error','message':self.errors[digest]}
        return {'state':'pending'}

    def direct(self,digest,rows):
        if digest not in self._direct:
            found=direct_source(rows)
            if found is None and not sources_reachable(rows):return None
            self._direct[digest]=found
        return self._direct[digest]

    def start(self,digest,rows,duration_hint=None):
        with self.lock:
            if self.status(digest,rows)['state'] in ('ready','preparing'):return self.status(digest,rows)
            if not sources_reachable(rows):
                self.offline.add(digest);self.errors[digest]=OFFLINE_MESSAGE;return self.status(digest,rows)
            if self.active:return {'state':'busy','message':'Another video is preparing. Try again shortly.'}
            self.active=digest;self.errors.pop(digest,None);self.offline.discard(digest)
        def work():
            try:prepare(rows,self.root,duration_hint)
            except SourceUnavailable:self.offline.add(digest);self.errors[digest]=OFFLINE_MESSAGE
            except Exception as error:
                reason=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__
                self.errors[digest]=f'Could not prepare this video locally ({reason}). The original is unchanged.'
                print(f'playback {digest[:12]} failed: {type(error).__name__}: {error}',file=sys.stderr,flush=True)
            finally:
                with self.lock:self.active=None
        threading.Thread(target=work,daemon=True).start()
        return {'state':'preparing'}

    def file(self,digest,rows=None):
        state=self.status(digest,rows)
        if state['state']!='ready':raise FileNotFoundError('Playback is not ready')
        if state.get('direct'):return self.direct(digest,rows)
        return self.root/(digest+'.mp4')
