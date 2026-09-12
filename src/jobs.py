"""Continuing read-only import cycles with retained progress and one writer."""
import argparse
from contextlib import closing
from .portable import lock as flock, on_external_root
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from . import catalog,imports,metadata


def events_path(directory):return Path(directory)/'import-events.jsonl'


def record(directory,event):
    value={'at':imports.now(),**event};path=events_path(directory)
    with path.open('a') as stream:stream.write(json.dumps(value)+'\n');stream.flush();os.fsync(stream.fileno())
    return value


def status(directory):
    path=events_path(directory)
    if not path.is_file():return {'state':'not_started','generation':None}
    last=None;generation=None
    with path.open() as stream:
        for line in stream:
            try:event=json.loads(line)
            except json.JSONDecodeError:continue
            last=event
            if event['state'] in ('complete','partial'):generation=event['generation']
    return {**(last or {'state':'not_started'}),'generation':generation}


def cycle(config,directory,exports=None,seconds=300,limit=1000):
    directory=Path(directory).resolve();exports=Path(exports).resolve() if exports is not None else None;extra=[exports] if exports is not None else []
    roots=json.loads(Path(config).read_text())['sources']
    if not roots:raise ValueError('At least one configured media source required')
    sources=[Path(p).resolve() for p in roots]
    if len(set(sources))!=len(sources):raise ValueError('Source folders must be distinct')
    if on_external_root(directory) or any(directory==p or p in directory.parents for p in [*sources,*extra]):raise ValueError('Catalog must stay on the local drive, outside sources and removable media')
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/'import-cycle.lock').open('a') as lock:
        try:flock(lock,blocking=False)
        except BlockingIOError:return {'state':'busy'}
        if not all(p.is_dir() for p in [*sources,*extra]):return record(directory,{'state':'waiting_for_sources'})
        record(directory,{'state':'scanning'})
        try:
            count=0;metadata_pending=0;catalogs=[]
            for index,root in enumerate(sources):
                path=directory/'catalog.db' if index==0 else directory/'source-catalogs'/(hashlib.sha256(str(root).encode()).hexdigest()+'.db')
                _,conn=catalog.connect(root,path)
                catalogs.append(path)
                with closing(conn):
                    count+=catalog.inventory(root,conn)
                    catalog.enrich(root,conn,limit=limit,workers=2)
                    catalog.publish_facts(root,conn)
                    metadata_pending+=conn.execute("SELECT count(*) FROM files WHERE present=1 AND kind IN ('photo','video') AND error IS NULL AND (metadata_version IS NULL OR metadata_version != ?)", (catalog.VERSION,)).fetchone()[0]
            record(directory,{'state':'verifying','master_files':count})
            with imports.database(directory/'imports.db',[*sources,*extra]) as conn:
                imports.inventory(conn,catalogs[0],exports,additional_catalogs=catalogs[1:])
                imports.hash_pending(conn,seconds=seconds,limit=limit)
                from .media_types import recover
                type_recovery=recover(conn,directory/'vision.db',directory/'type-probes')
                progress=imports.summary(conn)
            record(directory,{'state':'connecting_metadata','verified_contents':progress['verified_contents']})
            result=metadata.refresh(directory/'imports.db',exports,directory/'metadata.db')
            return record(directory,{'state':'partial' if progress['pending'] or metadata_pending else 'complete','metadata_pending':metadata_pending,'generation':uuid.uuid4().hex,'imports':progress,'metadata':dict(result),'type_recovery':type_recovery})
        except Exception as exc:
            record(directory,{'state':'error','error':type(exc).__name__})
            raise


def config_revision(path):
    try:
        value=Path(path).stat()
        return value.st_ino,value.st_size,value.st_mtime_ns
    except OSError:return None


def wait_for_config(path,revision,seconds):
    """Preserve the idle deadline, but wake when the local config changes."""
    deadline=time.monotonic()+seconds
    while config_revision(path)==revision:
        remaining=deadline-time.monotonic()
        if remaining<=0:return
        time.sleep(min(5,remaining))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=Path('vault.config.json'));p.add_argument('--directory',type=Path,default=Path('.catalog'));p.add_argument('--exports',type=Path,help='Folder of Google Takeout ZIP files (optional)');p.add_argument('--seconds',type=int,default=300);p.add_argument('--limit',type=int,default=1000);p.add_argument('--watch',action='store_true');p.add_argument('--interval',type=int,default=900);a=p.parse_args()
    if min(a.seconds,a.limit,a.interval)<1:p.error('Work limits must be positive')
    while True:
        revision=config_revision(a.config)
        try:result=cycle(a.config,a.directory,a.exports,a.seconds,a.limit)
        except Exception as exc:result={'state':'error','error':type(exc).__name__}
        print(json.dumps(result),flush=True)
        if not a.watch:break
        wait_for_config(a.config,revision,min(a.interval,30) if result['state']=='partial' else a.interval)


if __name__=='__main__':main()
