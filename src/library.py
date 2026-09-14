"""Timeline over verified content and its retained read-only source occurrences.

Unverified master entries stay visible. Verified copies collapse in the view;
their originals and source references are retained. Content hashes, rather than
presentation IDs, are the stable keys for later corrections and enrichment.
"""
from contextlib import closing
from collections import defaultdict
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import sqlite3
import uuid
import zipfile
import threading
import math
import time

from .server import Viewer, THUMBNAIL_WORKERS


class Library(Viewer):
    def __init__(self, database, imports, metadata=None, thumbnails=None, encoder=None, organization=None):
        super().__init__(database, thumbnails)
        self.import_database=Path(imports).resolve()
        self.snapshot_lock=threading.Lock()
        from .jobs import status
        self.generation=status(self.catalog_directory).get('generation')
        self.replacement=None
        from .organization import Organization
        self.organization=Organization(organization or Path(__file__).resolve().parents[1]/'corrections/organization.jsonl')
        from .video import Playback
        self.playback=Playback(self.catalog_directory/'playback')
        self.encoder=encoder
        self.visual_lock=threading.Lock()
        self._memo_lock=threading.Lock();self._select_cache={};self._memo={}
        self.visual_results={}
        self.visual_index=None
        self.description_checked=0
        self.description_rows={}
        self.ocr_checked=0
        self.ocr_texts={}
        self.geographic_names={}
        places_db=self.catalog_directory/'places.db'
        if places_db.is_file():
            with closing(sqlite3.connect(places_db.as_uri()+'?mode=ro',uri=True)) as conn:
                current=conn.execute("SELECT value FROM settings WHERE key='places_current'").fetchone()
                if current:self.geographic_names={r[0]:json.loads(r[1]) for r in conn.execute('SELECT place_key,value_json FROM place_labels WHERE gazetteer=?',(current[0],))}
        self.visual_database=Path(imports).resolve().parent/'vision.db'
        with closing(sqlite3.connect(Path(imports).resolve(strict=True).as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            rows=[dict(r) for r in conn.execute('SELECT * FROM occurrences WHERE present=1')]
            roots=json.loads(conn.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
        if str(self.source) not in roots:
            raise ValueError('Import snapshot belongs to another source')
        with closing(sqlite3.connect(self.import_database.as_uri()+'?mode=ro',uri=True)) as types:
            types.row_factory=sqlite3.Row
            from .media_types import facts as type_facts
            self.type_facts=type_facts(types)
        self.source_occurrences=len(rows)
        by_hash=defaultdict(list)
        master={}
        for row in rows:
            if row['content_hash']:
                by_hash[row['content_hash']].append(row)
                if not row['member']:
                    master[(row['source'],row['size'],row['mtime_ns'])]=row['content_hash']
        self.facts=defaultdict(list)
        metadata=Path(metadata) if metadata else Path(imports).parent/'metadata.db'
        if metadata.is_file():
            with closing(sqlite3.connect(metadata.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
                conn.row_factory=sqlite3.Row
                for row in conn.execute('SELECT * FROM metadata_facts'):
                    value=dict(row)
                    value['value']=json.loads(value.pop('value_json'))
                    self.facts[value['content_hash']].append(value)
        selected=[]
        seen=set()
        self.zip_sources={}
        self.sources_by_id={}
        for item in self.items:
            relative,size,mtime=self.paths[item['id']]
            digest=master.get((str(self.source/relative),size,mtime))
            if digest and digest in seen:
                continue
            if digest:
                seen.add(digest)
            item['content_hash']=digest
            if digest in self.type_facts:item['kind']=self.type_facts[digest]['detected_kind']
            item['source_count']=len(by_hash[digest]) if digest else 1
            item['duplicate']=item['source_count']>1
            self.sources_by_id[item['id']]=by_hash[digest] if digest else []
            self._attach(item)
            selected.append(item)
        for digest,sources in sorted(by_hash.items()):
            if digest in seen:
                continue
            sources=[r for r in sources if r['member']]
            if not sources:
                continue
            row=sources[0]
            key=hashlib.sha256(('zip:'+digest).encode()).hexdigest()
            item={'id':key,'name':PurePosixPath(row['member']).name,'kind':row['kind'],
                  'extension':PurePosixPath(row['member']).suffix.lower(),'size':row['size'],
                  'date':None,'day':None,'location':None,'camera':None,'width':None,'height':None,'keywords':[],'caption':None,'title':None,'rating':None,
                  'duration':None,'metadata_error':False,'duplicate':len(sources)>1,
                  'source_count':len(sources),'content_hash':digest,'source_type':'zip'}
            self.zip_sources[key]=sources
            self.sources_by_id[key]=sources
            self._attach(item)
            selected.append(item)
        self.items=sorted(selected,key=lambda i:(i['day'] or '',i['name']),reverse=True)
        self.by_id={item['id']:item for item in self.items}
        self.by_content={item['content_hash']:item for item in self.items if item['content_hash']}
        self.source_dates={i['id']:(i['date'],i['day'],i.get('date_inferred',False)) for i in self.items}
        self.source_locations={i['id']:i['location'] for i in self.items}
        self._apply_dates()

    def snapshot(self):
        from .jobs import status
        generation=status(self.catalog_directory).get('generation')
        with self.snapshot_lock:
            current=self.replacement or self
            if generation and generation!=current.generation:
                updated=Library(self.catalog_database,self.import_database,thumbnails=self.thumbnails,encoder=self.encoder,organization=self.organization.path)
                updated.visual_index=current.visual_index
                updated.visual_lock=current.visual_lock
                updated.playback=current.playback
                self.replacement=updated
            return self.replacement or self

    def _apply_dates(self):
        state=self.organization.read();choices=state['dates'];locations=state['locations'];dismissed=state['dismissed']
        for item in self.items:
            item['date'],item['day'],item['date_inferred']=self.source_dates[item['id']]
            item['location']=self.source_locations[item['id']]
            owner=locations.get(item['content_hash'])
            item['location_confirmed']=bool(owner)
            if owner:item['location']={'lat':owner['lat'],'lon':owner['lon'],'field':'owner','source':'Owner-set location'}
            item['date_dismissed']=item['content_hash'] in dismissed['date']
            item['location_dismissed']=item['content_hash'] in dismissed['location']
            choice=choices.get(item['content_hash'])
            item['date_confirmed']=bool(choice)
            if choice:
                item['date_inferred']=False
                item['date']={**choice['date'],'source':'Owner-set date' if choice.get('source')=='owner' else 'Owner-selected source date'}
                item['day']=item['date']['value'][:10]
        self.items.sort(key=lambda i:(i['day'] or '',i['name']),reverse=True)

    def video_status(self,key,start=False):
        item=self.by_id[key]
        if item['kind']!='video' or not item['content_hash']:raise ValueError('Choose a verified video')
        rows=self.sources_by_id[key]
        return self.playback.start(item['content_hash'],rows,self.video_duration_hint(item['content_hash'])) if start else self.playback.status(item['content_hash'],rows)

    def video_duration_hint(self,digest):
        path=self.catalog_directory/'frames.db'
        if not path.is_file():return None
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            rows=conn.execute('SELECT duration,sampler,source_span FROM frame_facts WHERE content_hash=?',(digest,)).fetchall()
        measured=[r for r in rows if json.loads(r[2]).get('duration_basis')=='decoded_video_extent']
        if not measured or len({r[0] for r in measured})!=1:return None
        return {'content_hash':digest,'duration':measured[0][0],'basis':'decoded_video_extent','sampler':measured[0][1],'source_span':json.loads(measured[0][2])}

    def video_file(self,key):
        item=self.by_id[key]
        if item['kind']!='video' or not item['content_hash']:raise FileNotFoundError('Unknown video')
        return self.playback.file(item['content_hash'],self.sources_by_id[key])

    def date_review(self,key):
        from .dates import evidence
        item=self.by_id[key]
        result=evidence(self.facts.get(item['content_hash'],[]))
        # File-name inferences sit beside the recorded facts, never among them: choosing one is an owner confirmation.
        from .dates import normalize
        inferred=[{'id':'inferred:'+c['field']+':'+c['value'],'date':{'value':c['value'],'meaning':'capture','field':c['field']},'source':'inferred','normalized':normalize(c['value']),'confidence':c['confidence'],'name':c['name'],'inferred':True} for c in self.inferred_dates(item)]
        result['choices']=list(result['choices'])+inferred
        result['inferred']=bool(item.get('date_inferred'))
        result['selected']=self.organization.read()['dates'].get(item['content_hash'])
        return result

    def inferred_dates(self,item):
        from .dates import infer
        names={item['name']}|{PurePosixPath(r.get('member') or r['source']).name for r in self.sources_by_id.get(item['id'],[])}
        return infer(names)

    @staticmethod
    def place_key(item):
        location=item.get('location')
        if not location:return None
        return f"{math.floor(location['lat']*10)}:{math.floor(location['lon']*10)}"

    def organize(self,op,data):
        if op=='search_review':
            if data.get('mode')=='video_images':
                from .scenes import observations
                rows,_=observations(self.catalog_directory/'frames.db')
                allowed={r['frame_id'] for r in rows if r['content_hash'] in self.by_content}
            else:allowed={i['content_hash'] for i in self.items if i['kind']=='photo' and i['content_hash']}
            if data.get('item') not in allowed:raise ValueError('Unknown search item')
        if op in ('date','reset_date'):
            item=self.by_content.get(data.get('content_hash'))
            if item is None:raise ValueError('Unknown content')
            if op=='date':
                choice=next((c for c in self.date_review(item['id'])['choices'] if c['id']==data.get('choice') and c['normalized']),None)
                if choice is None:raise ValueError('Unknown source date')
                data={'content_hash':item['content_hash'],'choice':{k:choice[k] for k in ('id','date','source')}}
        if op in ('faces','ignore_faces','restore_faces'):
            selected=data.get('faces')
            if not isinstance(selected,list) or not 1<=len(selected)<=200:raise ValueError('Choose 1 to 200 faces')
            cache=self._face_rows();allowed={r['face_id']:r['content_hash'] for r in (cache['rows'] if cache else [])}
            for face in selected:
                if op=='faces':
                    if not isinstance(face,dict) or allowed.get(face.get('face_id'))!=face.get('content_hash') or not face.get('content_hash'):raise ValueError('Unknown face')
                elif not isinstance(face,str) or face not in allowed:raise ValueError('Unknown face')
        if op=='reset_location' and data.get('content_hash') not in self.by_content:raise ValueError('Unknown content')
        if op in ('set_date','set_location','dismiss_gap','restore_gap','favorite','unfavorite','hide','unhide','bucket_add','bucket_remove'):
            contents=data.get('contents')
            if not isinstance(contents,list) or any(not isinstance(d,str) or d not in self.by_content for d in contents):raise ValueError('Select photos or videos from this library')
        if op=='stack':
            allowed={i['content_hash'] for i in self.items if i['kind']=='photo' and i['content_hash']}
            contents=data.get('contents')
            if not isinstance(contents,list) or any(not isinstance(d,str) or d not in allowed for d in contents):raise ValueError('Select verified photos from this library')
        if op=='unstack':
            known={s['id'] for s in self.stacks()['stacks']}
            if data.get('stack') not in known:raise ValueError('Unknown stack')
        if op in ('add','remove','album'):
            kinds=('photo',) if op=='album' else ('photo','video')  # a person can be tagged on a whole video; albums stay photos
            allowed={i['content_hash'] for i in self.items if i['kind'] in kinds and i['content_hash']}
            contents=data.get('contents')
            if not isinstance(contents,list) or any(not isinstance(d,str) or d not in allowed for d in contents):
                raise ValueError('Select verified photos from this library' if op=='album' else 'Select verified photos or videos from this library')
        if op=='exclude_trip':
            allowed={i['content_hash'] for i in self._select(trip=data.get('trip',''))}
            contents=data.get('contents')
            if not isinstance(contents,list) or any(not isinstance(d,str) or d not in allowed for d in contents):raise ValueError('Choose current trip items')
        if op in ('place','trip') and (op=='place' or data.get('place')):
            known={self.place_key(i) for i in self.items if i['location']}
            if not isinstance(data.get('place'),str) or any(k not in known for k in data['place'].split(',')):raise ValueError('Unknown recorded place')
        event=self.organization.append(op,data)
        if op in ('date','set_date','reset_date','set_location','reset_location','dismiss_gap','restore_gap'):self._apply_dates()
        with self.visual_lock:self.visual_results.clear()
        return event

    def person_suggestions(self,person):
        state=self.organization.read()
        if person not in state['people']:raise ValueError('Unknown person')
        path=self.catalog_directory/'faces.db'
        result={'candidates':[],'reference_faces':0,'total':0}
        if path.is_file():
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
                current=conn.execute("SELECT value FROM settings WHERE key='face_groups_current'").fetchone()
                if current:
                    from .faces import suggest_person
                    model=json.loads(current[0])['model']
                    rows=[{'face_id':r[0],'content_hash':r[1],'vector':json.loads(r[2]),'box':json.loads(r[3])} for r in conn.execute('SELECT face_id,content_hash,vector_json,box_json FROM face_observations WHERE model=?',(model,)) if r[1] in self.by_content]
                    result=suggest_person(rows,state['faces'],state['ignored_faces'],person)
                    by_id={r['face_id']:r for r in rows}
                    for candidate in result['candidates']:
                        candidate['item_id']=self.by_content[candidate['content_hash']]['id'];candidate['box']=by_id[candidate['face_id']]['box']
        return {**result,'person':state['people'][person],'meaning':'Possible matches from your confirmed face examples. Review every selected face; these suggestions do not assign anyone automatically.'}

    def _hash_rows(self):
        """Perceptual hashes for verified photos, cached until the store grows."""
        path=self.catalog_directory/'similar.db'
        if not path.is_file():return None
        current={i['content_hash']:i for i in self.items if i['content_hash'] and i['kind']=='photo'}
        from .similar import VERSION
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            if not conn.execute("SELECT name FROM sqlite_master WHERE name='image_hashes'").fetchone():return None
            count=conn.execute('SELECT COUNT(*) FROM similar_work WHERE model=?',(VERSION,)).fetchone()[0]
            cache=getattr(self,'_hash_cache',None)
            if cache and cache['key']==(count,len(current)):return cache
            processed=sum(r[0] in current for r in conn.execute('SELECT content_hash FROM similar_work WHERE model=?',(VERSION,)))
            hashes={r[0]:(r[1],r[2],r[3]) for r in conn.execute('SELECT content_hash,hash_hex,width,height FROM image_hashes WHERE model=?',(VERSION,)) if r[0] in current}
        from .similar import propose
        proposals=propose(list(current.values()),hashes)
        self._hash_cache={'key':(count,len(current)),'hashes':hashes,'processed':processed,'proposals':proposals}
        return self._hash_cache

    def stacks(self):
        """Near-duplicate and burst stacks: the owner's recorded stacks first, then current proposals.

Every stack collapses to its top in the grid. A stack is a view over hashes and times and
hides nothing durably: fanning one out shows every member, choosing a top records the
stack, and "separate" records the stack id so the same proposal stays apart. Members of a
recorded stack never reappear in proposals."""
        cache=self._hash_rows();state=self.organization.read();result=[];claimed=set()
        for stack in state['stacks'].values():
            contents=[c for c in stack['contents'] if c in self.by_content and self.by_content[c]['kind']=='photo']
            if len(contents)<2:continue
            top=stack['top'] if stack['top'] in contents else contents[0]
            claimed.update(contents)
            result.append({'id':stack['id'],'contents':contents,'top':top,'confirmed':True,'day':self.by_content[top]['day']})
        for stack in (cache['proposals'] if cache else [])+self._raw_pairs():
            if stack['id'] in state['separated'] or any(c in claimed for c in stack['contents']):continue
            claimed.update(stack['contents'])
            result.append({**stack,'confirmed':False})
        for stack in result:
            stack['collapse']=True  # always: separating a stack is one click, and nothing is hidden for good
            stack['count']=len(stack['contents'])
            stack['items']=[{'id':self.by_content[c]['id'],'content_hash':c,'name':self.by_content[c]['name'],'day':self.by_content[c]['day'],'date':self.by_content[c]['date'],'width':self.by_content[c]['width'],'height':self.by_content[c]['height'],'top':c==stack['top']} for c in stack['contents']]
        result.sort(key=lambda s:(not s['confirmed'],s['day'] is None,s['day'] or '',s['id']))
        return {'stacks':result,'confirmed':sum(s['confirmed'] for s in result),'proposed':sum(not s['confirmed'] for s in result),
                'hashed':cache['processed'] if cache else 0,'ready':cache is not None,
                'meaning':'Photos within a few bits of each other on the same day, or looser matches shot within ninety seconds. Every stack collapses to its top in the grid; the badge fans it out. Stacks are a view: nothing is deleted or hidden for good. Separate keeps the shots apart; choosing a top records the stack.'}

    def _raw_pairs(self):
        """A camera's raw file and the JPEG it wrote beside it, as one stack with the JPEG on top.

The pair is by folder and stem (DSC_0042.NEF next to DSC_0042.JPG), so the grid shows the
JPEG, the badge fans out to the raw, and Separate keeps them apart like any other stack."""
        from .probe import RAW_EXTS
        from .similar import stack_id
        groups=defaultdict(dict)
        for item in self.items:
            if item['kind']!='photo' or not item['content_hash'] or item['id'] not in self.paths:continue
            # Every folder a copy lives in counts: the JPEG may sit beside the raw in only one of them.
            locations=[Path(self.paths[item['id']][0])]+[Path(r['source']) for r in self.sources_by_id.get(item['id'],()) if not r['member']]
            for location in locations:
                groups[(str(location.parent),location.stem.casefold())][item['content_hash']]=item
        pairs={}
        for members in groups.values():
            raws=[i for i in members.values() if i['extension'] in RAW_EXTS];jpegs=[i for i in members.values() if i['extension'] not in RAW_EXTS]
            if not raws or not jpegs:continue
            contents=[i['content_hash'] for i in jpegs+raws]
            pairs[stack_id(contents)]={'id':stack_id(contents),'contents':contents,'top':jpegs[0]['content_hash'],'day':jpegs[0]['day'],'raw_pair':True}
        return list(pairs.values())

    def _stack_membership(self):
        cache=self._hash_rows();key=(cache['key'] if cache else None,self.organization.version())
        cached=getattr(self,'_membership_cache',None)
        if cached and cached[0]==key:return cached[1]
        members={}
        for stack in self.stacks()['stacks']:
            for c in stack['contents']:members[c]={'id':stack['id'],'count':stack['count'],'top':c==stack['top'],'confirmed':stack['confirmed'],'collapse':stack['collapse']}
        self._membership_cache=(key,members)
        return members

    def _face_rows(self):
        """Current-model face observations for verified photos and video samples, cached until the store grows."""
        path=self.catalog_directory/'faces.db'
        if not path.is_file():return None
        current={i['content_hash']:i for i in self.items if i['content_hash'] and i['kind'] in ('photo','video')}
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            row=conn.execute("SELECT value FROM settings WHERE key='face_groups_current'").fetchone()
            if not row:return None
            model=json.loads(row[0])['model']
            count=conn.execute('SELECT COUNT(*) FROM face_observations WHERE model=?',(model,)).fetchone()[0]
            cache=getattr(self,'_face_cache',None)
            if cache and cache['key']==(model,count,len(current)):return cache
            columns={r[1] for r in conn.execute('PRAGMA table_info(face_work)')}
            work=conn.execute('SELECT content_hash,kind FROM face_work WHERE model=?' if 'kind' in columns else 'SELECT content_hash,NULL FROM face_work WHERE model=?',(model,))
            # Work done under the wrong kind (a video that passed as a photo until verified) does not count as processed.
            processed=sum(h in current and (kind or 'photo')==current[h]['kind'] for h,kind in work)
            from .faces import unit
            def sample_time(span):
                data=json.loads(span) if span else {}
                return data.get('timestamp') if 'frame_file' in data else None
            rows=[{'face_id':r[0],'content_hash':r[1],'vector':unit(json.loads(r[2])),'box':json.loads(r[3]),'item_id':current[r[1]]['id'],'timestamp':sample_time(r[4])} for r in conn.execute('SELECT face_id,content_hash,vector_json,box_json,source_span FROM face_observations WHERE model=?',(model,)) if r[1] in current]
        self._face_cache={'key':(model,count,len(current)),'model':model,'rows':rows,'processed':processed,'groups':{}}
        return self._face_cache

    def face_review(self,ignored=False,sensitivity='balanced'):
        """Unnamed review groups at the chosen sensitivity, with named-person hints.

Groups are computed live over every current observation and cached per
sensitivity, so naming one group never reshuffles the others. Confirmed and
ignored faces are filtered out afterwards; a group whose centroid resembles a
named person's confirmed faces carries that person as a hint, never a label."""
        from .faces import SENSITIVITY, cluster, unit
        if sensitivity not in SENSITIVITY:raise ValueError('Unknown grouping sensitivity')
        cache=self._face_rows()
        if cache is None:return {'groups':[],'ready':False,'sensitivity':sensitivity}
        rows=cache['rows'];by_id={r['face_id']:r for r in rows};state=self.organization.read()
        if sensitivity not in cache['groups']:cache['groups'][sensitivity]=cluster(rows,SENSITIVITY[sensitivity])
        available=set(by_id);confirmed=available&set(state['faces']);hidden=available&state['ignored_faces']
        pending=hidden if ignored else available-confirmed-hidden
        import numpy as np
        exemplars={}
        for face_id in confirmed:exemplars.setdefault(state['faces'][face_id]['person'],[]).append(by_id[face_id]['vector'])
        people={p:np.mean(np.asarray(v,dtype=np.float32),axis=0) for p,v in exemplars.items() if p in state['people'] and p not in state['hidden_people']}
        for p,v in people.items():people[p]=v/np.linalg.norm(v)
        groups=[]
        for group in cache['groups'][sensitivity]:
            ids=[f for f in group['faces'] if f in pending]
            if not ids:continue
            hint=None
            if people:
                centroid=np.asarray(group['centroid'],dtype=np.float32)
                person,score=max(((p,float(v@centroid)) for p,v in people.items()),key=lambda t:t[1])
                if score>=SENSITIVITY[sensitivity]:hint={**state['people'][person],'similarity':round(score,3)}
            groups.append({'id':group['id'],'faces':[{'face_id':f,'content_hash':by_id[f]['content_hash'],'item_id':by_id[f]['item_id']} for f in ids],'looks_like':hint})
        groups.sort(key=lambda g:(-len(g['faces']),g['id']))
        return {'groups':groups,'ready':True,'sensitivity':sensitivity,'threshold':SENSITIVITY[sensitivity],
                'processed_photos':cache['processed'],'candidate_photos':sum(i['kind'] in ('photo','video') and bool(i['content_hash']) for i in self.items),
                'faces':len(rows),'confirmed':len(confirmed),'ignored':len(hidden),'single_groups':sum(len(g['faces'])==1 for g in groups)}

    def photo_faces(self,key):
        """Every detected face on one photo or video with its owner state: named, ignored or still unreviewed."""
        item=self.by_id[key];cache=self._face_rows()
        if cache is None or not item['content_hash']:return {'faces':[],'ready':cache is not None}
        state=self.organization.read();faces=[]
        for row in cache['rows']:
            if row['content_hash']!=item['content_hash']:continue
            owner=state['faces'].get(row['face_id']);person=state['people'].get(owner['person']) if owner else None
            faces.append({'face_id':row['face_id'],'content_hash':row['content_hash'],'person':person,'ignored':row['face_id'] in state['ignored_faces'],'cover':bool(person) and state['covers'].get(person['id'])==row['face_id'],'box':row.get('box'),'timestamp':row.get('timestamp')})
        return {'faces':faces,'ready':True}

    def face_preview(self,face_id):
        path=self.catalog_directory/'faces.db'
        if not path.is_file():raise FileNotFoundError()
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            row=conn.execute('SELECT content_hash,box_json,source_span FROM face_observations WHERE face_id=?',(face_id,)).fetchone()
        if row is None or row[0] not in self.by_content:raise FileNotFoundError()
        span=json.loads(row[2]) if row[2] else {}
        face={'item_id':self.by_content[row[0]]['id'],'box':json.loads(row[1]),'sample':span.get('frame_file')}
        target=self.thumbnails/('face-v2-'+face_id+'.jpg')
        if target.is_file():return target
        from .vision import read_image
        self.thumbnails.mkdir(parents=True,exist_ok=True)
        with THUMBNAIL_WORKERS:
            if face['sample']:
                # A video face was detected on a retained sample, so crop that sample instead of decoding the video.
                sample=self.catalog_directory/'video-samples'/face['sample']
                if not sample.is_file():raise FileNotFoundError()
                from PIL import Image
                with Image.open(sample) as opened:image=opened.convert('RGB')
            else:
                for row in self.sources_by_id[face['item_id']]:
                    try:image=read_image(row);break
                    except (OSError,ValueError,RuntimeError,StopIteration,zipfile.BadZipFile):continue
                else:raise FileNotFoundError()
            try:
                # Square crop with breathing room around the detector box, so chips show a face rather than a tight mask.
                x0,y0,x1,y1=face['box'];w,h=image.size
                side=max((x1-x0)*w,(y1-y0)*h)*1.8;cx=(x0+x1)/2*w;cy=(y0+y1)/2*h
                left=min(max(0,cx-side/2),max(0,w-side));top=min(max(0,cy-side/2),max(0,h-side))
                crop=image.crop((int(left),int(top),int(min(w,left+side)),int(min(h,top+side))))
                crop.thumbnail((240,240));temporary=self.thumbnails/(uuid.uuid4().hex+'.jpg')
                crop.save(temporary,format='JPEG',quality=85);crop.close();temporary.replace(target)
            finally:image.close()
            return target

    def item(self,key):
        """One item as the grid would show it now: people, buckets, favorite and hidden flags included."""
        rows=self._per_state('rows_by_id',lambda:{i['id']:i for i in self._select()})
        return rows.get(key) or self.by_id[key]

    def gaps(self,kind,group='',offset=0,limit=60):
        """Evidence-backed proposals for files missing a date or a location, grouped by the rule that produced them; see src/gaps.py."""
        from .gaps import proposals,page
        if kind not in ('date','location'):raise ValueError('Unknown gap')
        data=self._per_state(('gaps',kind),lambda:proposals(self,kind))
        return page(data,group,offset,limit)

    def fill_gaps(self,kind,group,action,contents=None,skip=()):
        """Accept a group's proposals (ordinary set_date / set_location events), mark items as not expected, or restore them. Only current proposals can be accepted."""
        from .gaps import proposals,events_for
        if kind not in ('date','location'):raise ValueError('Unknown gap')
        if action not in ('accept','dismiss','restore'):raise ValueError('Unknown gap action')
        data=self._per_state(('gaps',kind),lambda:proposals(self,kind))
        chosen=next((g for g in data['groups'] if g['id']==group),None)
        if chosen is None:raise ValueError('Unknown gap group')
        rows=[r for r in chosen['items'] if (contents is None or r['content_hash'] in contents) and r['content_hash'] not in set(skip)]
        if not rows:raise ValueError('Nothing left to fill')
        written=0
        for op,payload in events_for(kind,action,rows):
            self.organization.append(op,payload);written+=1
        self._apply_dates()
        with self.visual_lock:self.visual_results.clear()
        return {'saved':True,'items':len(rows),'events':written}

    def trip_paths(self,archived=False,limit=80):
        """Where each trip went: recorded coordinates in time order, thinned to at most 40 points, for saved trips and the first `limit` suggestions."""
        return self._per_state(('trip_paths',archived,limit),lambda:self._trip_paths(archived,limit))

    def _trip_paths(self,archived,limit):
        from datetime import date
        data=self.trips(archived);rows=list(data['trips'])+list(data['proposals'][:limit])
        located=[i for i in self.items if i.get('location') and i.get('day') and i.get('content_hash')]
        excluded=self.organization.read().get('trip_exclusions',{})
        paths={}
        for trip in rows:
            places=set(trip['place'].split(',')) if trip.get('place') else None;out=set(excluded.get(trip['id'],()) or ())
            hits=[i for i in located if trip['after']<=i['day']<=trip['before'] and (places is None or self.place_key(i) in places) and i['content_hash'] not in out]
            hits.sort(key=lambda i:((i['date'] or {}).get('value') or i['day'],i['name']))
            points=[]
            for i in hits:
                point=[round(i['location']['lat'],4),round(i['location']['lon'],4)]
                if not points or points[-1]!=point:points.append(point)
            if len(points)>40:
                step=(len(points)-1)/39;points=[points[round(k*step)] for k in range(40)]
            # Where the trip was left from and returned to: the nearest located photo within 30 days on either side, when it sits somewhere else.
            legs={}
            if points:
                for name,rows,edge in (('from',[i for i in located if i['day']<trip['after'] and (date.fromisoformat(trip['after'])-date.fromisoformat(i['day'])).days<=30],points[0]),('back',[i for i in located if i['day']>trip['before'] and (date.fromisoformat(i['day'])-date.fromisoformat(trip['before'])).days<=30],points[-1])):
                    rows.sort(key=lambda i:((i['date'] or {}).get('value') or i['day'],i['name']),reverse=name=='from')
                    if rows:
                        point=[round(rows[0]['location']['lat'],4),round(rows[0]['location']['lon'],4)]
                        if abs(point[0]-edge[0])>0.2 or abs(point[1]-edge[1])>0.2:legs[name]=point
            paths[trip['id']]={'points':points,'located':len(hits),'days':len({i['day'] for i in hits}),'from':legs.get('from'),'back':legs.get('back')}
        return {'paths':paths,'meaning':'Lines join recorded coordinates in time order; photos without a location are not drawn. Dashed legs reach the nearest located photo within 30 days before and after the trip when it was taken somewhere else.'}

    def _per_state(self,name,build,ttl=20):
        """Memoize a view of the organization state; refreshed when the state object changes or after `ttl` seconds, so enrichment jobs still show up."""
        import time
        state=self.organization.read();now=time.monotonic()
        with self._memo_lock:
            hit=self._memo.get(name)
            if hit and hit[0] is state and now-hit[1]<ttl:return hit[2]
        value=build()
        with self._memo_lock:self._memo[name]=(state,now,value)
        return value

    def people(self):
        return self._per_state('people',self._people)

    def _people(self):
        state=self.organization.read();groups=[]
        faces={}
        for face_id,value in sorted(state['faces'].items()):faces.setdefault(value['person'],[]).append(face_id)
        members={}
        for item in self.items:
            for person in state['tags'].get(item['content_hash'],()):members.setdefault(person,[]).append(item)
        for person in state['people'].values():
            mine=members.get(person['id'],[])
            own=faces.get(person['id'],[]);cover=state['covers'].get(person['id'])
            groups.append({**person,'count':len(mine),'cover':mine[0]['id'] if mine else None,'face':cover if cover in own else (own or [None])[0],'cover_face':cover if cover in own else None,'confirmed_faces':len(own),'hidden':person['id'] in state['hidden_people']})
        return {'people':sorted(groups,key=lambda p:p['name'].casefold()),
                'untagged':sum(i['kind'] in ('photo','video') and not state['tags'].get(i['content_hash']) for i in self.items),
                'automatic_grouping':self._face_rows() is not None}

    def places(self):
        return self._per_state('places',self._places)

    def _places(self):
        names=self.organization.read()['places'];groups={}
        for item in self.items:
            key=self.place_key(item)
            if key is None:continue
            if key not in groups:
                groups[key]={'id':key,'name':names.get(key,''),'count':0,'cover':item['id'],
                             'lat':0,'lon':0,'first':None,'last':None}
            group=groups[key];group['count']+=1
            group['lat']+=item['location']['lat'];group['lon']+=item['location']['lon']
            if item['day']:
                group['first']=min(group['first'] or item['day'],item['day'])
                group['last']=max(group['last'] or item['day'],item['day'])
        for group in groups.values():
            group['lat']/=group['count'];group['lon']/=group['count']
            group['geographic']=self.geographic_names.get(group['id'])
            if not group['name'] and group['geographic']:group['name']='Near '+group['geographic']['name']
        return {'places':sorted(groups.values(),key=lambda p:(-p['count'],p['id'])),'attribution':'GeoNames, CC BY 4.0. Offline nearest-settlement lookup.' if self.geographic_names else None,
                'missing':sum(not i['location'] for i in self.items),
                'meaning':'Recorded coordinates grouped into 0.1 degree areas, about 11 km north to south. Not city boundaries.'}

    def albums(self,archived=False):
        state=self.organization.read();rows=[]
        for album in state['albums'].values():
            if (album['id'] in state['archived_albums'])!=archived:continue
            items=[self.by_content[d] for d in album['contents'] if d in self.by_content]
            rows.append({**album,'count':len(items),'missing':len(album['contents'])-len(items),'cover':items[0]['id'] if items else None})
        return {'albums':sorted(rows,key=lambda a:(a['name'].casefold(),a['id'])),'archived':archived}

    def buckets(self,archived=False):
        """Owner-managed collections. Membership grows by explicit add/remove; nothing is inferred."""
        state=self.organization.read();rows=[]
        for bucket in state['buckets'].values():
            if (bucket['id'] in state['archived_buckets'])!=archived:continue
            items=[self.by_content[d] for d in bucket['contents'] if d in self.by_content and d not in state['hidden']]
            rows.append({'id':bucket['id'],'name':bucket['name'],'count':len(items),'photos':sum(i['kind']=='photo' for i in items),'videos':sum(i['kind']=='video' for i in items),
                         'missing':len(bucket['contents'])-len(items),'cover':items[-1]['id'] if items else None})
        return {'buckets':sorted(rows,key=lambda b:(b['name'].casefold(),b['id'])),'archived':archived,'meaning':'You choose what goes in each bucket. Members can be photos or videos and one item can sit in many buckets.'}

    def album_items(self,key):
        album=self.organization.read()['albums'].get(key)
        if album is None:raise ValueError('Unknown album')
        items=[self.by_content[d] for d in album['contents'] if d in self.by_content]
        return {'album':album,'items':items,'total':len(items),'missing':len(album['contents'])-len(items)}

    def highlights(self,mode='metadata',query='',category='',kind='all',year='',after='',before='',person='',place='',limit=30,trip=''):
        if not 1<=limit<=200:raise ValueError('Choose 1 to 200 highlights')
        filters=dict(query=query,kind=kind,year=year,after=after,before=before,person=person,place=place,trip=trip)
        if mode in ('metadata','everything'):selected=self._select(**filters);scope='All photos matching these filters.'
        elif mode in ('visual','descriptions'):
            if mode=='visual' and not query.strip():selected=self._select(**filters);scope='All photos matching these filters.'
            else:
                result=self.visual(**filters,limit=200) if mode=='visual' else self.described(**filters,category=category,limit=200)
                selected=result['items'];scope='Up to 200 search results; AI matches and analysis coverage may be incomplete.'
        else:raise ValueError('Highlights require a photo search')
        selected=[i for i in selected if i['kind']=='photo' and i['content_hash']]
        selected.sort(key=lambda i:(i['day'] is None,i['day'] or '',i['id']))
        # Sample across ordered positions; never imply an aesthetic quality score.
        count=min(limit,len(selected))
        indices=[round(i*(len(selected)-1)/(count-1)) for i in range(count)] if count>1 else ([0] if count else [])
        return {'items':[selected[i] for i in indices],'total':len(selected),'coverage':scope,'meaning':'Evenly sampled across the timeline, oldest first and undated last. Review the selection; no best-photo ranking is implied.'}

    def trips(self,archived=False):
        return self._per_state(('trips',archived),lambda:self._trips(archived))

    def _trips(self,archived=False):
        from .trips import proposals
        places=self.places()['places'];labels={p['id']:p['name'] or 'Recorded place' for p in places}
        saved=[];state=self.organization.read()
        for trip in state['trips'].values():
            if (trip['id'] in state['archived_trips'])!=archived:continue
            matching=self._select(trip=trip['id'])
            saved.append({**trip,'count':len(matching),'cover':matching[0]['id'] if matching else None})
        proposed=proposals(self.items,self.place_key,labels)
        # A suggestion whose dates and area already sit inside a saved trip is that trip, so it leaves the pile.
        def covered(p):return any(t['after']<=p['after'] and p['before']<=t['before'] and (not t['place'] or p['place'] in t['place'].split(',')) for t in state['trips'].values())
        return {'trips':sorted(saved,key=lambda t:(t['after'],t['id']),reverse=True),
                'proposals':[] if archived else [p for p in proposed if not covered(p)],
                'meaning':'Suggested visits group at least three files in one recorded area, with gaps of at most two days and spans of at most fourteen days. They may be everyday photos. Review dates and places before saving as a trip.'}

    def trip_exclusions(self,key):
        state=self.organization.read()
        if key not in state['trips']:raise ValueError('Unknown trip')
        hashes=state['trip_exclusions'].get(key,set())
        rows=[self.by_content[h] for h in sorted(hashes) if h in self.by_content]
        return {'items':rows[:200],'total':len(hashes),'missing':len(hashes)-len(rows)}

    def transcript(self,key):
        item=self.by_id[key];path=self.catalog_directory/'transcripts.db'
        if not path.is_file():return None
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row;model=conn.execute("SELECT value FROM settings WHERE key='transcripts_current'").fetchone()
            if not model:return None
            work=conn.execute('SELECT status,segments FROM transcript_work WHERE content_hash=? AND model=?',(item['content_hash'],model[0])).fetchone()
            if not work:return None
            segments=[dict(r) for r in conn.execute('SELECT * FROM transcript_facts WHERE content_hash=? AND model=? ORDER BY start_seconds',(item['content_hash'],model[0]))]
            return {'status':work['status'],'segments':segments,'model':model[0]}

    def moments(self,query='',year='',after='',before='',offset=0,limit=80,person='',place='',trip=''):
        if len(query)>300 or offset<0 or not 1<=limit<=200:raise ValueError('Invalid moment query')
        path=self.catalog_directory/'transcripts.db'
        if not path.is_file():return {'items':[],'total':0,'next_offset':None,'processed_videos':0}
        allowed={i['content_hash']:i for i in self._select('',kind='video',year=year,after=after,before=before,person=person,place=place,trip=trip)};terms=query.casefold().split();results=[]
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row;model=conn.execute("SELECT value FROM settings WHERE key='transcripts_current'").fetchone()
            if not model:return {'items':[],'total':0,'next_offset':None,'processed_videos':0}
            for row in conn.execute('SELECT content_hash,start_seconds,end_seconds,text FROM transcript_facts WHERE model=? ORDER BY content_hash,start_seconds',(model[0],)):
                if row['content_hash'] in allowed and all(t in row['text'].casefold() for t in terms):results.append({**allowed[row['content_hash']],'moment':{'start':row['start_seconds'],'end':row['end_seconds'],'text':row['text']}})
            processed=conn.execute('SELECT count(*) FROM transcript_work WHERE model=?',(model[0],)).fetchone()[0]
        return {'items':results[offset:offset+limit],'total':len(results),'next_offset':offset+limit if offset+limit<len(results) else None,'processed_videos':processed}

    def descriptions(self):
        path=self.catalog_directory/'descriptions.db'
        if not path.is_file():return {}
        if time.monotonic()-self.description_checked<60:return self.description_rows
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            from .descriptions import accepted_models
            models=accepted_models(conn)
            if models:
                values={}
                for fact in conn.execute('SELECT * FROM description_facts WHERE model IN ('+','.join('?' for _ in models)+') ORDER BY derived_at,model',models):
                    value=dict(fact);value['description']=json.loads(value.pop('value_json'));values[value['content_hash']]=value
                self.description_rows=values
        self.description_checked=time.monotonic();return self.description_rows

    def reviewed_search(self,items,query,mode):
        reviews=self.organization.read()['search_reviews'];query=' '.join(query.casefold().split())
        rows=[{**i,'search_verdict':reviews.get((mode,query,i.get('frame_id') or i['content_hash']))} for i in items]
        # Preserve model order within each owner-review bucket. Never hide results.
        rows.sort(key=lambda i:{'match':0,None:1,'mismatch':2}[i['search_verdict']])
        return rows

    def video_moments(self,query='',year='',after='',before='',offset=0,limit=80,person='',place='',trip=''):
        from .scenes import observations
        from .vision import SearchIndex
        if offset<0 or not 1<=limit<=200:raise ValueError('Invalid pagination')
        path=self.catalog_directory/'scenes.db'
        if self.encoder is None or not path.is_file():raise RuntimeError('Video image search is not ready')
        eligible={i['content_hash']:i for i in self._select('', 'video',year,after,before,person=person,place=place,trip=trip)}
        rows,_=observations(self.catalog_directory/'frames.db');allowed={r['frame_id']:r for r in rows if r['content_hash'] in eligible}
        if not self.visual_lock.acquire(blocking=False):raise RuntimeError('Image search is busy')
        try:
            if not hasattr(self,'scene_index'):self.scene_index=SearchIndex(path,self.encoder)
            ranked=self.scene_index.search(query,limit=200,allowed=set(allowed))
        finally:self.visual_lock.release()
        items=[]
        for match in ranked['results']:
            row=allowed[match['content_hash']];item=eligible[row['content_hash']]
            items.append({**item,'similarity':match['similarity'],'frame_id':row['frame_id'],'moment':{'start':row['timestamp'],'end':row['timestamp'],'text':'Sampled video image'},'sample_interval':row['interval']})
        items=self.reviewed_search(items,query,'video_images')
        return {'items':items[offset:offset+limit],'total':len(items),'next_offset':offset+limit if offset+limit<len(items) else None,'indexed_frames':ranked['indexed_contents'],'eligible_frames':ranked['eligible_contents'],'sampled_videos':len({r['content_hash'] for r in rows}),'meaning':'Closest sampled video images, not confirmed object matches. Up to 60 keyframe samples per video, at least five seconds apart; brief appearances can be missed.'}

    def frame_preview(self,key):
        from .scenes import observations,sample_path
        rows,_=observations(self.catalog_directory/'frames.db')
        row=next((r for r in rows if r['frame_id']==key and r['content_hash'] in self.by_content),None)
        if row is None:raise FileNotFoundError('Unavailable video sample')
        return sample_path(self.catalog_directory,row)

    def described(self,query='',category='',kind='all',year='',after='',before='',offset=0,limit=80,person='',place='',trip=''):
        from .descriptions import matches,CATEGORIES
        if len(query)>300 or offset<0 or not 1<=limit<=200 or (category and category not in CATEGORIES):raise ValueError('Invalid description query')
        facts=self.descriptions();eligible=[i for i in self._select('',kind,year,after,before,person=person,place=place,trip=trip) if i['kind']=='photo']
        indexed=[i for i in eligible if i['content_hash'] in facts]
        selected=[{**i,'ai_description':facts[i['content_hash']]['description']} for i in indexed if (not category or facts[i['content_hash']]['description']['category']==category) and matches(facts[i['content_hash']]['description'],query)]
        selected=self.reviewed_search(selected,query,'descriptions')
        return {'items':selected[offset:offset+limit],'total':len(selected),'next_offset':offset+limit if offset+limit<len(selected) else None,'indexed_contents':len(indexed),'eligible_contents':len(eligible),'meaning':'AI descriptions and tags may be wrong or incomplete. Only analyzed photos are searched.'}

    def ocr_search_text(self):
        path=self.catalog_directory/'ocr.db'
        if not path.is_file():return {}
        if time.monotonic()-self.ocr_checked<60:return self.ocr_texts
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            row=conn.execute("SELECT value FROM settings WHERE key='ocr_current'").fetchone()
            if row:self.ocr_texts={r[0]:r[1].casefold() for r in conn.execute('SELECT content_hash,text FROM ocr_facts WHERE model=?',(row[0],))}
        self.ocr_checked=time.monotonic()
        return self.ocr_texts

    def ocr_details(self,digest):
        path=self.catalog_directory/'ocr.db'
        if not path.is_file():return None
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            model=conn.execute("SELECT value FROM settings WHERE key='ocr_current'").fetchone()
            if not model:return None
            row=conn.execute('SELECT * FROM ocr_facts WHERE content_hash=? AND model=?',(digest,model[0])).fetchone()
        if not row:return None
        value=dict(row);value['recognition']=json.loads(value.pop('words_json'));return value

    def _select(self,query='',kind='all',year='',after='',before='',near='',person='',place='',trip='',ocr=True,stack='',bucket=''):
        if len(query)>300:raise ValueError('Search is too long')
        state=self.organization.read();key=(query,kind,year,after,before,near,person,place,trip,ocr,stack,bucket)
        with self._memo_lock:
            cache=self._select_cache
            if cache.get('state') is not state:cache.clear();cache['state']=state;cache['rows']={}
            hit=cache['rows'].get(key)
            if hit is not None:return hit
        selected=self._select_rows(state,*key)
        with self._memo_lock:
            if cache.get('state') is state:
                rows=cache['rows']
                if len(rows)>=12:rows.pop(next(iter(rows)))
                rows[key]=selected
        return selected

    def _select_rows(self,state,query,kind,year,after,before,near,person,place,trip,ocr,stack,bucket):
        terms=query.casefold().split();selected=[]
        if bucket and bucket not in state['buckets']:raise ValueError('Unknown bucket')
        in_bucket=set(state['buckets'][bucket]['contents']) if bucket else None
        bucket_index={}
        for b in state['buckets'].values():
            if b['id'] in state['archived_buckets']:continue
            for digest in b['contents']:bucket_index.setdefault(digest,[]).append(b)
        # stack='' collapses every stack to its top; 'all' shows every member; a stack id shows that stack alone.
        membership=self._stack_membership() if stack!='all' or kind=='stacks' else {}
        chosen=state['trips'].get(trip) if trip else None
        if trip and chosen is None:raise ValueError('Unknown trip')
        recognized=self.ocr_search_text() if terms and ocr else {}
        favorites=state['favorites'];hidden=state['hidden']
        texts=self._text_index(state) if terms else None
        for item in super()._select('','all' if kind in ('date_review','stacks','favorites','hidden') else kind,year,after,before,near):
            # Hidden items leave every view except the Hidden review; favourites are a plain filter.
            if kind=='hidden':
                if item['content_hash'] not in hidden:continue
            elif item['content_hash'] in hidden:continue
            if kind=='favorites' and item['content_hash'] not in favorites:continue
            if in_bucket is not None and item['content_hash'] not in in_bucket:continue
            item_buckets=bucket_index.get(item['content_hash'],[])
            if kind=='date_review' and (item.get('date_status') not in ('conflict','timezone_unknown','invalid') or item.get('date_confirmed')):continue
            member=membership.get(item['content_hash'])
            if stack and stack!='all':
                if not member or member['id']!=stack:continue
            elif kind=='stacks' and not (member and member['top'] and member['collapse']):continue
            elif not stack and member and member['collapse'] and not member['top']:continue
            people=[state['people'][p] for p in sorted(state['tags'].get(item['content_hash'],set())) if p in state['people']]
            if person=='untagged' and (people or item['kind'] not in ('photo','video')):continue
            if person and person!='untagged' and not any(p['id']==person for p in people):continue
            key=self.place_key(item)
            if chosen and (not item['day'] or not chosen['after']<=item['day']<=chosen['before'] or (chosen['place'] and key not in chosen['place'].split(',')) or item['content_hash'] in state['trip_exclusions'].get(trip,set())):continue
            if place and key not in place.split(','):continue
            name=state['places'].get(key,'')
            if terms:
                searchable=texts[item['id']]+' '+recognized.get(item['content_hash'],'').casefold()
                if not all(term in searchable for term in terms):continue
            selected.append({**item,'people':people,'place_name':name,'stack':member,'favorite':item['content_hash'] in favorites,'hidden':item['content_hash'] in hidden,'buckets':[b['id'] for b in item_buckets]})
        return selected

    def _text_index(self,state):
        """Item id -> casefolded text a search can match, minus recognized text (kept separate).

Built once per organization state and reused by every query until a correction lands; the
per-item string work dominated search time when it ran on every request."""
        cached=getattr(self,'_text_cache',None)
        if cached and cached[0] is state:return cached[1]
        trips=[t for t in state['trips'].values() if t['id'] not in state['archived_trips']]
        buckets={}
        for b in state['buckets'].values():
            if b['id'] in state['archived_buckets']:continue
            for digest in b['contents']:buckets.setdefault(digest,[]).append(b['name'])
        geo_text={key:' '.join(str(geo.get(k,'')) for k in ('name','region','country')) for key,geo in self.geographic_names.items()}
        texts={}
        for item in self.items:
            digest=item['content_hash'];key=self.place_key(item);day=item['day']
            people=' '.join(state['people'][p]['name'] for p in state['tags'].get(digest,()) if p in state['people'])
            trip_names=' '.join(t['name'] for t in trips if day and t['after']<=day<=t['before'] and (not t['place'] or t['place']==key) and digest not in state['trip_exclusions'].get(t['id'],set())) if day else ''
            texts[item['id']]=f"{item['name']} {item['camera'] or ''} {' '.join(item.get('keywords') or [])} {item.get('caption') or ''} {item.get('title') or ''} {day or ''} {state['places'].get(key,'')} {geo_text.get(key,'')} {trip_names} {people} {' '.join(buckets.get(digest,()))}".casefold()
        self._text_cache=(state,texts)
        return texts

    def everything(self,query='',limit=12,year='',after='',before=''):
        """One search fanned out across every kind of thing the library knows, grouped by kind.

Owner names and recorded facts come first; text read from photos, AI descriptions and
spoken words are model output and say so. Image similarity is not fanned out: it is
slow and unverified, so the UI offers it as a separate step."""
        query=' '.join(query.split());terms=query.casefold().split()
        if not terms or len(query)>300:raise ValueError('Type a search')
        if not 1<=limit<=50:raise ValueError('Invalid limit')
        def hit(text):return all(t in (text or '').casefold() for t in terms)
        people=[p for p in self.people()['people'] if hit(p['name'])]
        places=[p for p in self.places()['places'] if hit(p['name']+' '+' '.join(str((p.get('geographic') or {}).get(k,'')) for k in ('name','region','country')))]
        trips=[t for t in self.trips()['trips'] if hit(t['name'])]
        albums=[a for a in self.albums()['albums'] if hit(a['name'])]
        buckets=[b for b in self.buckets()['buckets'] if hit(b['name'])]
        files=self.search(query=query,limit=limit,year=year,after=after,before=before,ocr=False)
        dated={i['content_hash'] for i in self._select('','all',year,after,before)}
        recognized=self.ocr_search_text()
        text=[self.by_content[h] for h,t in recognized.items() if h in self.by_content and h in dated and hit(t)]
        text.sort(key=lambda i:(i['day'] or '',i['name']),reverse=True)
        described=self.described(query=query,limit=limit,year=year,after=after,before=before) if self.descriptions() else None
        moments=self.moments(query=query,limit=limit,year=year,after=after,before=before)
        return {'query':query,
                'people':{'items':people[:limit],'total':len(people)},
                'places':{'items':places[:limit],'total':len(places)},
                'trips':{'items':trips[:limit],'total':len(trips)},
                'albums':{'items':albums[:limit],'total':len(albums)},
                'buckets':{'items':buckets[:limit],'total':len(buckets)},
                'dates':self._date_hits(query,year,after,before),
                'files':{'items':files['items'],'total':files['total'],'meaning':'File names, cameras, dates, and named people, places and trips.'},
                'text':{'items':text[:limit],'total':len(text),'meaning':'Text recognized in photos by a local model; it can misread.'},
                'descriptions':{'items':described['items'],'total':described['total'],'indexed':described['indexed_contents'],'meaning':described['meaning']} if described else {'items':[],'total':0,'indexed':0,'meaning':'No AI descriptions yet.'},
                'moments':{'items':moments['items'],'total':moments['total'],'processed_videos':moments['processed_videos'],'meaning':'Spoken words from local transcripts; they contain mistakes.'}}

    def suggest(self,query='',limit=6):
        """Typed completions for the search box: what the library can filter on, grouped by kind.

Each suggestion carries the filter it applies, so choosing one narrows the grid instead
of running a text search. AI object tags are model output and say so."""
        query=' '.join(query.split());q=query.casefold()
        if len(query)>300 or not 1<=limit<=20:raise ValueError('Invalid suggestion query')
        def hit(text):return not q or all(t in (text or '').casefold() for t in q.split())
        def starts(text):return (text or '').casefold().startswith(q)
        def rank(rows,key):return sorted(rows,key=lambda r:(not starts(key(r)),key(r).casefold()))
        people=[{'kind':'person','id':p['id'],'name':p['name'],'count':p['count'],'face':p['face'],'hidden':p['hidden']} for p in self.people()['people'] if hit(p['name'])]
        places=[{'kind':'place','id':p['id'],'name':p['name'] or p['id'],'count':p['count'],'cover':p['cover'],'region':' · '.join(str((p.get('geographic') or {}).get(k,'')) for k in ('region','country') if (p.get('geographic') or {}).get(k))} for p in self.places()['places'] if hit((p['name'] or '')+' '+' '.join(str((p.get('geographic') or {}).get(k,'')) for k in ('name','region','country')))]
        trips_data=self.trips();trips=[{'kind':'trip','id':t['id'],'name':t['name'],'count':t['count'],'after':t['after'],'before':t['before'],'place':t['place'],'cover':t['cover'],'saved':True} for t in trips_data['trips'] if hit(t['name'])]
        albums=[{'kind':'album','id':a['id'],'name':a['name'],'count':a['count']} for a in self.albums()['albums'] if hit(a['name'])]
        buckets=[{'kind':'bucket','id':b['id'],'name':b['name'],'count':b['count'],'cover':b['cover']} for b in self.buckets()['buckets'] if hit(b['name'])]
        kinds=[{'kind':'filter','id':k,'name':n} for k,n in (('photo','Photos only'),('video','Videos only'),('favorites','Favorites'),('hidden','Hidden photos'),('duplicates','Exact duplicates'),('stacks','Similar stacks'),('unknown','Missing location'),('undated','Undated'),('date_review','Dates needing review')) if hit(n) or (q and q in k)]
        tags=[]
        facts=self.descriptions()
        if facts and q:
            counts={}
            for value in facts.values():
                for obj in value['description'].get('objects',[]):
                    if hit(obj['name']) or (' ' in q and hit(obj['color']+' '+obj['name'])):counts[obj['name']]=counts.get(obj['name'],0)+1
            tags=[{'kind':'tag','id':name,'name':name,'count':count} for name,count in sorted(counts.items(),key=lambda t:(-t[1],t[0]))]
        dates=self._date_hits(query)['items'] if q else []
        return {'query':query,
                'people':rank(people,lambda r:r['name'])[:limit],'places':rank(places,lambda r:r['name'])[:limit],'trips':rank(trips,lambda r:r['name'])[:limit],
                'albums':rank(albums,lambda r:r['name'])[:limit],'buckets':rank(buckets,lambda r:r['name'])[:limit],'dates':[{'kind':'date',**d} for d in dates[:limit]],'filters':kinds[:limit],
                'tags':tags[:limit],'tags_meaning':'Objects an AI model reported seeing; it can be wrong.','text':query,
                'meaning':'Pick a suggestion to filter, or press Enter to search file names, text in photos, descriptions and spoken words for these words.'}

    def _date_hits(self,query,year='',after='',before=''):
        """A query that reads as a year, a month or a day becomes a jump into the timeline."""
        q=query.casefold().strip();months=['january','february','march','april','may','june','july','august','september','october','november','december']
        target=None
        if re.fullmatch(r'(19|20)\d{2}',q):target=('year',q)
        elif re.fullmatch(r'(19|20)\d{2}-\d{2}',q):target=('month',q)
        elif re.fullmatch(r'(19|20)\d{2}-\d{2}-\d{2}',q):target=('day',q)
        else:
            m=re.fullmatch(r'([a-z]+)\.? ((?:19|20)\d{2})',q) or re.fullmatch(r'((?:19|20)\d{2}) ([a-z]+)\.?',q)
            if m:
                word,yr=(m.group(1),m.group(2)) if not m.group(1).isdigit() else (m.group(2),m.group(1))
                index=next((i for i,name in enumerate(months) if name.startswith(word) and len(word)>=3),None)
                if index is not None:target=('month',f'{yr}-{index+1:02d}')
        if not target:return {'items':[],'total':0}
        kind,value=target;timeline=self.timeline(year=year,after=after,before=before)
        if kind=='year':hits=[{'kind':'year','value':y['year'],'count':y['count'],'offset':y['offset']} for y in timeline['years'] if y['year']==value]
        elif kind=='month':hits=[{'kind':'month','value':m['month'],'count':m['count'],'offset':m['offset']} for y in timeline['years'] for m in y['months'] if m['month']==value]
        else:
            items=self._select('','all',year,after,before);first=next((i for i,item in enumerate(items) if item['day']==value),None)
            hits=[{'kind':'day','value':value,'count':sum(i['day']==value for i in items),'offset':first}] if first is not None else []
        return {'items':hits,'total':len(hits)}

    def timeline(self,query='',kind='all',year='',after='',before='',near='',person='',place='',trip=''):
        """Year and month buckets in display order, each with the offset of its first item, so a scrubber can jump into `search` pagination."""
        items=self._select(query,kind,year,after,before,near,person,place,trip)
        years=[];undated={'count':0,'offset':None}
        for index,item in enumerate(items):
            day=item['day']
            if not day:
                if undated['offset'] is None:undated['offset']=index
                undated['count']+=1;continue
            if not years or years[-1]['year']!=day[:4]:years.append({'year':day[:4],'count':0,'offset':index,'months':[]})
            bucket=years[-1];bucket['count']+=1
            if not bucket['months'] or bucket['months'][-1]['month']!=day[:7]:bucket['months'].append({'month':day[:7],'count':0,'offset':index})
            bucket['months'][-1]['count']+=1
        return {'total':len(items),'years':years,'undated':undated,'order':'newest first; undated last'}

    def search(self,query='',kind='all',year='',offset=0,limit=80,after='',before='',near='',person='',place='',trip='',ocr=True,stack='',bucket=''):
        if offset<0 or not 1<=limit<=200:raise ValueError('Invalid pagination')
        items=self._select(query,kind,year,after,before,near,person,place,trip,ocr,stack,bucket)
        return {'items':items[offset:offset+limit],'total':len(items),
                'next_offset':offset+limit if offset+limit<len(items) else None}

    def slideshow(self,query='',year='',after='',before='',near='',located=False,limit=500,kind='all',person='',place='',trip='',bucket=''):
        if not 1<=limit<=500:raise ValueError('Invalid slideshow limit')
        items=[i for i in self._select(query,'located' if located else kind,year,after,before,near,person,place,trip,bucket=bucket) if i['kind']=='photo']
        items.sort(key=lambda i:(i['day'] is None,i['day'] or '',i['id']))
        return {'items':items[:limit],'total':len(items),'truncated':len(items)>limit,
                'coverage':'Current verified library; only photos matching these filters.',
                'order':'oldest first; undated last'}

    def _attach(self,item):
        facts=self.facts.get(item['content_hash'],[])
        for fact in facts:
            value=fact['value']
            source=fact['extractor']+':'+str(value.get('field','embedded')) if isinstance(value,dict) else fact['extractor']
            if fact['attribute']=='date' and isinstance(value,dict):
                current=item['date']
                if current is None or (current.get('meaning')!='capture' and value.get('meaning')=='capture'):
                    item['date']={**value,'source':value.get('source',source)}
                    item['day']=value['value'][:10]
            elif fact['attribute']=='location' and not item['location']:
                item['location']={**value,'source':value.get('source',source)}
            elif fact['attribute']=='raw_embedded':
                item['camera']=item['camera'] or (value.get('exif') or {}).get('Model')
                embedded=value.get('embedded') or {}
                if embedded.get('keywords') and not item.get('keywords'):item['keywords']=embedded['keywords']
                for key in ('caption','title'):
                    if embedded.get(key) and not item.get(key):item[key]=embedded[key]['value']
                if embedded.get('rating') is not None and item.get('rating') is None:item['rating']=embedded['rating']
                item['width']=item['width'] or value.get('width')
                item['height']=item['height'] or value.get('height')
        from .dates import evidence
        item['date_status']=evidence(facts)['status']
        item['metadata_fact_count']=len(facts)
        item['date_inferred']=False
        if item['date'] is None:
            candidates=self.inferred_dates(item)
            if candidates:
                best=candidates[0]
                item['date']={'value':best['value'],'meaning':'capture','field':best['field'],'source':'Inferred from file name','confidence':best['confidence']}
                item['day']=best['value'][:10];item['date_inferred']=True

    def summary(self):
        result={**super().summary(),'source_occurrences':self.source_occurrences,
                'zip_items':len(self.zip_sources),'deduplicated':True,
                'unverified_items':sum(not i['content_hash'] for i in self.items),
                'catalog_scope':'Master entries plus verified ZIP contents. Unverified master copies may still overlap; metadata and indexing coverage remain incomplete.'}
        result['not_expected_date']=sum(not i['day'] and i.get('date_dismissed') for i in self.items)
        result['not_expected_location']=sum(not i['location'] and i.get('location_dismissed') for i in self.items)
        result['missing_date']-=result['not_expected_date'];result['missing_location']-=result['not_expected_location']
        result['owner_dates']=sum(bool(i.get('date_confirmed')) for i in self.items);result['owner_locations']=sum(bool(i.get('location_confirmed')) for i in self.items)
        result['date_review_needed']=sum(i.get('date_status') in ('conflict','timezone_unknown','invalid') and not i.get('date_confirmed') for i in self.items)
        result['generation']=self.generation
        result['capabilities']['video_playback']=True
        result['capabilities']['organization']=True
        result['capabilities']['buckets']=True
        result['capabilities']['faces']=self._face_rows() is not None
        result['capabilities']['stacks']=self._hash_rows() is not None
        result['capabilities']['favorites']=True
        result['capabilities']['video_images']=self.encoder is not None and (self.catalog_directory/'scenes.db').is_file()
        result['capabilities']['transcripts']=(self.catalog_directory/'transcripts.db').is_file()
        result['capabilities']['descriptions']=(self.catalog_directory/'descriptions.db').is_file()
        result['capabilities']['ocr']=(self.catalog_directory/'ocr.db').is_file()
        result['capabilities']['semantic_search']=self.encoder is not None and self.visual_database.is_file()
        return result

    def visual(self,query='',kind='all',year='',after='',before='',offset=0,limit=80,person='',place='',trip=''):
        if self.encoder is None or not self.visual_database.is_file():
            raise RuntimeError('Visual search is not ready')
        if offset<0 or limit<1 or limit>200:
            raise ValueError('Invalid pagination')
        allowed={i['content_hash']:i for i in self._select('',kind,year,after,before,person=person,place=place,trip=trip)
                 if i['kind']=='photo' and i['content_hash']}
        if not self.visual_lock.acquire(blocking=False):
            raise RuntimeError('Another visual query is running')
        try:
            key=(query,kind,year,after,before,person,place,trip)
            cached=self.visual_results.get(key)
            if cached and time.monotonic()-cached[0]<120:
                result=cached[1]
            else:
                from .vision import SearchIndex
                if self.visual_index is None:self.visual_index=SearchIndex(self.visual_database,self.encoder)
                result=self.visual_index.search(query,200,allowed=set(allowed))
                self.visual_results={key:(time.monotonic(),result)}
        finally:
            self.visual_lock.release()
        from .descriptions import matches,COLORS
        words=query.casefold().split();tagged=[]
        if len(words)==2 and words[0] in COLORS:
            tagged=[{**allowed[d],'ai_description':f['description']} for d,f in self.descriptions().items() if d in allowed and matches(f['description'],query)]
        tag_ids={i['content_hash'] for i in tagged}
        ranked=(tagged+[{**allowed[r['content_hash']],'similarity':r['similarity']} for r in result['results'] if r['content_hash'] not in tag_ids])[:200]
        ranked=self.reviewed_search(ranked,query,'visual')
        return {'items':ranked[offset:offset+limit],'total':len(ranked),
                'next_offset':offset+limit if offset+limit<len(ranked) else None,
                'indexed_contents':result['indexed_contents'],'eligible_contents':result['eligible_contents'],
                'ranking_limit':200,'meaning':result['meaning']}

    def metadata(self,key):
        item=self.by_id[key]
        if not item['content_hash']:
            return super().metadata(key)
        return {'content_hash':item['content_hash'],'dates':self.date_review(key),'transcript':self.transcript(key),'detected_type':self.type_facts.get(item['content_hash']),'ai':self.descriptions().get(item['content_hash']),'ocr':self.ocr_details(item['content_hash']),'facts':self.facts.get(item['content_hash'],[]),
                'sources':[{'path':r['source'],'member':r['member'],'size':r['size']} for r in self.sources_by_id[key]],
                'status':'Source claims are preserved separately. Differing values are not automatically resolved.'}

    def thumbnail(self,key):
        if key not in self.zip_sources:
            return super().thumbnail(key)
        target=self.thumbnails/(key+'.jpg')
        if target.is_file() and target.stat().st_size:
            return target
        from .vision import read_image
        with THUMBNAIL_WORKERS:
            if target.is_file() and target.stat().st_size:
                return target
            self.thumbnails.mkdir(parents=True,exist_ok=True)
            if self.by_id[key]['kind']!='photo':
                return self._zip_video_preview(key)
            for row in self.zip_sources[key]:
                try:
                    image=read_image(row,max_side=640,fast=True)
                    staging=self.thumbnails/(key+'.'+uuid.uuid4().hex+'.jpg')
                    try:
                        image.thumbnail((640,640))
                        image.save(staging,format='JPEG',quality=85)
                    finally:
                        image.close()
                    if not target.exists():
                        staging.rename(target)
                    return target
                except (OSError,ValueError,ImportError,zipfile.BadZipFile,StopIteration):
                    continue
        raise RuntimeError('No supported ZIP preview source')

    def _zip_video_preview(self,key):
        """A video inside an archive: copy the member into the preview cache, render one frame
        from that copy, and drop the copy. ffmpeg needs a seekable file (the moov atom often sits
        at the end), and the archive on the external drive is never touched beyond reading."""
        import shutil
        for row in self.zip_sources[key]:
            extracted=self.thumbnails/(key+'.'+uuid.uuid4().hex+PurePosixPath(row['member']).suffix.lower())
            try:
                with zipfile.ZipFile(row['source']) as archive:
                    info=next(i for i in archive.infolist() if i.header_offset==row['offset'])
                    if (info.filename,info.file_size,info.CRC)!=(row['member'],row['size'],row['crc']):
                        raise ValueError('ZIP member changed')
                    with archive.open(info) as stream,extracted.open('wb') as out:
                        shutil.copyfileobj(stream,out,1<<20)
                return self.render_preview(extracted,key)
            except (OSError,ValueError,RuntimeError,StopIteration,zipfile.BadZipFile):
                continue
            finally:
                extracted.unlink(missing_ok=True)
        raise RuntimeError('No supported ZIP preview source')
