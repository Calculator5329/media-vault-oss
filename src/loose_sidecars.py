"""Match inventoried adjacent Google Photos JSON to one verified media source."""
from collections import Counter
import hashlib
import json
from pathlib import Path
from .exports import MAX_JSON
from .imports import _source_stat


def collect(rows):
    media={r['source']:r for r in rows if not r['member'] and r['kind'] in ('photo','video')}
    counts=Counter();matches=[]
    for sidecar in rows:
        path=Path(sidecar['source'])
        if sidecar['member'] or path.suffix.lower()!='.json':continue
        if sidecar['size']>MAX_JSON:
            counts['loose_sidecar_oversized']+=1;continue
        if not sidecar['content_hash']:
            counts['loose_sidecar_waiting_for_hash']+=1;continue
        before=_source_stat(sidecar)
        with path.open('rb') as stream:raw=stream.read(MAX_JSON+1)
        after=_source_stat(sidecar)
        if before.st_ino!=after.st_ino or hashlib.sha256(raw).hexdigest()!=sidecar['content_hash']:
            raise ValueError('Sidecar changed since verified inventory')
        try:
            data=json.loads(raw)
            if not isinstance(data,dict) or 'photoTakenTime' not in data:
                counts['loose_json_unrecognized']+=1;continue
            json.dumps(data,allow_nan=False)
        except (ValueError,UnicodeError):
            counts['loose_sidecar_unreadable']+=1;continue
        names=set();title=data.get('title')
        if isinstance(title,str) and title and '/' not in title and '\\' not in title and title not in ('.','..'):
            names.add(str(path.parent/title))
        for suffix in ('.supplemental-metadata.json','.json'):
            if path.name.endswith(suffix):
                names.add(str(path)[:-len(suffix)]);break
        candidates={r['id']:r for name in names if (r:=media.get(name)) is not None}
        if len(candidates)!=1:
            counts['loose_sidecar_ambiguous' if candidates else 'loose_sidecar_unmatched']+=1;continue
        target=next(iter(candidates.values()))
        if not target['content_hash']:
            counts['loose_sidecar_waiting_for_hash']+=1;continue
        _source_stat(target)
        matches.append((target,sidecar,data));counts['loose_sidecar_attached']+=1
    return matches,counts
