"""Timeline over verified content and its retained read-only source occurrences.

Unverified master entries stay visible. Verified copies collapse in the view;
their originals and source references are retained. Content hashes, rather than
presentation IDs, are the stable keys for later corrections and enrichment.
"""
from contextlib import closing
from collections import defaultdict
import hashlib
import json
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
        self.alternate_sources={}
        self.sources_by_id={}
        for item in self.items:
            relative,size,mtime=self.paths[item['id']]
            digest=master.get((str(self.source/relative),size,mtime))
            if self.type_facts.get(digest,{}).get('detected_kind',item['kind']) not in ('photo','video'):continue
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
            row=next((r for r in sources if self.type_facts.get(digest,{}).get('detected_kind',r['kind']) in ('photo','video')),None)
            if row is None:continue
            source_type='zip' if row['member'] else 'file'
            name=PurePosixPath(row['member']).name if row['member'] else Path(row['source']).name
            key=hashlib.sha256((source_type+':'+digest).encode()).hexdigest()
            item={'id':key,'name':name,'kind':self.type_facts.get(digest,{}).get('detected_kind',row['kind']),
                  'extension':Path(name).suffix.lower(),'size':row['size'],
                  'date':None,'day':None,'location':None,'camera':None,'width':None,'height':None,
                  'duration':None,'metadata_error':False,'duplicate':len(sources)>1,
                  'source_count':len(sources),'content_hash':digest,'source_type':source_type}
            self.alternate_sources[key]=sources
            self.sources_by_id[key]=sources
            self._attach(item)
            selected.append(item)
        self.items=sorted(selected,key=lambda i:(i['day'] or '',i['name']),reverse=True)
        self.by_id={item['id']:item for item in self.items}
        self.by_content={item['content_hash']:item for item in self.items if item['content_hash']}
        self.source_dates={i['id']:(i['date'],i['day']) for i in self.items}
        self._apply_dates()

    def snapshot(self):
        from .jobs import status
        with self.snapshot_lock:
            generation=status(self.catalog_directory).get('generation')
            current=self.replacement or self
            if generation and generation!=current.generation:
                updated=Library(self.catalog_database,self.import_database,thumbnails=self.thumbnails,encoder=self.encoder,organization=self.organization.path)
                updated.visual_index=current.visual_index
                updated.visual_lock=current.visual_lock
                updated.playback=current.playback
                self.replacement=updated
            return self.replacement or self

    def refresh_until_stopped(self, stop, interval=1):
        """Prepare published checkpoints ahead of requests using the same lock."""
        while not stop.is_set():
            delay=interval
            try:self.snapshot()
            except Exception as exc:
                # Content and paths must never enter worker diagnostics.
                print(json.dumps({'phase':'snapshot_refresh','state':'error','error':type(exc).__name__}),flush=True)
                delay=max(30,interval)
            if stop.wait(delay):break

    def _apply_dates(self):
        choices=self.organization.read()['dates']
        for item in self.items:
            item['date'],item['day']=self.source_dates[item['id']]
            choice=choices.get(item['content_hash'])
            item['date_confirmed']=bool(choice)
            if choice:
                item['date']={**choice['date'],'source':'Owner-selected source date'}
                item['day']=item['date']['value'][:10]
        self.items.sort(key=lambda i:(i['day'] or '',i['name']),reverse=True)

    def video_status(self,key,start=False):
        item=self.by_id[key]
        if item['kind']!='video' or not item['content_hash']:raise ValueError('Choose a verified video')
        return self.playback.start(item['content_hash'],self.sources_by_id[key],self.video_duration_hint(item['content_hash'])) if start else self.playback.status(item['content_hash'])

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
        return self.playback.file(item['content_hash'])

    def date_review(self,key):
        from .dates import evidence
        item=self.by_id[key]
        result=evidence(self.facts.get(item['content_hash'],[]))
        result['selected']=self.organization.read()['dates'].get(item['content_hash'])
        return result

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
            rows=self.face_review()['observations'];allowed={r['face_id']:r['content_hash'] for r in rows}
            for face in selected:
                if op=='faces':
                    if not isinstance(face,dict) or allowed.get(face.get('face_id'))!=face.get('content_hash') or not face.get('content_hash'):raise ValueError('Unknown face')
                elif not isinstance(face,str) or face not in allowed:raise ValueError('Unknown face')
        if op in ('add','remove','album'):
            allowed={i['content_hash'] for i in self.items if i['kind']=='photo' and i['content_hash']}
            contents=data.get('contents')
            if not isinstance(contents,list) or any(not isinstance(d,str) or d not in allowed for d in contents):
                raise ValueError('Select verified photos from this library')
        if op=='exclude_trip':
            allowed={i['content_hash'] for i in self._select(trip=data.get('trip',''))}
            contents=data.get('contents')
            if not isinstance(contents,list) or any(not isinstance(d,str) or d not in allowed for d in contents):raise ValueError('Choose current trip items')
        if op in ('place','trip') and (op=='place' or data.get('place')) and data.get('place') not in {self.place_key(i) for i in self.items if i['location']}:
            raise ValueError('Unknown recorded place')
        event=self.organization.append(op,data)
        if op in ('date','reset_date'):self._apply_dates()
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
                    from .faces import suggest_person,observations as face_observations
                    model=json.loads(current[0])['model']
                    rows=[{'face_id':r['face_id'],'content_hash':r['content_hash'],'vector':json.loads(r['vector_json']),'box':json.loads(r['box_json'])} for r in face_observations(conn,model) if r['content_hash'] in self.by_content]
                    result=suggest_person(rows,state['faces'],state['ignored_faces'],person)
                    by_id={r['face_id']:r for r in rows}
                    for candidate in result['candidates']:
                        candidate['item_id']=self.by_content[candidate['content_hash']]['id'];candidate['box']=by_id[candidate['face_id']]['box']
        return {**result,'person':state['people'][person],'meaning':'Possible matches from your confirmed face examples. Review every selected face; these suggestions do not assign anyone automatically.'}

    def face_review(self,ignored=False):
        path=self.catalog_directory/'faces.db'
        if not path.is_file():return {'groups':[],'observations':[],'ready':False}
        current={i['content_hash']:i for i in self.items if i['content_hash'] and i['kind']=='photo'}
        state=self.organization.read()
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            row=conn.execute("SELECT value FROM settings WHERE key='face_groups_current'").fetchone()
            if not row:return {'groups':[],'observations':[],'ready':False}
            config=json.loads(row[0]);model=config['model']
            processed=sum(r[0] in current for r in conn.execute('SELECT content_hash FROM face_work WHERE model=?',(model,)))
            from .faces import observations as face_observations
            observations=[{'face_id':r['face_id'],'content_hash':r['content_hash'],'box':json.loads(r['box_json']),'item_id':current[r['content_hash']]['id'],'recovered_input':r['extractor_version']=='face-recovery-1'} for r in face_observations(conn,model) if r['content_hash'] in current]
            available={r['face_id'] for r in observations};pending=(available&state['ignored_faces']) if ignored else available-set(state['faces'])-state['ignored_faces']
            groups=[]
            for group_id,raw in conn.execute('SELECT group_id,face_ids_json FROM face_groups WHERE model=? AND revision=?',(model,config['revision'])):
                ids=[f for f in json.loads(raw) if f in pending]
                if ids:groups.append({'id':group_id,'faces':ids})
        return {'groups':sorted(groups,key=lambda g:(-len(g['faces']),g['id'])),'observations':observations,
                'ready':True,'processed_photos':processed,'candidate_photos':sum(i['kind']=='photo' and bool(i['content_hash']) for i in self.items),'confirmed':len(available&set(state['faces'])),'ignored':len(available&state['ignored_faces'])}

    def face_preview(self,face_id):
        path=self.catalog_directory/'faces.db'
        if not path.is_file():raise FileNotFoundError()
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            row=conn.execute('SELECT content_hash,box_json,extractor_version,source_span FROM face_observations WHERE face_id=?',(face_id,)).fetchone()
        if row is None or row[0] not in self.by_content:raise FileNotFoundError()
        face={'item_id':self.by_content[row[0]]['id'],'box':json.loads(row[1])}
        target=self.thumbnails/('face-'+face_id+'.jpg')
        recovered=None
        if row[2]=='face-recovery-1':
            from .image_recovery import load
            recovered=load(self.catalog_directory/'image-recovery.db',row[0])
            if recovered is None:raise FileNotFoundError('Recovery unavailable')
            image,fact=recovered;span=json.loads(row[3])
            if any(span[k]!=fact[k] for k in ('artifact_sha256',)) or span['recovery_recipe']!=fact['recipe']:
                image.close();raise ValueError('Face recovery identity changed')
        if target.is_file():
            if recovered:recovered[0].close()
            return target
        from .vision import read_image
        self.thumbnails.mkdir(parents=True,exist_ok=True)
        with THUMBNAIL_WORKERS:
            if recovered:image=recovered[0];image.thumbnail((1600,1600))
            else:
                sources=self.sources_by_id[face['item_id']]
                for row in sources:
                    try:image=read_image(row);break
                    except (OSError,ValueError,RuntimeError,StopIteration,zipfile.BadZipFile):continue
                else:raise FileNotFoundError()
            try:
                x0,y0,x1,y1=face['box'];w,h=image.size
                crop=image.crop((int(x0*w),int(y0*h),int(x1*w),int(y1*h)))
                crop.thumbnail((180,180));temporary=self.thumbnails/(uuid.uuid4().hex+'.jpg')
                crop.save(temporary,format='JPEG',quality=85);crop.close();temporary.replace(target)
            finally:image.close()
            return target

    def people(self):
        state=self.organization.read();groups=[]
        for person in state['people'].values():
            members=[i for i in self.items if person['id'] in state['tags'].get(i['content_hash'],set())]
            groups.append({**person,'count':len(members),'cover':members[0]['id'] if members else None})
        return {'people':sorted(groups,key=lambda p:p['name'].casefold()),
                'untagged':sum(i['kind']=='photo' and not state['tags'].get(i['content_hash']) for i in self.items),
                'automatic_grouping':self.face_review()['ready']}

    def places(self):
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

    def album_items(self,key):
        album=self.organization.read()['albums'].get(key)
        if album is None:raise ValueError('Unknown album')
        items=[self.by_content[d] for d in album['contents'] if d in self.by_content]
        return {'album':album,'items':items,'total':len(items),'missing':len(album['contents'])-len(items)}

    def highlights(self,mode='metadata',query='',category='',kind='all',year='',after='',before='',person='',place='',limit=30,trip='',style='variety'):
        if not 1<=limit<=200:raise ValueError('Choose 1 to 200 highlights')
        filters=dict(query=query,kind=kind,year=year,after=after,before=before,person=person,place=place,trip=trip)
        if mode=='metadata':selected=self._select(**filters);scope='All photos matching these filters.'
        elif mode in ('visual','descriptions'):
            if mode=='visual' and not query.strip():selected=self._select(**filters);scope='All photos matching these filters.'
            else:
                result=self.visual(**filters,limit=200) if mode=='visual' else self.described(**filters,category=category,limit=200)
                selected=result['items'];scope='Up to 200 search results; AI matches and analysis coverage may be incomplete.'
        else:raise ValueError('Highlights require a photo search')
        selected=[i for i in selected if i['kind']=='photo' and i['content_hash']]
        selected.sort(key=lambda i:(i['day'] is None,i['day'] or '',i['id']))
        from .highlights import select
        draft=select(selected,limit,self.visual_database,getattr(self.encoder,'identity',None),style=style,quality_database=self.catalog_directory/'quality.db')
        coverage=draft['selection']
        meaning='Spread across the timeline, oldest first and undated last.'
        if coverage['variety_spans']:meaning+=' Within each time span, indexed photos are chosen for visual variety.'
        meaning+=' Review the selection; no best-photo or duplicate verdict is implied.'
        scope+=f" Visual variety coverage: {coverage['indexed_candidates']} of {coverage['candidates']} selection candidates; missing vectors use timeline selection."
        if style=='quality':
            meaning='Spread across time, preferring model-rated technical clarity where scores are complete. Remove or reorder photos before saving.'
            scope=scope.split(' Visual variety coverage:')[0]
            scope+=f" Technical clarity coverage: {coverage['quality_candidates']} of {coverage['candidates']} candidates scored; {coverage['quality_spans']} time spans use scores."
            if coverage['quality_status']=='unavailable':scope+=' Quality scores are not available yet; the usual selection is used.'
        return {**draft,'total':len(selected),'coverage':scope,'meaning':meaning}

    def trips(self,archived=False):
        from .trips import proposals
        places=self.places()['places'];labels={p['id']:p['name'] or 'Recorded place' for p in places}
        saved=[];state=self.organization.read()
        for trip in state['trips'].values():
            if (trip['id'] in state['archived_trips'])!=archived:continue
            matching=self._select(trip=trip['id'])
            saved.append({**trip,'count':len(matching),'cover':matching[0]['id'] if matching else None})
        proposed=proposals(self.items,self.place_key,labels)
        filters={(t['after'],t['before'],t['place']) for t in state['trips'].values()}
        return {'trips':sorted(saved,key=lambda t:(t['after'],t['id']),reverse=True),
                'proposals':[] if archived else [p for p in proposed if (p['after'],p['before'],p['place']) not in filters],
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
            from .transcript_chunks import coverage
            return {'status':work['status'],'segments':segments,'model':model[0],'coverage':coverage(conn,model[0],item['content_hash'])}

    def moments(self,query='',year='',after='',before='',offset=0,limit=80,person='',place='',trip=''):
        if len(query)>300 or offset<0 or not 1<=limit<=200:raise ValueError('Invalid moment query')
        path=self.catalog_directory/'transcripts.db'
        if not path.is_file():return {'items':[],'total':0,'next_offset':None,'processed_videos':0}
        allowed={i['content_hash']:i for i in self._select('',kind='video',year=year,after=after,before=before,person=person,place=place,trip=trip)};terms=query.casefold().split();results=[]
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row;model=conn.execute("SELECT value FROM settings WHERE key='transcripts_current'").fetchone()
            if not model:return {'items':[],'total':0,'next_offset':None,'processed_videos':0}
            statuses=dict(conn.execute('SELECT content_hash,status FROM transcript_work WHERE model=?',(model[0],)))
            for row in conn.execute('SELECT content_hash,start_seconds,end_seconds,text FROM transcript_facts WHERE model=? ORDER BY content_hash,start_seconds',(model[0],)):
                if row['content_hash'] in allowed and all(t in row['text'].casefold() for t in terms):results.append({**allowed[row['content_hash']],'moment':{'start':row['start_seconds'],'end':row['end_seconds'],'text':row['text'],'transcript_status':statuses.get(row['content_hash'],'unknown')}})
            processed=conn.execute('SELECT count(*) FROM transcript_work WHERE model=?',(model[0],)).fetchone()[0]
        return {'items':results[offset:offset+limit],'total':len(results),'next_offset':offset+limit if offset+limit<len(results) else None,'processed_videos':processed}

    def descriptions(self):
        path=self.catalog_directory/'descriptions.db'
        if not path.is_file():return {}
        if time.monotonic()-self.description_checked<60:return self.description_rows
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            from .descriptions import facts
            values=facts(conn)
            for value in values.values():value['description']=json.loads(value.pop('value_json'))
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
        from .frames import sampling_gaps
        gaps=sampling_gaps(allowed.values())
        if not self.visual_lock.acquire(blocking=False):raise RuntimeError('Image search is busy')
        try:
            if not hasattr(self,'scene_index'):self.scene_index=SearchIndex(path,self.encoder)
            ranked=self.scene_index.search(query,limit=200,allowed=set(allowed))
        finally:self.visual_lock.release()
        items=[]
        for match in ranked['results']:
            row=allowed[match['content_hash']];item=eligible[row['content_hash']]
            items.append({**item,'similarity':match['similarity'],'frame_id':row['frame_id'],'moment':{'start':row['timestamp'],'end':row['timestamp'],'text':'Sampled video image'},'sample_interval':row['interval'],'sampling':gaps[row['content_hash']]})
        items=self.reviewed_search(items,query,'video_images')
        return {'items':items[offset:offset+limit],'total':len(items),'next_offset':offset+limit if offset+limit<len(items) else None,'indexed_frames':ranked['indexed_contents'],'eligible_frames':ranked['eligible_contents'],'sampled_videos':len(gaps),'eligible_videos':len(eligible),'videos_without_samples':len(eligible)-len(gaps),'sampling':{'measured_videos':sum(g['available'] for g in gaps.values()),'largest_gap_seconds':max((g['largest_gap_seconds'] for g in gaps.values() if g['available']),default=None)},'meaning':'Closest sampled video images, not confirmed object matches. The initial pass uses up to 60 keyframe samples; targeted gap snapshots may add more. Brief appearances can still be missed.'}

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
            if row:
                self.ocr_texts={r[0]:r[1].casefold() for r in conn.execute('SELECT content_hash,text FROM ocr_facts WHERE model=?',(row[0],))}
                from .ocr_recovery import facts
                for digest,fact in facts(conn,row[0]).items():self.ocr_texts.setdefault(digest,fact['text'].casefold())
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
            if not row:
                from .ocr_recovery import facts
                row=facts(conn,model[0]).get(digest)
        if not row:return None
        value=dict(row);value['recognition']=json.loads(value.pop('words_json'));return value

    def _select(self,query='',kind='all',year='',after='',before='',near='',person='',place='',trip=''):
        if len(query)>300:raise ValueError('Search is too long')
        state=self.organization.read();terms=query.casefold().split();selected=[]
        chosen=state['trips'].get(trip) if trip else None
        if trip and chosen is None:raise ValueError('Unknown trip')
        recognized=self.ocr_search_text() if terms else {}
        for item in super()._select('','all' if kind=='date_review' else kind,year,after,before,near):
            if kind=='date_review' and (item.get('date_status') not in ('conflict','timezone_unknown','invalid') or item.get('date_confirmed')):continue
            people=[state['people'][p] for p in sorted(state['tags'].get(item['content_hash'],set())) if p in state['people']]
            if person=='untagged' and (people or item['kind']!='photo'):continue
            if person and person!='untagged' and not any(p['id']==person for p in people):continue
            key=self.place_key(item)
            if chosen and (not item['day'] or not chosen['after']<=item['day']<=chosen['before'] or (chosen['place'] and chosen['place']!=key) or item['content_hash'] in state['trip_exclusions'].get(trip,set())):continue
            if place and key!=place:continue
            geo=self.geographic_names.get(key,{})
            name=state['places'].get(key,'')
            geographic=' '.join(str(geo.get(k,'')) for k in ('name','region','country'))
            trip_names=' '.join(t['name'] for t in state['trips'].values() if t['id'] not in state['archived_trips'] and item['content_hash'] not in state['trip_exclusions'].get(t['id'],set()) and item['day'] and t['after']<=item['day']<=t['before'] and (not t['place'] or t['place']==key))
            searchable=f"{item['name']} {item['camera'] or ''} {item['day'] or ''} {name} {geographic} {trip_names} {' '.join(p['name'] for p in people)} {recognized.get(item['content_hash'],'')}".casefold()
            if not all(term in searchable for term in terms):continue
            selected.append({**item,'people':people,'place_name':name})
        return selected

    def search(self,query='',kind='all',year='',offset=0,limit=80,after='',before='',near='',person='',place='',trip=''):
        if offset<0 or not 1<=limit<=200:raise ValueError('Invalid pagination')
        items=self._select(query,kind,year,after,before,near,person,place,trip)
        return {'items':items[offset:offset+limit],'total':len(items),
                'next_offset':offset+limit if offset+limit<len(items) else None}

    def slideshow(self,query='',year='',after='',before='',near='',located=False,limit=500,kind='all',person='',place='',trip=''):
        if not 1<=limit<=500:raise ValueError('Invalid slideshow limit')
        items=[i for i in self._select(query,'located' if located else kind,year,after,before,near,person,place,trip) if i['kind']=='photo']
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
                item['width']=item['width'] or value.get('width')
                item['height']=item['height'] or value.get('height')
        from .dates import evidence
        item['date_status']=evidence(facts)['status']
        item['metadata_fact_count']=len(facts)

    def summary(self):
        result={**super().summary(),'source_occurrences':self.source_occurrences,
                'zip_items':sum(i.get('source_type')=='zip' for i in self.items),'deduplicated':True,
                'unverified_items':sum(not i['content_hash'] for i in self.items),
                'catalog_scope':'Primary catalog plus verified contents from all configured folders and export ZIPs. Unverified master copies may still overlap; metadata and indexing coverage remain incomplete.'}
        result['date_review_needed']=sum(i.get('date_status') in ('conflict','timezone_unknown','invalid') and not i.get('date_confirmed') for i in self.items)
        result['generation']=self.generation
        result['capabilities']['video_playback']=True
        result['capabilities']['organization']=True
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

    def similar_versions(self,key,limit=24):
        if not 1<=limit<=48:raise ValueError('Choose 1 to 48 suggestions')
        anchor=self.by_id[key]
        if anchor['kind']!='photo' or not anchor['content_hash']:raise ValueError('Choose a verified photo')
        allowed={h:i for h,i in self.by_content.items() if i['kind']=='photo'}
        result={'anchor':anchor,'items':[],'total':0,'indexed':0,'eligible':len(allowed),'state':'unavailable','model':None}
        path=self.catalog_directory/'fingerprints.db'
        if not path.is_file():return result
        from .fingerprints import compare
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
                conn.row_factory=sqlite3.Row;conn.execute('BEGIN')
                model=conn.execute("SELECT value FROM settings WHERE key='fingerprints_current'").fetchone()
                if not model:return result
                result['model']=model[0]
                from .fingerprint_recovery import facts
                recovered=facts(conn,model[0])
                seed=conn.execute('SELECT * FROM fingerprints_facts WHERE content_hash=? AND model=?',(anchor['content_hash'],model[0])).fetchone()
                if not seed:seed=recovered.get(anchor['content_hash'])
                if not seed:
                    work=conn.execute('SELECT status FROM fingerprints_work WHERE content_hash=? AND model=?',(anchor['content_hash'],model[0])).fetchone()
                    result['state']='failed' if work and work[0]=='error' else 'pending';return result
                seed=dict(seed);result['state']='flat' if seed['gray_range']<8 else 'ready';matches=[]
                from itertools import chain
                base=conn.execute('SELECT content_hash,gray_hash,gray_range,color_json,aspect_ratio FROM fingerprints_facts WHERE model=?',(model[0],))
                for row in chain(base,recovered.values()):
                    if row['content_hash'] not in allowed:continue
                    result['indexed']+=1
                    if row['content_hash']==anchor['content_hash'] or result['state']=='flat':continue
                    evidence=compare(seed,dict(row))
                    if evidence:matches.append({**allowed[row['content_hash']],'comparison':evidence})
                matches.sort(key=lambda i:(i['comparison']['hash_distance'],i['comparison']['color_rmse'],i['id']))
                result.update(items=matches[:limit],total=len(matches))
        except sqlite3.Error:result.update(state='unavailable',items=[],total=0)
        return result

    def quality_details(self,key):
        """Read only the current technical-quality suggestion with its source evidence."""
        import math
        item=self.by_id[key];path=self.catalog_directory/'quality.db'
        if item['kind']!='photo' or not path.is_file():return None
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
                conn.row_factory=sqlite3.Row;conn.execute('BEGIN')
                model=conn.execute("SELECT value FROM settings WHERE key='quality_current'").fetchone()
                if not model:return None
                fact=conn.execute('SELECT * FROM quality_facts WHERE content_hash=? AND model=?',(item['content_hash'],model[0])).fetchone()
                work=conn.execute('SELECT status FROM quality_work WHERE content_hash=? AND model=?',(item['content_hash'],model[0])).fetchone()
                if not fact:
                    from .quality_recovery import facts
                    fact=facts(conn,model[0]).get(item['content_hash'])
                if fact and isinstance(fact['score'],(int,float)) and math.isfinite(fact['score']):
                    return {'status':'complete','model':fact['model'],'fact':dict(fact)}
                return {'status':work[0] if work and work[0] in ('error','running') else 'pending','model':model[0],'fact':None}
        except sqlite3.Error:return None

    def recovered_preview_details(self,key):
        from .image_recovery import load
        item=self.by_id[key]
        if item['kind']!='photo' or not item['content_hash']:return None
        try:
            result=load(self.catalog_directory/'image-recovery.db',item['content_hash'])
            if result is None:return None
            image,fact=result;image.close()
            return {'status':'ready','recipe':fact['recipe'],'artifact_sha256':fact['artifact_sha256'],'width':fact['width'],'height':fact['height'],'provenance':json.loads(fact['details_json'])}
        except (OSError,ValueError,KeyError,sqlite3.Error):return {'status':'unavailable'}

    def metadata(self,key):
        item=self.by_id[key]
        if not item['content_hash']:
            return super().metadata(key)
        return {'content_hash':item['content_hash'],'dates':self.date_review(key),'quality':self.quality_details(key),'recovered_preview':self.recovered_preview_details(key),'transcript':self.transcript(key),'detected_type':self.type_facts.get(item['content_hash']),'ai':self.descriptions().get(item['content_hash']),'ocr':self.ocr_details(item['content_hash']),'facts':self.facts.get(item['content_hash'],[]),
                'sources':[{'path':r['source'],'member':r['member'],'size':r['size']} for r in self.sources_by_id[key]],
                'status':'Source claims are preserved separately. Differing values are not automatically resolved.'}

    def thumbnail(self,key):
        item=self.by_id[key]
        if item['kind']=='photo' and item['content_hash']:
            from .image_recovery import load
            recovered=load(self.catalog_directory/'image-recovery.db',item['content_hash'])
            if recovered:
                image,fact=recovered
                target=self.thumbnails/(key+'.recovered-'+fact['artifact_sha256']+'.jpg')
                try:
                    with THUMBNAIL_WORKERS:
                        if not target.is_file():
                            self.thumbnails.mkdir(parents=True,exist_ok=True)
                            staging=self.thumbnails/(target.stem+'.'+uuid.uuid4().hex+'.jpg')
                            image.thumbnail((640,640));image.save(staging,format='JPEG',quality=85)
                            if not target.exists():staging.rename(target)
                    return target
                finally:image.close()
        if key not in self.alternate_sources:
            return super().thumbnail(key)
        target=self.thumbnails/(key+'.jpg')
        if target.is_file() and target.stat().st_size:
            return target
        if self.by_id[key]['kind']!='photo':
            raise RuntimeError('Additional-source video preview is not connected yet')
        from .vision import read_image
        with THUMBNAIL_WORKERS:
            if target.is_file() and target.stat().st_size:
                return target
            self.thumbnails.mkdir(parents=True,exist_ok=True)
            for row in self.alternate_sources[key]:
                try:
                    image=read_image(row)
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
        raise RuntimeError('No supported preview source')
