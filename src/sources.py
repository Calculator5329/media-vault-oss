"""Additive source configuration with retained revisions and local-only writes."""
from contextlib import closing
from .portable import lock as flock, on_external_root
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid


class Sources:
    def __init__(self,config,directory,primary):
        self.config=Path(config).resolve(strict=True)
        self.directory=Path(directory).resolve(strict=True)
        self.primary=Path(primary).resolve()
        if on_external_root(self.config) or on_external_root(self.directory):
            raise ValueError('Configuration must stay on the local drive, outside removable media')
        self._read()

    def _read(self):
        raw=self.config.read_bytes();value=json.loads(raw)
        if not isinstance(value,dict) or not isinstance(value.get('sources'),list) or not value['sources']:
            raise ValueError('Source configuration unavailable')
        roots=[Path(p).resolve() for p in value['sources']]
        if roots[0]!=self.primary:raise ValueError('Configuration belongs to another library')
        if any(p==root or root in p.parents for p in (self.config,self.directory) for root in roots):
            raise ValueError('Configuration cannot be inside originals')
        return raw,value,roots

    def _registered(self):
        db=self.directory/'imports.db'
        if not db.is_file():return []
        with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as conn:
            row=conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()
        return [Path(p).resolve() for p in json.loads(row[0])] if row else []

    def status(self):
        raw,_,roots=self._read();registered=self._registered()
        return {'revision':hashlib.sha256(raw).hexdigest(),
                'sources':[{'path':str(p),'online':p.is_dir(),'role':'Primary photos' if index==0 else 'Additional photos','registered':p in registered} for index,p in enumerate(roots)],
                'exports':[{'path':str(p),'online':p.is_dir()} for p in registered if p not in roots],
                'meaning':'Originals stay in place. While the import service is running, new folders trigger a scan after about five seconds when idle, or after the current scan finishes. All watched sources must be connected. Adjacent Google Photos JSON metadata is matched within each folder; other sidecar formats are not yet supported.'}

    def add(self,path,revision):
        if not isinstance(path,str) or not path.strip() or len(path)>4096 or not Path(path).expanduser().is_absolute():
            raise ValueError('Enter an absolute folder path')
        candidate=Path(path).expanduser().resolve(strict=True)
        if not candidate.is_dir():raise ValueError('Choose a readable folder')
        with os.scandir(candidate) as entries:next(entries,None)
        with (self.directory/'source-config.lock').open('a') as lock:
            flock(lock)
            raw,value,roots=self._read()
            if revision!=hashlib.sha256(raw).hexdigest():raise ValueError('Sources changed; refresh before adding a folder')
            for root in [*roots,*self._registered(),self.directory,self.config.parent]:
                if candidate==root or root in candidate.parents or candidate in root.parents:
                    raise ValueError('Choose a separate folder outside watched folders and vault state')
            history=self.directory/'source-config-history';history.mkdir(exist_ok=True)
            if history.is_symlink() or history.resolve().parent!=self.directory:
                raise ValueError('Configuration history must stay inside vault state')
            token=uuid.uuid4().hex
            # Retain the exact previous configuration before atomically replacing it.
            with (history/(token+'.json')).open('xb') as backup:
                os.chmod(backup.name,0o600);backup.write(raw);backup.flush();os.fsync(backup.fileno())
            value['sources'].append(str(candidate))
            staging=self.config.with_name('.'+self.config.name+'.'+token+'.pending')
            with staging.open('x') as stream:
                os.chmod(staging,0o600);stream.write(json.dumps(value,indent=2)+'\n');stream.flush();os.fsync(stream.fileno())
            staging.replace(self.config)
        return self.status()
