"""Supervise bounded local AI batches without moving or uploading originals.

One CPU photo queue, one CPU audio queue, and one GPU queue preserve per-store
writer order. Existing workers own identity, model provenance, and checkpoints.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_EXCEPTION
from contextlib import contextmanager
from .portable import lock as flock, assert_local_state
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid
from .imports import now

ROLES={'photos':('faces','ocr','similar'),'gpu':('vision','scenes','descriptions'),'audio':('frames','transcripts')}
RESOURCES={'vision_python','face_python','audio_python','vision_model','face_models','description_model','transcript_model'}
# A stage runs only when every resource it needs is present in the resources file.
STAGE_NEEDS={'faces':('face_python','face_models'),'ocr':('face_python',),'similar':('face_python',),
             'vision':('vision_python','vision_model'),'scenes':('vision_python','vision_model'),'descriptions':('vision_python','description_model'),
             'frames':('audio_python',),'transcripts':('audio_python','transcript_model')}


def enabled_roles(resources):
    present=set(resources)
    unknown=present-RESOURCES
    if unknown:raise ValueError('Unknown resource keys: '+', '.join(sorted(unknown)))
    roles={role:tuple(s for s in stages if set(STAGE_NEEDS[s])<=present) for role,stages in ROLES.items()}
    roles={role:stages for role,stages in roles.items() if stages}
    if not roles:raise ValueError('No enrichment stage has its runtime and model paths; see docs/models.md')
    return roles
COUNTS={'processed_this_run','indexed','errors','remaining','processed_photos','observations','photos_without_detected_faces','candidates','processed_videos','frames','model'}


def local_path(value):
    return assert_local_state(value,'Derived state and runtimes')


def record(directory,stage,state,**extra):
    event={'at':now(),'stage':stage,'state':state,**extra}
    with (directory/'enrichment-events.jsonl').open('a') as stream:
        flock(stream);stream.write(json.dumps(event)+'\n');stream.flush();os.fsync(stream.fileno())
    return event


def status(directory):
    path=Path(directory)/'enrichment-events.jsonl';stages={}
    if path.is_file():
        with path.open() as stream:
            for line in stream:
                try:event=json.loads(line);stages[event['stage']]=event
                except (json.JSONDecodeError,KeyError):continue
    return {'stages':stages,'meaning':'Retained worker checkpoints; current process health is reported by the local service manager.'}


@contextmanager
def exclusive(directory):
    with (directory/'enrichment.lock').open('a') as lock:
        flock(lock,blocking=False)
        yield


def command(stage,directory,resources,seconds,limit):
    runtime='face_python' if stage in ('faces','ocr','similar') else 'audio_python' if stage in ('transcripts','frames') else 'vision_python'
    args=[str(resources[runtime]),'-m','src.'+stage,'--imports',str(directory/'imports.db'),'--database',str(directory/(stage+'.db')),'--seconds',str(seconds),'--limit',str(limit)]
    model={'vision':'vision_model','scenes':'vision_model','descriptions':'description_model','transcripts':'transcript_model','faces':'face_models'}.get(stage)
    if model:args.extend(['--models' if stage=='faces' else '--model',str(resources[model])])
    if stage=='faces':args.append('--publish-groups')
    return args


def terminate(process):
    if process.poll() is not None:return
    if os.name=='nt':
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        return
    try:os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:return
    try:process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait()


def run_worker(args,log,repo,stop,timeout):
    env={**os.environ,'PYTHONPATH':str(repo),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false'}
    with log.open('xb') as stream:
        isolation={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
        process=subprocess.Popen(args,cwd=log.parent,stdout=stream,stderr=subprocess.STDOUT,env=env,**isolation)
        deadline=time.monotonic()+timeout
        try:
            while process.poll() is None:
                if stop.wait(.5):terminate(process);return {'state':'interrupted'}
                if time.monotonic()>deadline:terminate(process);return {'state':'timeout'}
        finally:
            terminate(process)
    if process.returncode:return {'state':'error','exit_code':process.returncode}
    # Only known aggregate fields enter the shared status, never recognized text.
    result=None
    with log.open() as stream:
        for line in stream:
            try:value=json.loads(line)
            except json.JSONDecodeError:continue
            if isinstance(value,dict) and 'remaining' in value:result={k:v for k,v in value.items() if k in COUNTS}
    return {'state':'complete','result':result} if result is not None else {'state':'error','error':'MissingCheckpoint'}


class Supervisor:
    def __init__(self,repo,directory,resources,sources,seconds=300,limit=1000,interval=30,idle_interval=900,runner=run_worker):
        self.repo=Path(repo).resolve();self.directory=local_path(directory)
        roles=enabled_roles(resources)
        for value in resources.values():local_path(value)
        # Invoking the venv symlink selects its pyvenv.cfg; realpath bypasses it.
        self.resources={k:Path(v).absolute() for k,v in resources.items()}
        if any(not p.exists() for p in self.resources.values()):raise ValueError('Runtime or local model is unavailable')
        self.sources=[Path(p).resolve() for p in sources]
        if not self.sources:raise ValueError('Read-only source roots are required')
        if any(self.directory==p or p in self.directory.parents for p in self.sources):raise ValueError('Derived state cannot be inside originals')
        if min(seconds,limit,interval,idle_interval)<1:raise ValueError('Work bounds must be positive')
        self.seconds=seconds;self.limit=limit;self.interval=interval;self.idle_interval=idle_interval;self.runner=runner;self.stop=threading.Event();self.roles=roles
        self.directory.mkdir(parents=True,exist_ok=True);self.logs=self.directory/'worker-logs';self.logs.mkdir(exist_ok=True)

    def batch(self,stage):
        if self.stop.is_set():return {'state':'interrupted'}
        if not all(p.is_dir() for p in self.sources):return record(self.directory,stage,'waiting_for_sources')
        if not (self.directory/'imports.db').is_file():return record(self.directory,stage,'waiting_for_imports')
        log=self.logs/(stage+'-'+uuid.uuid4().hex+'.log')
        record(self.directory,stage,'running',log=log.name)
        try:result=self.runner(command(stage,self.directory,self.resources,self.seconds,self.limit),log,self.repo,self.stop,self.seconds+1200)
        except Exception as exc:result={'state':'error','error':type(exc).__name__}
        return record(self.directory,stage,**result,log=log.name)

    def queue(self,stages,once):
        due={s:0 for s in stages}
        while not self.stop.is_set():
            for stage in stages:
                if self.stop.is_set():break
                if time.monotonic()<due[stage]:continue
                event=self.batch(stage)
                delay=self.interval if event['state']=='complete' and event.get('result',{}).get('remaining',0)>0 else self.idle_interval
                due[stage]=time.monotonic()+delay
            if once:break
            self.stop.wait(min(self.interval,max(.1,min(due.values())-time.monotonic())))

    def run(self,once=False):
        with exclusive(self.directory):
            with ThreadPoolExecutor(max_workers=len(self.roles)) as pool:
                futures=[pool.submit(self.queue,stages,once) for stages in self.roles.values()]
                try:
                    finished,_=wait(futures,return_when=FIRST_EXCEPTION)
                    for future in finished:future.result()
                finally:self.stop.set()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--resources',type=Path,required=True);p.add_argument('--directory',type=Path,required=True);p.add_argument('--source',type=Path,action='append',required=True);p.add_argument('--seconds',type=int,default=300);p.add_argument('--limit',type=int,default=1000);p.add_argument('--interval',type=int,default=30);p.add_argument('--idle-interval',type=int,default=900);p.add_argument('--once',action='store_true');a=p.parse_args()
    supervisor=Supervisor(Path(__file__).resolve().parents[1],a.directory,json.loads(a.resources.read_text()),a.source,a.seconds,a.limit,a.interval,a.idle_interval)
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:supervisor.stop.set())
    try:supervisor.run(a.once)
    except BlockingIOError:p.error('Another enrichment supervisor owns this catalog')


if __name__=='__main__':main()
