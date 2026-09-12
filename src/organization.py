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
        people={};tags={};places={};faces={};ignored=set();dates={};trips={};albums={};archived_albums=set();search_reviews={};trip_exclusions={};archived_trips=set()
        for event in events:
            op=event['op'];data=event['data']
            if op=='search_review':
                key=(data['mode'],data['query'],data['item'])
                if data['verdict']=='reset':search_reviews.pop(key,None)
                else:search_reviews[key]=data['verdict']
            elif op=='person': people[data['person']]={'id':data['person'],'name':data['name']}
            elif op=='place': places[data['place']]=data['name']
            elif op=='album':albums[data['album']]={**data,'id':data['album']}
            elif op=='archive_album':archived_albums.add(data['album'])
            elif op=='restore_album':archived_albums.discard(data['album'])
            elif op=='exclude_trip':trip_exclusions.setdefault(data['trip'],set()).update(data['contents'])
            elif op=='restore_trip_items':trip_exclusions.setdefault(data['trip'],set()).difference_update(data['contents'])
            elif op=='archive_trip':archived_trips.add(data['trip'])
            elif op=='restore_trip':archived_trips.discard(data['trip'])
            elif op=='trip':trips[data['trip']]={**data,'id':data['trip']}
            elif op=='date':dates[data['content_hash']]=data['choice']
            elif op=='reset_date':dates.pop(data['content_hash'],None)
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
        return {'people':people,'tags':tags,'places':places,'faces':faces,'ignored_faces':ignored,'dates':dates,'trips':trips,'albums':albums,'archived_albums':archived_albums,'search_reviews':search_reviews,'trip_exclusions':trip_exclusions,'archived_trips':archived_trips}

    def read(self):
        if not self.path.exists():return self.replay([])
        with self.path.open() as stream:
            flock(stream,shared=True)
            return self.replay([json.loads(line) for line in stream if line.strip()])

    def append(self,op,data):
        if op not in ('person','place','add','remove','faces','ignore_faces','restore_faces','date','reset_date','trip','album','archive_album','restore_album','search_review','exclude_trip','restore_trip_items','archive_trip','restore_trip') or not isinstance(data,dict):
            raise ValueError('Unknown owner action')
        data=dict(data)
        expected={'archive_trip':{'trip'},'restore_trip':{'trip'},'exclude_trip':{'trip','contents'},'restore_trip_items':{'trip','contents'},'person':{'person','name'},'place':{'place','name'},'add':{'person','contents'},'remove':{'person','contents'},'faces':{'person','faces'},'ignore_faces':{'faces'},'restore_faces':{'faces'},'date':{'content_hash','choice'},'reset_date':{'content_hash'},'trip':{'trip','name','after','before','place'},'album':{'album','name','contents'},'archive_album':{'album'},'restore_album':{'album'},'search_review':{'mode','query','item','verdict'}}[op]
        if set(data)!=expected:raise ValueError('Unexpected action fields')
        if op=='search_review':
            if data['mode'] not in ('visual','descriptions','video_images') or data['verdict'] not in ('match','mismatch','reset'):raise ValueError('Invalid search review')
            if not isinstance(data['query'],str) or not data['query'].strip() or len(data['query'])>300 or any(ord(c)<32 for c in data['query']):raise ValueError('Invalid search query')
            if not isinstance(data['item'],str) or not re.fullmatch('[a-f0-9]{64}',data['item']):raise ValueError('Invalid search item')
            data['query']=' '.join(data['query'].casefold().split())
        if op in ('person','place','trip','album'):
            name=data['name']
            if not isinstance(name,str) or not name.strip() or len(name)>100 or any(ord(c)<32 for c in name):
                raise ValueError('Use a name of 1 to 100 characters')
            data['name']=name.strip()
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
            if not isinstance(data['place'],str) or (data['place'] and not re.fullmatch(r'-?\d+:-?\d+',data['place'])):raise ValueError('Invalid place')
        if 'person' in data:
            if op=='person' and not data['person']:data['person']=uuid.uuid4().hex
            if not isinstance(data['person'],str) or not re.fullmatch('[a-f0-9]{32}',data['person']):raise ValueError('Invalid person')
        if op=='place' and (not isinstance(data['place'],str) or not re.fullmatch(r'-?\d+:-?\d+',data['place'])):raise ValueError('Invalid place')
        if op in ('add','remove','album','exclude_trip','restore_trip_items'):
            contents=data['contents']
            if not isinstance(contents,list) or not 1<=len(contents)<=200 or any(not isinstance(d,str) or not re.fullmatch('[a-f0-9]{64}',d) for d in contents):
                raise ValueError('Select 1 to 200 verified photos')
            data['contents']=list(dict.fromkeys(contents)) if op=='album' else sorted(set(contents))
        if op in ('faces','ignore_faces','restore_faces'):
            selected=data['faces']
            if not isinstance(selected,list) or not 1<=len(selected)<=200:raise ValueError('Choose 1 to 200 faces')
            for face in selected:
                values=[face] if op in ('ignore_faces','restore_faces') else list(face.values()) if isinstance(face,dict) and set(face)=={'face_id','content_hash'} else []
                if not values or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in values):raise ValueError('Invalid face selection')
        if op in ('date','reset_date'):
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
            if op in ('add','remove','faces') and data['person'] not in state['people']:raise ValueError('Person no longer exists')
            if op in ('exclude_trip','restore_trip_items','archive_trip','restore_trip') and data['trip'] not in state['trips']:raise ValueError('Unknown trip')
            if op in ('archive_album','restore_album') and data['album'] not in state['albums']:raise ValueError('Unknown album')
            event={'id':uuid.uuid4().hex,'at':datetime.now(timezone.utc).isoformat(),'op':op,'data':data,'source':'owner'}
            stream.seek(0,2)
            stream.write(('\n' if existing and not existing.endswith('\n') else '')+json.dumps(event,ensure_ascii=False)+'\n')
            stream.flush();os.fsync(stream.fileno())
        return event
