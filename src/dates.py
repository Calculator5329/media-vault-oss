"""Compare capture claims without inventing a timezone or discarding sources."""
from datetime import datetime, timezone
import hashlib
import json


def normalize(value):
    if not isinstance(value,str) or len(value)<19:raise ValueError('A complete timestamp is required')
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    aware=parsed.utcoffset() is not None
    return {'value':parsed.astimezone(timezone.utc).isoformat() if aware else parsed.isoformat(),
            'timezone_known':aware}


def evidence(facts):
    choices=[];seen=set()
    for fact in facts:
        value=fact.get('value')
        if fact.get('attribute')!='date' or not isinstance(value,dict) or value.get('meaning')!='capture':continue
        raw=value.get('value')
        try:normalized=normalize(raw)
        except (ValueError,TypeError,OverflowError):normalized=None
        source={k:fact.get(k) for k in ('source_path','source_span','extractor','extractor_version','confidence','derived_at','tier')}
        identity=hashlib.sha256(json.dumps({'value':value,'source_path':source['source_path'],'source_span':source['source_span'],'extractor':source['extractor']},sort_keys=True).encode()).hexdigest()
        if identity in seen:continue
        seen.add(identity);choices.append({'id':identity,'date':value,'normalized':normalized,'source':source})
    valid=[c['normalized'] for c in choices if c['normalized']]
    known={c['value'] for c in valid if c['timezone_known']}
    unknown={c['value'] for c in valid if not c['timezone_known']}
    if len(valid)!=len(choices):status='invalid'
    elif len(known)>1:status='conflict'
    elif unknown and (known or len(unknown)>1):status='timezone_unknown'
    elif len(choices)>1:status='equivalent'
    elif choices:status='single'
    else:status='missing'
    return {'status':status,'choices':choices,'comparison':'Explicit offsets are compared in UTC. Unknown timezones are never inferred.'}


import re
PATTERNS=[('timestamp',re.compile(r'(?<!\d)((?:19|20)\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})(?!\d)'),0.8),
          ('date',re.compile(r'(?<!\d)((?:19|20)\d{2})-(\d{2})-(\d{2})(?!\d)'),0.7),
          ('compact',re.compile(r'(?<!\d)((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)'),0.6),
          ('epoch',re.compile(r'(?<!\d)(1[3-8]\d{8})(\d{3})?(?!\d)'),0.6)]


def infer(names,latest_year=None):
    """Capture-date candidates read from file names alone.

Camera, phone, screenshot and messaging apps stamp the moment into the name.
These are inferences with a stated confidence, never recorded capture facts,
and they carry no timezone unless the name is a UTC epoch."""
    latest=latest_year or datetime.now().year+1
    seen=set();candidates=[]
    for name in sorted(set(names)):
        for label,pattern,confidence in PATTERNS:
            match=pattern.search(name)
            if not match:continue
            try:
                if label=='epoch':when=datetime.fromtimestamp(int(match.group(1)),timezone.utc)
                elif label=='timestamp':when=datetime(*map(int,match.groups()))
                else:when=datetime(int(match.group(1)),int(match.group(2)),int(match.group(3)),12)
            except (ValueError,OverflowError,OSError):continue
            if not 1990<=when.year<=latest:continue
            value=when.isoformat()
            if value not in seen:
                seen.add(value);candidates.append({'value':value,'meaning':'capture','field':'file name ('+label+')','confidence':confidence,'name':name})
            break
    return sorted(candidates,key=lambda c:(-c['confidence'],c['value']))
