"""Timeline coverage with local visual variety; never an aesthetic verdict."""
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
from .vision import unit


def positions(length,count):
    return [round(i*(length-1)/(count-1)) for i in range(count)] if count>1 else ([0] if count else [])


def select(items,limit,database,model=None,*,style='variety',quality_database=None):
    """Return an ordered draft from a bounded pool; missing vectors keep anchors.

    Callers supply chronologically sorted, verified photos after all filters.
    Reads only current-model vectors; no inference, media reads or writes.
    """
    if not 1<=limit<=200:raise ValueError('Choose 1 to 200 highlights')
    if style not in ('variety','quality'):raise ValueError('Choose visual variety or technical clarity')
    pool=[items[i] for i in positions(len(items),min(200,len(items)))]
    anchors=positions(len(pool),min(limit,len(pool)))
    vectors={};path=Path(database)
    if model and pool and path.is_file():
        hashes=[i['content_hash'] for i in pool]
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            for digest,raw in conn.execute('SELECT content_hash,vector_json FROM visual_facts WHERE model=? AND content_hash IN ('+','.join('?' for _ in hashes)+')',(model,*hashes)):
                try:vectors[digest]=unit(json.loads(raw))
                except (ValueError,TypeError):continue
            from .visual_recovery import facts
            for digest,fact in facts(conn,model).items():
                if digest in hashes:
                    try:vectors.setdefault(digest,unit(json.loads(fact['vector_json'])))
                    except (ValueError,TypeError):pass
    qualities={};quality_model=None;quality_status='unused'
    if style=='quality':
        quality_status='unavailable';path=Path(quality_database) if quality_database else None
        if pool and path and path.is_file():
            try:
                with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
                    row=conn.execute("SELECT value FROM settings WHERE key='quality_current'").fetchone()
                    if row:
                        quality_model=row[0];hashes=[i['content_hash'] for i in pool]
                        for digest,score in conn.execute('SELECT content_hash,score FROM quality_facts WHERE model=? AND content_hash IN ('+','.join('?' for _ in hashes)+')',(quality_model,*hashes)):
                            if isinstance(score,(int,float)) and math.isfinite(score):qualities[digest]=score
                        from .quality_recovery import facts
                        for digest,fact in facts(conn,quality_model).items():
                            if digest in hashes:qualities.setdefault(digest,fact['score'])
                        quality_status='available'
            except sqlite3.Error:qualities={};quality_model=None
    chosen=[];changed=0;spans=0;quality_spans=0
    for slot,anchor in enumerate(anchors):
        pick=anchor
        if 0<slot<len(anchors)-1:
            start=(anchors[slot-1]+anchor)//2+1
            stop=(anchor+anchors[slot+1])//2+1
            options=list(range(start,stop))
            prior=[vectors[pool[i]['content_hash']] for i in chosen if pool[i]['content_hash'] in vectors]
            candidates=[vectors.get(pool[i]['content_hash']) for i in options]
            # Do not infer that an unindexed photo is less worthy of selection.
            if style=='quality' and all(pool[i]['content_hash'] in qualities for i in options):
                pick=min(options,key=lambda i:(-qualities[pool[i]['content_hash']],abs(i-anchor),i));quality_spans+=1
            elif prior and all(v is not None and len(v)==len(prior[0]) for v in candidates) and all(len(v)==len(prior[0]) for v in prior):
                def score(i):
                    v=vectors[pool[i]['content_hash']]
                    similarity=max(sum(a*b for a,b in zip(v,p)) for p in prior)
                    return similarity,abs(i-anchor),i
                pick=min(options,key=score);spans+=1
        chosen.append(pick);changed+=pick!=anchor
    return {'items':[pool[i] for i in chosen],
            'selection':{'method':'timeline-quality-1' if quality_spans else 'timeline-variety-1' if spans else 'timeline-1',
                         'candidates':len(pool),'indexed_candidates':len(vectors),
                         'variety_spans':spans,'changed_from_timeline':changed,
                         'model':model if vectors else None,'style':style,
                         'quality_candidates':len(qualities),'quality_spans':quality_spans,
                         'quality_model':quality_model,'quality_status':quality_status}}
