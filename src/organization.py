"""Durable owner names and whole-photo membership, independent of face models.

The append-only log lives outside the disposable catalog. Content hashes survive
renames and index rebuilds. Removing a tag appends a new event, retaining history.
"""
from .portable import lock as flock, assert_local_state
import json
import os
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone


class Organization:
    def __init__(self,path):
        self.path=assert_local_state(path,'Owner corrections')

    @staticmethod
    def replay(events):
        people={};tags={};places={};faces={};ignored=set();dates={};hidden_people=set();covers={};trips={};albums={};archived_albums=set();search_reviews={};trip_exclusions={};archived_trips=set();stacks={};separated=set();favorites=set();hidden_items=set();buckets={};archived_buckets=set();locations={};dismissed={'date':set(),'location':set()}
        for event in events:
            op=event['op'];data=event['data']
            if op=='search_review':
                key=(data['mode'],data['query'],data['item'])
                if data['verdict']=='reset':search_reviews.pop(key,None)
                else:search_reviews[key]=data['verdict']
            elif op=='person': people[data['person']]={'id':data['person'],'name':data['name']}
            elif op=='merge_person':
                # Everything confirmed for the merged person now belongs to the surviving one.
                source,target=data['person'],data['into'];people.pop(source,None);hidden_people.discard(source);covers.pop(source,None)
                for members in tags.values():
                    if source in members:members.discard(source);members.add(target)
                for value in faces.values():
                    if value['person']==source:value['person']=target
            elif op=='hide_person':hidden_people.add(data['person'])
            elif op=='show_person':hidden_people.discard(data['person'])
            elif op=='cover_face':covers[data['person']]=data['face_id']
            elif op=='stack':stacks[data['stack']]={'id':data['stack'],'contents':data['contents'],'top':data['top']};separated.discard(data['stack'])
            elif op=='unstack':stacks.pop(data['stack'],None);separated.add(data['stack'])
            elif op=='favorite':favorites.update(data['contents'])
            elif op=='unfavorite':favorites.difference_update(data['contents'])
            elif op=='hide':hidden_items.update(data['contents'])
            elif op=='unhide':hidden_items.difference_update(data['contents'])
            elif op=='place': places[data['place']]=data['name']
            elif op=='album':albums[data['album']]={**data,'id':data['album']}
            # Buckets are owner-managed collections that grow over time: create or rename keeps the members.
            elif op=='bucket':buckets[data['bucket']]={'id':data['bucket'],'name':data['name'],'contents':buckets.get(data['bucket'],{}).get('contents',[])}
            elif op=='bucket_add':
                if data['bucket'] in buckets:buckets[data['bucket']]['contents']=list(dict.fromkeys(buckets[data['bucket']]['contents']+data['contents']))
            elif op=='bucket_remove':
                if data['bucket'] in buckets:buckets[data['bucket']]['contents']=[d for d in buckets[data['bucket']]['contents'] if d not in set(data['contents'])]
            elif op=='archive_bucket':archived_buckets.add(data['bucket'])
            elif op=='restore_bucket':archived_buckets.discard(data['bucket'])
            elif op=='archive_album':archived_albums.add(data['album'])
            elif op=='restore_album':archived_albums.discard(data['album'])
            elif op=='exclude_trip':trip_exclusions.setdefault(data['trip'],set()).update(data['contents'])
            elif op=='restore_trip_items':trip_exclusions.setdefault(data['trip'],set()).difference_update(data['contents'])
            elif op=='archive_trip':archived_trips.add(data['trip'])
            elif op=='restore_trip':archived_trips.discard(data['trip'])
            elif op=='trip':trips[data['trip']]={**data,'id':data['trip']}
            elif op=='date':dates[data['content_hash']]=data['choice']
            elif op=='set_date':
                for digest in data['contents']:dates[digest]={'id':'owner:'+data['date'],'date':{'value':data['date']+'T12:00:00','meaning':'capture','field':'owner'},'source':'owner'}
                dismissed['date'].difference_update(data['contents'])
            elif op=='reset_date':dates.pop(data['content_hash'],None)
            elif op=='set_location':
                for digest in data['contents']:locations[digest]={'lat':data['lat'],'lon':data['lon']}
                dismissed['location'].difference_update(data['contents'])
            elif op=='reset_location':locations.pop(data['content_hash'],None)
            elif op=='dismiss_gap':dismissed[data['gap']].update(data['contents'])
            elif op=='restore_gap':dismissed[data['gap']].difference_update(data['contents'])
            elif op=='faces':
                for face in data['faces']:
                    faces[face['face_id']]={'person':data['person'],'content_hash':face['content_hash']}
                    ignored.discard(face['face_id'])
            elif op=='restore_faces':ignored.difference_update(data['faces'])
            elif op=='ignore_faces':
                for face_id in data['faces']:
                    ignored.add(face_id);faces.pop(face_id,None)
            elif op in ('add','remove'):
                for digest in data['contents']:
                    members=tags.setdefault(digest,set())
                    if op=='add':members.add(data['person'])
                    else:
                        members.discard(data['person'])
                        faces={key:value for key,value in faces.items() if not (value['person']==data['person'] and value['content_hash']==digest)}
            else:raise ValueError('Unrecognized owner action')
        for value in faces.values():tags.setdefault(value['content_hash'],set()).add(value['person'])
        return {'people':people,'hidden_people':hidden_people,'covers':covers,'tags':tags,'places':places,'faces':faces,'ignored_faces':ignored,'dates':dates,'trips':trips,'albums':albums,'archived_albums':archived_albums,'search_reviews':search_reviews,'trip_exclusions':trip_exclusions,'archived_trips':archived_trips,'stacks':stacks,'separated':separated,'favorites':favorites,'hidden':hidden_items,'buckets':buckets,'archived_buckets':archived_buckets,'locations':locations,'dismissed':dismissed}

    def version(self):
        """Identity of the log's current bytes; the replayed state is a pure function of it."""
        try:stat=self.path.stat()
        except FileNotFoundError:return None
        return (stat.st_mtime_ns,stat.st_size,stat.st_ino)

    def read(self):
        """Replayed state, cached per log version. Callers must treat it as read-only."""
        version=self.version()
        cached=getattr(self,'_cache',None)
        if cached and cached[0]==version:return cached[1]
        if version is None:state=self.replay([])
        else:
            with self.path.open() as stream:
                flock(stream,shared=True)
                state=self.replay([json.loads(line) for line in stream if line.strip()])
        self._cache=(version,state)
        return state

    def append(self,op,data):
        if op not in ('person','merge_person','hide_person','show_person','cover_face','place','add','remove','faces','ignore_faces','restore_faces','date','set_date','reset_date','trip','album','archive_album','restore_album','search_review','exclude_trip','restore_trip_items','archive_trip','restore_trip','stack','unstack','favorite','unfavorite','hide','unhide','bucket','bucket_add','bucket_remove','archive_bucket','restore_bucket','set_location','reset_location','dismiss_gap','restore_gap') or not isinstance(data,dict):
            raise ValueError('Unknown owner action')
        data=dict(data)
        expected={'bucket':{'bucket','name'},'bucket_add':{'bucket','contents'},'bucket_remove':{'bucket','contents'},'archive_bucket':{'bucket'},'restore_bucket':{'bucket'},'favorite':{'contents'},'unfavorite':{'contents'},'hide':{'contents'},'unhide':{'contents'},'stack':{'stack','contents','top'},'unstack':{'stack'},'archive_trip':{'trip'},'restore_trip':{'trip'},'exclude_trip':{'trip','contents'},'restore_trip_items':{'trip','contents'},'person':{'person','name'},'merge_person':{'person','into'},'hide_person':{'person'},'show_person':{'person'},'cover_face':{'person','face_id'},'place':{'place','name'},'add':{'person','contents'},'remove':{'person','contents'},'faces':{'person','faces'},'ignore_faces':{'faces'},'restore_faces':{'faces'},'date':{'content_hash','choice'},'set_date':{'contents','date'},'reset_date':{'content_hash'},'trip':{'trip','name','after','before','place'},'album':{'album','name','contents'},'archive_album':{'album'},'restore_album':{'album'},'search_review':{'mode','query','item','verdict'},'set_location':{'contents','lat','lon'},'reset_location':{'content_hash'},'dismiss_gap':{'gap','contents'},'restore_gap':{'gap','contents'}}[op]
        if set(data)!=expected:raise ValueError('Unexpected action fields')
        if op=='search_review':
            if data['mode'] not in ('visual','descriptions','video_images') or data['verdict'] not in ('match','mismatch','reset'):raise ValueError('Invalid search review')
            if not isinstance(data['query'],str) or not data['query'].strip() or len(data['query'])>300 or any(ord(c)<32 for c in data['query']):raise ValueError('Invalid search query')
            if not isinstance(data['item'],str) or not re.fullmatch('[a-f0-9]{64}',data['item']):raise ValueError('Invalid search item')
            data['query']=' '.join(data['query'].casefold().split())
        if op in ('person','place','trip','album','bucket'):
            name=data['name']
            if not isinstance(name,str) or not name.strip() or len(name)>100 or any(ord(c)<32 for c in name):
                raise ValueError('Use a name of 1 to 100 characters')
            data['name']=name.strip()
        if 'bucket' in data:
            if op=='bucket' and not data['bucket']:data['bucket']=uuid.uuid4().hex
            if not isinstance(data['bucket'],str) or not re.fullmatch('[a-f0-9]{32}',data['bucket']):raise ValueError('Invalid bucket')
        if op in ('album','archive_album','restore_album'):
            if op=='album' and not data['album']:data['album']=uuid.uuid4().hex
            if not isinstance(data['album'],str) or not re.fullmatch('[a-f0-9]{32}',data['album']):raise ValueError('Invalid album')
        if op in ('exclude_trip','restore_trip_items','archive_trip','restore_trip') and (not isinstance(data['trip'],str) or not re.fullmatch('[a-f0-9]{32}',data['trip'])):raise ValueError('Invalid trip')
        if op=='trip':
            from datetime import date
            if not data['trip']:data['trip']=uuid.uuid4().hex
            if not isinstance(data['trip'],str) or not re.fullmatch('[a-f0-9]{32}',data['trip']):raise ValueError('Invalid trip')
            if not all(isinstance(data[k],str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',data[k]) for k in ('after','before')):raise ValueError('Use complete dates')
            if date.fromisoformat(data['after'])>date.fromisoformat(data['before']):raise ValueError('Reversed trip dates')
            if not isinstance(data['place'],str) or (data['place'] and not re.fullmatch(r'-?\d+:-?\d+(,-?\d+:-?\d+)*',data['place'])):raise ValueError('Invalid place')
            # A trip may span several recorded areas: keys joined by commas, deduplicated and ordered so equal trips compare equal.
            data['place']=','.join(sorted(set(data['place'].split(',')))) if data['place'] else ''
        if 'person' in data:
            if op=='person' and not data['person']:data['person']=uuid.uuid4().hex
            if not isinstance(data['person'],str) or not re.fullmatch('[a-f0-9]{32}',data['person']):raise ValueError('Invalid person')
        if op=='merge_person' and (not isinstance(data['into'],str) or not re.fullmatch('[a-f0-9]{32}',data['into']) or data['into']==data['person']):raise ValueError('Merge two different people')
        if op=='place' and (not isinstance(data['place'],str) or not re.fullmatch(r'-?\d+:-?\d+',data['place'])):raise ValueError('Invalid place')
        if op=='set_date':
            from datetime import date
            if not isinstance(data['date'],str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',data['date']):raise ValueError('Use a complete date')
            date.fromisoformat(data['date'])
        if op=='set_location':
            for axis,limit in (('lat',90),('lon',180)):
                value=data[axis]
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not -limit<=value<=limit:raise ValueError('Use coordinates in decimal degrees')
                data[axis]=round(float(value),6)
        if op in ('dismiss_gap','restore_gap') and data['gap'] not in ('date','location'):raise ValueError('Unknown gap')
        if op in ('add','remove','album','exclude_trip','restore_trip_items','set_date','stack','favorite','unfavorite','hide','unhide','bucket_add','bucket_remove','set_location','dismiss_gap','restore_gap'):
            contents=data['contents']
            if not isinstance(contents,list) or not (2 if op=='stack' else 1)<=len(contents)<=200 or any(not isinstance(d,str) or not re.fullmatch('[a-f0-9]{64}',d) for d in contents):
                raise ValueError('Select 1 to 200 verified photos')
            data['contents']=list(dict.fromkeys(contents)) if op=='album' else sorted(set(contents))
        if op=='stack':
            from .similar import stack_id
            if len(data['contents'])<2 or data['top'] not in data['contents']:raise ValueError('A stack needs two photos and a top from among them')
            if not data['stack']:data['stack']=stack_id(data['contents'])
        if op in ('stack','unstack') and (not isinstance(data['stack'],str) or not re.fullmatch('[a-f0-9]{32}',data['stack'])):raise ValueError('Invalid stack')
        if op=='cover_face' and (not isinstance(data['face_id'],str) or not re.fullmatch('[a-f0-9]{64}',data['face_id'])):raise ValueError('Invalid face')
        if op in ('faces','ignore_faces','restore_faces'):
            selected=data['faces']
            if not isinstance(selected,list) or not 1<=len(selected)<=200:raise ValueError('Choose 1 to 200 faces')
            for face in selected:
                values=[face] if op in ('ignore_faces','restore_faces') else list(face.values()) if isinstance(face,dict) and set(face)=={'face_id','content_hash'} else []
                if not values or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in values):raise ValueError('Invalid face selection')
        if op in ('date','reset_date','reset_location'):
            if not isinstance(data['content_hash'],str) or not re.fullmatch('[a-f0-9]{64}',data['content_hash']):raise ValueError('Invalid content')
            if op=='date':
                from .dates import normalize
                choice=data['choice']
                if not isinstance(choice,dict) or set(choice)!={'id','date','source'}:raise ValueError('Invalid date choice')
                normalize(choice['date']['value'])
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with os.fdopen(os.open(self.path,os.O_RDWR|os.O_CREAT,0o600),'r+',encoding='utf-8') as stream:
            flock(stream)
            existing=stream.read();events=[json.loads(line) for line in existing.splitlines() if line.strip()]
            state=self.replay(events)
            if op in ('add','remove','faces','hide_person','show_person','cover_face') and data['person'] not in state['people']:raise ValueError('Person no longer exists')
            if op=='cover_face' and state['faces'].get(data['face_id'],{}).get('person')!=data['person']:raise ValueError('Choose a confirmed face of this person')
            if op=='merge_person' and (data['person'] not in state['people'] or data['into'] not in state['people']):raise ValueError('Person no longer exists')
            if op in ('exclude_trip','restore_trip_items','archive_trip','restore_trip') and data['trip'] not in state['trips']:raise ValueError('Unknown trip')
            if op in ('archive_album','restore_album') and data['album'] not in state['albums']:raise ValueError('Unknown album')
            if op in ('bucket_add','bucket_remove','archive_bucket','restore_bucket') and data['bucket'] not in state['buckets']:raise ValueError('Unknown bucket')
            event={'id':uuid.uuid4().hex,'at':datetime.now(timezone.utc).isoformat(),'op':op,'data':data,'source':'owner'}
            stream.seek(0,2)
            stream.write(('\n' if existing and not existing.endswith('\n') else '')+json.dumps(event,ensure_ascii=False)+'\n')
            stream.flush();os.fsync(stream.fileno())
        self._cache=None
        return event
