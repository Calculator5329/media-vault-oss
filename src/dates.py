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
