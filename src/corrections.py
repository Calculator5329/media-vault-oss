"""Append-only owner constraints over consumer-local, stable identity observations.

No detector, embedding model, named person or cross-corpus ID is selected here.
The future detector supplies observations; the kit's ordinary rebuild callback
replays the external correction log into provenance-carrying assignments.
"""
from .portable import lock as flock
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from .kit import create_fact_table, insert_fact

OBSERVATIONS="mv_identity_observation"
ASSIGNMENTS="mv_identity_assignment"

class CorrectionError(ValueError): pass


def setup(conn):
    create_fact_table(conn,OBSERVATIONS,{"observation_id":"TEXT PRIMARY KEY","entity_id":"TEXT NOT NULL"})
    create_fact_table(conn,ASSIGNMENTS,{"observation_id":"TEXT PRIMARY KEY","entity_id":"TEXT","correction_ids":"TEXT NOT NULL"})


def _validate(events):
    seen=set()
    required={"merge":{"left","right"},"split":{"entity","new_entity","observations"},"not-her":{"entity","observation"},"retract":{"target"}}
    for e in events:
        if not isinstance(e,dict) or e.get("op") not in required:raise CorrectionError("unknown correction operation")
        if set(e)!={"id","op","at","reason"}|required[e["op"]]:raise CorrectionError("correction fields do not match operation")
        if any(not isinstance(v,str) or not v.strip() for k,v in e.items() if k!="observations"):raise CorrectionError("correction strings must be nonempty")
        if e["id"] in seen:raise CorrectionError("duplicate correction id")
        if e["op"]=="retract":
            target=next((prior for prior in events if prior.get("id")==e["target"]),None)
            if e["target"] not in seen or target["op"]=="retract":raise CorrectionError("retraction must name an earlier ordinary correction")
        seen.add(e["id"])
        try:
            if datetime.fromisoformat(e["at"].replace("Z","+00:00")).tzinfo is None:raise ValueError()
        except ValueError as exc:raise CorrectionError("correction time needs an explicit timezone") from exc
        if e["op"]=="split":
            ids=e["observations"]
            if not isinstance(ids,list) or not ids or any(not isinstance(v,str) or not v for v in ids) or len(set(ids))!=len(ids):raise CorrectionError("split needs distinct observation ids")
            if e["entity"]==e["new_entity"]:raise CorrectionError("split target must differ")
        if e["op"]=="merge" and e["left"]==e["right"]:raise CorrectionError("merge endpoints must differ")


def read_log(path):
    p=Path(path)
    if not p.exists():return []
    try: events=[json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    except (ValueError,UnicodeError) as exc:raise CorrectionError("invalid correction log") from exc
    _validate(events);return events


def append(path,event):
    """Serialize concurrent appends; validate the complete history before writing."""
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open("a+",encoding="utf-8") as f:
        flock(f);f.seek(0)
        try:events=[json.loads(line) for line in f if line.strip()]
        except ValueError as exc:raise CorrectionError("invalid correction log") from exc
        _validate(events+[event]);f.seek(0);existing=f.read();f.seek(0,2)
        if existing and not existing.endswith("\n"):f.write("\n")
        f.write(json.dumps(event,sort_keys=True)+"\n");f.flush();os.fsync(f.fileno())


def apply_constraints(assignments,events):
    """Replay all operations. Contradictory or stale IDs refuse publication."""
    _validate(events)
    result=dict(assignments);entities=set(result.values());parent={e:e for e in entities};negative=[];separate=[]
    def find(e):
        if e not in parent:raise CorrectionError("correction references unknown entity; reconfirm stable identity")
        while parent[e]!=e:e=parent[e]
        return e
    retracted={e["target"] for e in events if e["op"]=="retract"}
    for e in events:
        if e["op"]=="retract" or e["id"] in retracted:continue
        op=e["op"]
        if op=="merge":
            a,b=find(e["left"]),find(e["right"])
            low,high=sorted((a,b));parent[high]=low
            for obs,value in result.items():
                if value is not None:result[obs]=find(value)
        elif op=="split":
            old=find(e["entity"]);new=e["new_entity"]
            if new in parent:raise CorrectionError("split target already exists")
            selected=set(e["observations"])
            if any(obs not in result or result[obs] is None or find(result[obs])!=old for obs in selected):raise CorrectionError("split observations no longer belong to its entity")
            remaining={obs for obs,value in result.items() if value is not None and find(value)==old and obs not in selected}
            parent[new]=new
            for obs in selected:result[obs]=new
            separate.extend((a,b) for a in selected for b in remaining)
        else:
            obs=e["observation"];entity=find(e["entity"])
            if obs not in result:raise CorrectionError("correction references missing observation")
            negative.append((obs,entity))
            if result[obs] is not None and find(result[obs])==entity:result[obs]=None
    for a,b in separate:
        if result[a] is not None and result[b] is not None and find(result[a])==find(result[b]):raise CorrectionError("merge contradicts a split constraint")
    for obs,entity in negative:
        if result[obs] is not None and find(result[obs])==find(entity):raise CorrectionError("merge contradicts a not-her constraint")
    return {obs:find(value) if value is not None else None for obs,value in sorted(result.items())}


class ConstraintOverlay:
    def __init__(self,path):self.path=Path(path)
    def __call__(self,conn):
        """Kit Rebuilder.apply_corrections callback; original observations stay intact."""
        setup(conn)
        cursor=conn.execute(f"SELECT * FROM {OBSERVATIONS} ORDER BY observation_id")
        columns=[c[0] for c in cursor.description];rows=[dict(zip(columns,r)) for r in cursor]
        events=read_log(self.path)
        resolved=apply_constraints({r["observation_id"]:r["entity_id"] for r in rows},events)
        # Compute everything before replacing the derived table. Caller transaction
        # rolls back invalid correction publication while retaining the source log.
        conn.execute(f"DELETE FROM {ASSIGNMENTS}")
        digest=hashlib.sha256(json.dumps(events,sort_keys=True).encode()).hexdigest()
        for row in rows:
            obs=row["observation_id"];result={**row,"entity_id":resolved[obs],"correction_ids":json.dumps([e["id"] for e in events])}
            if events:
                result.update(source_path=str(self.path),source_span="sha256:"+digest,extractor="mv.owner-constraints",extractor_version="1",derived_at=events[-1]["at"])
            insert_fact(conn,ASSIGNMENTS,result)
