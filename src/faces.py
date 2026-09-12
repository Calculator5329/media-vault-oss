"""Fresh, offline face observations over verified photos. No inferred names.

Detection and embeddings are model suggestions. Per-photo checkpoints distinguish
success with no detected face from failed decoding. Observations carry immutable
content/model/geometry identities; names and review decisions remain external.
"""
import argparse
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import zipfile

from .imports import database, now
from .kit import create_fact_table, insert_fact
from .vision import read_image, unit

VERSION='faces-1'


class OpenCVFaces:
    embedding_dimension=128
    def __init__(self,models):
        import cv2
        import numpy as np
        self.cv=cv2;self.np=np;cv2.setNumThreads(2)
        root=Path(models).resolve(strict=True)
        detector=root/'face_detection_yunet_2023mar.onnx'
        recognizer=root/'face_recognition_sface_2021dec.onnx'
        receipt=json.loads((root/'acquisition.json').read_text())
        for path in (detector,recognizer):
            expected=next(r['sha256'] for r in receipt['models'] if r['file']==path.name)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Face model identity mismatch')
        settings={'models':receipt['models'],'opencv':cv2.__version__,'max_side':1600,'score':0.9,'nms':0.3,'version':VERSION}
        self.identity='yunet-sface:'+hashlib.sha256(json.dumps(settings,sort_keys=True).encode()).hexdigest()
        self.detector=cv2.FaceDetectorYN.create(str(detector),'',(320,320),0.9,0.3,5000)
        self.recognizer=cv2.FaceRecognizerSF.create(str(recognizer),'')

    def detect(self,image):
        bgr=self.cv.cvtColor(self.np.asarray(image),self.cv.COLOR_RGB2BGR)
        height,width=bgr.shape[:2];self.detector.setInputSize((width,height))
        _,faces=self.detector.detect(bgr)
        if faces is None:return []
        result=[]
        for face in faces:
            x,y,w,h=map(float,face[:4]);x0=max(0,x);y0=max(0,y);x1=min(width,x+w);y1=min(height,y+h)
            if min(x1-x0,y1-y0)<24:continue
            crop=self.recognizer.alignCrop(bgr,face)
            vector=unit(self.recognizer.feature(crop).flatten().tolist())
            box=[round(x0/width,6),round(y0/height,6),round(x1/width,6),round(y1/height,6)]
            result.append({'box':box,'score':float(face[-1]),'vector':vector})
        return sorted(result,key=lambda f:tuple(f['box']))


def index(import_database,output,backend,seconds=600,limit=1000,reader=read_image):
    source=Path(import_database).resolve(strict=True)
    if Path(output).resolve()==source:raise ValueError('Face store must be separate')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
        candidates=defaultdict(list)
        for row in conn.execute("SELECT * FROM occurrences WHERE present=1 AND kind='photo' AND content_hash IS NOT NULL ORDER BY member!='',source,offset"):
            candidates[row['content_hash']].append(dict(row))
    start=time.monotonic();processed=0
    with database(output,roots) as conn:
        create_fact_table(conn,'face_observations',{'face_id':'TEXT PRIMARY KEY','content_hash':'TEXT NOT NULL','model':'TEXT NOT NULL','box_json':'TEXT NOT NULL','vector_json':'TEXT NOT NULL'})
        conn.execute('CREATE TABLE IF NOT EXISTS face_work(content_hash TEXT,model TEXT,status TEXT,face_count INTEGER,error TEXT,derived_at TEXT,PRIMARY KEY(content_hash,model))')
        done={r[0] for r in conn.execute('SELECT content_hash FROM face_work WHERE model=?',(backend.identity,))}
        for digest,rows in candidates.items():
            if processed>=limit or time.monotonic()-start>=seconds:break
            if digest in done:continue
            image=None;error='NoReadableSource';observations=[]
            for row in rows:
                try:image=reader(row);break
                except (OSError,ValueError,RuntimeError,StopIteration,zipfile.BadZipFile) as exc:error=type(exc).__name__
            if image is not None:
                try:
                    for face in backend.detect(image):
                        box=face['box'];vector=unit(face['vector']);score=float(face['score'])
                        if len(box)!=4 or not all(isinstance(v,(float,int)) and 0<=v<=1 for v in box) or not (box[0]<box[2] and box[1]<box[3]) or not 0<=score<=1:raise ValueError('Invalid face observation')
                        geometry=json.dumps(box,separators=(',',':'))
                        identity=hashlib.sha256((digest+backend.identity+geometry).encode()).hexdigest()
                        observations.append({'face_id':identity,'content_hash':digest,'model':backend.identity,'box_json':geometry,'vector_json':json.dumps(vector),
                            'source_path':row['source'],'source_span':json.dumps({'member':row['member'],'offset':row['offset'],'box':box}),
                            'extractor':'opencv-yunet-sface','extractor_version':VERSION,'confidence':score,'derived_at':now(),'tier':'personal'})
                    error=None
                except Exception as exc:
                    error=type(exc).__name__;observations=[]
                finally:
                    if hasattr(image,'close'):image.close()
            with conn:
                for observation in observations:insert_fact(conn,'face_observations',observation)
                conn.execute('INSERT INTO face_work VALUES(?,?,?,?,?,?)',(digest,backend.identity,'error' if error else 'complete',len(observations),error,now()))
            done.add(digest);processed+=1
            if processed%100==0:print(json.dumps({'phase':'face-observations',**summary(conn,backend.identity),'processed_this_run':processed}),flush=True)
        from .face_recovery import run as recover
        recovered=recover(conn,backend,candidates,source.parent/'image-recovery.db',start+seconds,max(0,limit-processed))
        return {**summary(conn,backend.identity),'processed_this_run':processed+recovered['processed'],'candidates':len(candidates),'remaining':len(set(candidates)-done)+recovered['remaining'],'model':backend.identity}


def summary(conn,model):
    row=conn.execute("SELECT count(*),sum(status='error'),sum(status='complete' AND face_count=0),sum(face_count) FROM face_work WHERE model=?",(model,)).fetchone()
    values=dict(zip(('processed_photos','errors','photos_without_detected_faces','observations'),(v or 0 for v in row)))
    from .face_recovery import completed
    recovered=completed(conn,model);values['errors']-=len(recovered)
    values['photos_without_detected_faces']+=sum(r['face_count']==0 for r in recovered.values())
    values['observations']+=sum(r['face_count'] for r in recovered.values())
    return values


def observations(conn,model):
    from .face_recovery import observations as recovered
    cursor=conn.execute('SELECT * FROM face_observations WHERE model=?',(model,));columns=[r[0] for r in cursor.description]
    return [dict(zip(columns,row)) for row in cursor]+recovered(conn,model)


def suggest_groups(observations,anchor_threshold=0.65,member_threshold=0.55):
    """Conservative complete-link suggestions, never named identities.

Every member must match the fixed anchor and every existing member. Faces from
one photo cannot join the same group. This avoids single-link similarity chains;
thresholds remain provisional until evaluated on the owner's review examples.
"""
    import numpy as np
    groups=[]
    for face in sorted(observations,key=lambda f:f['face_id']):
        vector=np.asarray(unit(face['vector']),dtype=np.float32)
        matches=[]
        for index,group in enumerate(groups):
            if face['content_hash'] in group['contents']:continue
            score=float(group['vectors'][0]@vector)
            if score>=anchor_threshold and all(float(v@vector)>=member_threshold for v in group['vectors']):matches.append((score,index))
        matches.sort(reverse=True)
        if matches and (len(matches)==1 or matches[0][0]-matches[1][0]>=0.08):
            group=groups[matches[0][1]]
        else:
            group={'id':face['face_id'],'faces':[],'vectors':[],'contents':set()};groups.append(group)
        group['faces'].append(face['face_id']);group['vectors'].append(vector);group['contents'].add(face['content_hash'])
    return sorted([{'id':g['id'],'faces':g['faces']} for g in groups],key=lambda g:(-len(g['faces']),g['id']))


def publish_groups(output,model):
    source=Path(output).resolve(strict=True)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as conn:
        roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
        rows=[{'face_id':r['face_id'],'content_hash':r['content_hash'],'vector':json.loads(r['vector_json'])} for r in observations(conn,model)]
    groups=suggest_groups(rows)
    revision=hashlib.sha256(json.dumps({'faces':sorted(r['face_id'] for r in rows),'version':'complete-link-1','anchor':0.65,'member':0.55},sort_keys=True).encode()).hexdigest()
    with database(output,roots) as conn:
        create_fact_table(conn,'face_groups',{'group_id':'TEXT NOT NULL','model':'TEXT NOT NULL','revision':'TEXT NOT NULL','face_ids_json':'TEXT NOT NULL'})
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS face_group_identity ON face_groups(group_id,model,revision)')
        if not conn.execute('SELECT 1 FROM face_groups WHERE model=? AND revision=?',(model,revision)).fetchone():
            with conn:
                for group in groups:
                    insert_fact(conn,'face_groups',{'group_id':group['id'],'model':model,'revision':revision,'face_ids_json':json.dumps(group['faces']),
                        'source_path':str(source),'source_span':revision,'extractor':'conservative-face-groups','extractor_version':'1','confidence':0.55,'derived_at':now(),'tier':'personal'})
        with conn:
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('face_groups_current',?)",(json.dumps({'model':model,'revision':revision}),))
    return {'groups':len(groups),'multi_face_groups':sum(len(g['faces'])>1 for g in groups),'faces':len(rows),'revision':revision}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--imports',type=Path,required=True);parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--models',type=Path,required=True);parser.add_argument('--seconds',type=int,default=600);parser.add_argument('--limit',type=int,default=1000)
    parser.add_argument('--publish-groups',action='store_true',help='Publish conservative review groups after this checkpoint')
    args=parser.parse_args()
    if args.limit<1 or args.seconds<1:parser.error('Work bounds must be positive')
    result=index(args.imports,args.database,OpenCVFaces(args.models),args.seconds,args.limit)
    if args.publish_groups:result['grouping']=publish_groups(args.database,result['model'])
    print(json.dumps(result),flush=True)





def suggest_person(observations,assignments,ignored,person,limit=200):
    """Suggest unassigned faces from confirmed exemplars; never assign identities."""
    import numpy as np
    if not 1<=limit<=200:raise ValueError('Choose 1 to 200 candidates')
    by_id={r['face_id']:r for r in observations};profiles={}
    for key,value in assignments.items():
        if key in by_id:profiles.setdefault(value['person'],[]).append(by_id[key])
    references=profiles.get(person,[])
    if not references:return {'candidates':[],'reference_faces':0,'total':0}
    occupied={r['content_hash'] for r in references}
    pending=[r for r in observations if r['face_id'] not in assignments and r['face_id'] not in ignored and r['content_hash'] not in occupied]
    if not pending:return {'candidates':[],'reference_faces':len(references),'total':0}
    matrix=np.asarray([unit(r['vector']) for r in pending],dtype=np.float32)
    best=None;second=None;other=np.full(len(pending),-1,dtype=np.float32)
    for who,rows in profiles.items():
        rows=sorted(rows,key=lambda r:r['face_id'])
        if len(rows)>32:rows=[rows[round(i*(len(rows)-1)/31)] for i in range(32)]
        scores=matrix@np.asarray([unit(r['vector']) for r in rows],dtype=np.float32).T
        maxima=scores.max(axis=1)
        if who==person:
            best=maxima;second=np.partition(scores,-2,axis=1)[:,-2] if len(rows)>1 else None
        else:other=np.maximum(other,maxima)
    result=[]
    for i,row in enumerate(pending):
        if best[i]<(0.70 if second is None else 0.65):continue
        if second is not None and second[i]<0.55:continue
        if best[i]-other[i]<0.08:continue
        result.append({'face_id':row['face_id'],'content_hash':row['content_hash'],'similarity':float(best[i])})
    result.sort(key=lambda r:(-r['similarity'],r['face_id']))
    return {'candidates':result[:limit],'reference_faces':len(references),'total':len(result)}

if __name__=='__main__':main()
