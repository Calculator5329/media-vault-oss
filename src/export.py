"""Export buckets to folders the owner chooses.

One folder per bucket, named after the bucket, holding a byte-identical copy of every
member. Originals are only read; nothing in the destination is ever deleted or
overwritten: a file that already exists with the right size is left alone, a name
clash gets a short content-id suffix, and every copy is verified against the
content hash before it takes its final name."""
import hashlib,re,threading,time,zipfile
from pathlib import Path

MAX_ERRORS=25

def safe_name(name,fallback):
    cleaned=re.sub(r'[\\/:*?"<>|\x00-\x1f]+',' ',name).strip(' .')
    cleaned=' '.join(cleaned.split())[:80].strip(' .')
    return cleaned or fallback

def _copy(row,target):
    """Stream one verified source into target's .part file, then rename it into place."""
    part=target.with_name(target.name+'.part');digest=hashlib.sha256();written=0
    try:
        with part.open('wb') as out:
            if row['member']:
                with zipfile.ZipFile(row['source']) as archive:
                    info=next(i for i in archive.infolist() if i.header_offset==row['offset'])
                    if (info.filename,info.file_size,info.CRC)!=(row['member'],row['size'],row['crc']):raise ValueError('ZIP member changed')
                    with archive.open(info) as stream:
                        for chunk in iter(lambda:stream.read(1<<20),b''):digest.update(chunk);out.write(chunk);written+=len(chunk)
            else:
                with Path(row['source']).open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1<<20),b''):digest.update(chunk);out.write(chunk);written+=len(chunk)
        if written!=row['size'] or digest.hexdigest()!=row['content_hash']:raise ValueError('Source bytes do not match content identity')
        part.replace(target)
    except BaseException:
        part.unlink(missing_ok=True);raise
    return written

class Exporter:
    def __init__(self):
        self.lock=threading.Lock();self.thread=None;self.stop=threading.Event()
        self.status={'state':'idle'}

    def check_destination(self,library,destination,create_parents=False):
        path=Path(destination).expanduser()
        if not destination or not path.is_absolute():raise ValueError('Choose an absolute folder path')
        path=path.resolve()
        for root in (*library.sources,library.catalog_directory):
            root=Path(root).resolve()
            if path==root or root in path.parents:raise ValueError('Export outside the archive and the catalog; originals stay untouched')
        if path.exists() and not path.is_dir():raise ValueError('That path is a file, not a folder')
        if not path.exists() and not path.parent.is_dir():
            # Name the first folder that is missing, and a same-name-different-case sibling when there is one, since that is the usual typo.
            missing=path.parent
            while not missing.parent.exists() and missing.parent!=missing:missing=missing.parent
            if missing.parent.is_dir():
                twin=next((c for c in missing.parent.iterdir() if c.is_dir() and c.name.casefold()==missing.name.casefold() and c.name!=missing.name),None)
                if twin and not create_parents:raise ValueError(f'{missing} does not exist. Did you mean {twin / path.relative_to(missing)}?')
            if not create_parents:raise ValueError(f'{missing} does not exist. Check the spelling, or choose Create folders and export.')
        return path

    def start(self,library,destination,bucket_ids=None,create_parents=False):
        with self.lock:
            if self.thread and self.thread.is_alive():raise ValueError('An export is already running')
            path=self.check_destination(library,destination,create_parents)
            if create_parents:path.mkdir(parents=True,exist_ok=True)
            state=library.organization.read()
            wanted=[b for b in state['buckets'].values() if b['id'] not in state['archived_buckets'] and (not bucket_ids or b['id'] in set(bucket_ids))]
            if not wanted:raise ValueError('No buckets to export')
            total=sum(len(b['contents']) for b in wanted)
            self.status={'state':'running','destination':str(path),'buckets':len(wanted),'total':total,'copied':0,'existing':0,'failed':0,'bytes':0,'errors':[],'folders':[],'started':time.time(),'finished':None}
            self.stop.clear()
            self.thread=threading.Thread(target=self._run,args=(library,path,wanted),name='bucket-export',daemon=True);self.thread.start()
            return dict(self.status)

    def cancel(self):self.stop.set()

    def snapshot(self):
        with self.lock:return dict(self.status)

    def _note(self,**changes):
        with self.lock:self.status.update(changes)

    def _run(self,library,path,wanted):
        try:
            path.mkdir(parents=True,exist_ok=True)
            used=set()
            for bucket in wanted:
                name=safe_name(bucket['name'],bucket['id'][:8])
                if name.casefold() in used:name=f"{name} {bucket['id'][:8]}"
                used.add(name.casefold());folder=path/name;folder.mkdir(exist_ok=True)
                with self.lock:self.status['folders'].append(str(folder))
                for digest in bucket['contents']:
                    if self.stop.is_set():self._note(state='cancelled',finished=time.time());return
                    item=library.by_content.get(digest);rows=library.sources_by_id.get(item['id'],[]) if item else []
                    if not rows:self._error(f'{digest[:12]}: no verified source on disk');continue
                    row=rows[0];base=Path(row['member'] or row['source']).name;target=folder/base
                    if target.exists() and target.stat().st_size!=row['size']:target=folder/f"{Path(base).stem}-{digest[:8]}{Path(base).suffix}"
                    if target.exists() and target.stat().st_size==row['size']:
                        with self.lock:self.status['existing']+=1
                        continue
                    try:
                        written=_copy(row,target)
                        with self.lock:self.status['copied']+=1;self.status['bytes']+=written
                    except (OSError,ValueError,zipfile.BadZipFile,StopIteration) as exc:self._error(f'{base}: {exc}')
            self._note(state='done',finished=time.time())
        except OSError as exc:
            self._note(state='failed',finished=time.time());self._error(str(exc))

    def _error(self,message):
        with self.lock:
            self.status['failed']+=1
            if len(self.status['errors'])<MAX_ERRORS:self.status['errors'].append(message)
