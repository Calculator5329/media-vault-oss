"""Warm the preview cache so browsing never waits on a cold source read.

Previews are derived state in .catalog/previews, keyed by content hash. The
viewer generates them on demand, which on a 35k-item archive on a USB disk
means a results page of never-seen photos trickles in for tens of seconds.
The warmer generates every missing preview once, newest first, in the
background, and yields to live requests. Originals are only read.
"""
import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def missing(library):
    """Items without a cached preview, newest first, ZIP members grouped by archive so reads stay sequential."""
    have={name[:64] for name in os.listdir(library.thumbnails)} if library.thumbnails.is_dir() else set()
    items=[i for i in library.items if i['kind']!='other' and i['id'] not in have]
    plain=[i for i in items if i['id'] not in library.zip_sources]
    zipped=[i for i in items if i['id'] in library.zip_sources]
    plain.sort(key=lambda i:(i['day'] or ''),reverse=True)
    zipped.sort(key=lambda i:(library.zip_sources[i['id']][0]['source'],library.zip_sources[i['id']][0]['offset']))
    return plain+zipped


def warm(library,workers=4,stop=None,limit=None,progress=None):
    """Generate missing previews. Returns counts; never raises for a single bad item."""
    todo=missing(library)
    if limit is not None:todo=todo[:limit]
    done=0;failed=0;started=time.monotonic()
    def one(item):
        if stop is not None and stop.is_set():return None
        try:library.thumbnail(item['id']);return True
        except Exception:return False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ok in pool.map(one,todo):
            if ok is None:break
            done+=ok;failed+=not ok
            if progress and (done+failed)%200==0:progress(done,failed,len(todo))
    return {'candidates':len(todo),'generated':done,'failed':failed,'seconds':round(time.monotonic()-started,1)}


class Warmer:
    """Background warmer for the viewer: a separate low-priority process, so preview work never
competes with request threads for the interpreter lock. Ends with the viewer."""
    def __init__(self,library,workers=3):
        self.library=library;self.workers=workers;self.process=None
    def start(self):
        import subprocess,sys
        args=[sys.executable,'-m','src.previews','--database',str(self.library.catalog_database),'--imports',str(self.library.import_database),'--workers',str(self.workers)]
        self.process=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,preexec_fn=lambda:os.nice(15),cwd=str(Path(__file__).resolve().parents[1]))
        return self
    def running(self):return self.process is not None and self.process.poll() is None


def main():
    parser=argparse.ArgumentParser(description='Generate every missing preview for a catalog, newest first.')
    parser.add_argument('--database',type=Path,required=True);parser.add_argument('--imports',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=6);parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    from .library import Library
    library=Library(args.database,args.imports)
    print(f'{len(missing(library))} previews missing of {len(library.items)} items',flush=True)
    result=warm(library,workers=args.workers,limit=args.limit,progress=lambda d,f,t:print(f'{d+f}/{t} ({f} failed)',flush=True))
    print(result)


if __name__=='__main__':
    main()
