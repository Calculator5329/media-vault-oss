"""Owner tags survive restart; sources remain unchanged and untrusted writes refuse."""
import io
import json
import unittest
from src.organization import Organization
from src.library import Library
from src.server import handler
from tests import test_library


class OrganizationTests(unittest.TestCase):
    def setUp(self):
        f=test_library.LibraryTests();f.setUp();self.viewer=f.build();self.fixture=f
        self.path=self.viewer.organization.path
        self.photo=self.viewer.search(query='new.jpg')['items'][0]

    def person(self):
        return self.viewer.organize('person',{'person':'','name':'Synthetic Person'})['data']['person']

    def test_tags_survive_new_viewer_and_remove_retains_history(self):
        person=self.person();digest=self.photo['content_hash']
        self.viewer.organize('add',{'person':person,'contents':[digest]})
        rebuilt=Library(self.fixture.f.catalog,self.fixture.f.db,organization=self.path)
        self.assertEqual(rebuilt.search(person=person)['total'],1)
        self.assertEqual(rebuilt.search(query='Synthetic Person')['total'],1)
        self.assertEqual(rebuilt.slideshow(person=person)['total'],1)
        self.assertEqual(rebuilt.people()['people'][0]['count'],1)
        rebuilt.organize('remove',{'person':person,'contents':[digest]})
        self.assertEqual(rebuilt.search(person=person)['total'],0)
        self.assertEqual(len(self.path.read_text().splitlines()),3)
        self.assertEqual((self.fixture.f.source/'a.jpg').read_bytes(),b'same image bytes')

    def test_places_are_named_without_replacing_gps(self):
        place=self.viewer.places()['places'][0]
        self.viewer.organize('place',{'place':place['id'],'name':'Synthetic Park'})
        self.assertEqual(self.viewer.search(place=place['id'])['total'],1)
        self.assertEqual(self.viewer.search(query='Synthetic Park')['total'],1)
        self.assertEqual(self.viewer.slideshow(place=place['id'])['total'],1)
        self.assertEqual(self.viewer.places()['places'][0]['name'],'Synthetic Park')
        self.assertEqual(self.viewer.search(place=place['id'])['items'][0]['location']['lat'],40)

    def test_invalid_actions_do_not_change_saved_history(self):
        person=self.person();before=self.path.read_bytes()
        for op,data in [('add',{'person':person,'contents':['0'*64]}),('person',{'person':'','name':'\n'}),('add',{'person':person,'contents':[]}),('place',{'place':'900:900','name':'Unknown'})]:
            with self.assertRaises(ValueError):self.viewer.organize(op,data)
        self.assertEqual(self.path.read_bytes(),before)
        with self.assertRaises(ValueError):Organization('/run/media/unsafe.jsonl')

    def post(self,value,origin='http://127.0.0.1:8771',host='127.0.0.1:8771'):
        body=json.dumps(value).encode();instance=object.__new__(handler(self.viewer,8771))
        instance.path='/api/organize';instance.headers={'Host':host,'Content-Type':'application/json','Content-Length':str(len(body))}
        if origin:instance.headers['Origin']=origin
        instance.rfile=io.BytesIO(body);responses=[];instance.send=lambda status,body,mime='application/json':responses.append((status,json.loads(body)))
        instance.do_POST();return responses[0]

    def test_http_save_requires_same_origin_and_returns_durable_receipt(self):
        value={'op':'person','data':{'person':'','name':'Synthetic Person'}}
        for origin in ('https://foreign.example',None):self.assertEqual(self.post(value,origin)[0],403)
        self.assertFalse(self.path.exists())
        status,result=self.post(value)
        self.assertEqual(status,200);self.assertTrue(result['saved'])
        self.assertEqual(json.loads(self.path.read_text())['id'],result['event'])

    def test_face_confirmations_can_be_split_and_removal_survives_replay(self):
        person=self.person();digest=self.photo['content_hash']
        other=self.viewer.organize('person',{'person':'','name':'Another Synthetic Person'})['data']['person']
        store=self.viewer.organization
        store.append('faces',{'person':person,'faces':[{'face_id':'a'*64,'content_hash':digest},{'face_id':'b'*64,'content_hash':digest}]})
        store.append('faces',{'person':other,'faces':[{'face_id':'b'*64,'content_hash':digest}]})
        state=store.read();self.assertEqual(state['tags'][digest],{person,other})
        self.assertEqual(state['faces']['a'*64]['person'],person)
        store.append('ignore_faces',{'faces':['b'*64]})
        self.assertEqual(store.read()['tags'][digest],{person})
        store.append('remove',{'person':person,'contents':[digest]})
        self.assertFalse(store.read()['tags'][digest]);self.assertFalse(store.read()['faces'])

    def test_face_review_ignore_restore_and_confirm_follow_current_observations(self):
        from src import faces
        class Backend:
            identity='synthetic-review'
            def detect(self,image):return [{'box':[.1,.1,.4,.5],'score':.99,'vector':[1,0]}]
        db=self.fixture.f.root/'faces.db'
        faces.index(self.fixture.f.db,db,Backend(),reader=lambda row:'fixture')
        faces.publish_groups(db,Backend.identity)
        review=self.viewer.face_review();face=review['groups'][0]['faces'][0];face_id=face['face_id']
        self.viewer.organize('ignore_faces',{'faces':[face_id]})
        self.assertEqual([f['face_id'] for f in self.viewer.face_review(ignored=True)['groups'][0]['faces']],[face_id])
        self.viewer.organize('restore_faces',{'faces':[face_id]})
        self.assertFalse(self.viewer.face_review(ignored=True)['groups'])
        person=self.person()
        self.viewer.organize('faces',{'person':person,'faces':[{'face_id':face_id,'content_hash':face['content_hash']}]})
        self.assertEqual(self.viewer.face_review()['confirmed'],1)
        self.assertEqual(self.viewer.search(person=person)['total'],1)
        with self.assertRaises(ValueError):self.viewer.organize('faces',{'person':person,'faces':[{'face_id':'0'*64,'content_hash':face['content_hash']}]})

    def test_merge_person_moves_tags_and_faces_and_retires_the_source(self):
        a=self.person();b=self.viewer.organize('person',{'person':'','name':'Other Person'})['data']['person']
        digest=self.photo['content_hash']
        self.viewer.organize('add',{'person':a,'contents':[digest]})
        self.viewer.organize('merge_person',{'person':a,'into':b})
        state=self.viewer.organization.read()
        self.assertNotIn(a,state['people']);self.assertEqual(state['tags'][digest],{b})
        with self.assertRaises(ValueError):self.viewer.organize('add',{'person':a,'contents':[digest]})
        with self.assertRaises(ValueError):self.viewer.organize('merge_person',{'person':b,'into':b})
        with self.assertRaises(ValueError):self.viewer.organize('merge_person',{'person':b,'into':'0'*32})

    def test_face_review_hints_named_people_and_keeps_groups_stable_across_naming(self):
        from src import faces
        class Backend:
            identity='synthetic-hints'
            def detect(self,image):return [{'box':[.1,.1,.4,.5],'score':.99,'vector':[1,0]}]
        db=self.fixture.f.root/'faces.db'
        faces.index(self.fixture.f.db,db,Backend(),reader=lambda row:'fixture');faces.publish_groups(db,Backend.identity)
        review=self.viewer.face_review(sensitivity='balanced')
        self.assertEqual(len(review['groups']),1);self.assertIsNone(review['groups'][0]['looks_like'])
        first=review['groups'][0]['faces'][0];person=self.person()
        self.viewer.organize('faces',{'person':person,'faces':[{k:first[k] for k in ('face_id','content_hash')}]})
        after=self.viewer.face_review(sensitivity='balanced')
        self.assertEqual(after['groups'][0]['id'],review['groups'][0]['id'])
        self.assertEqual(len(after['groups'][0]['faces']),len(review['groups'][0]['faces'])-1)
        self.assertEqual(after['groups'][0]['looks_like']['id'],person);self.assertEqual(after['confirmed'],1)
        self.assertEqual(self.viewer.people()['people'][0]['face'],first['face_id'])
        with self.assertRaises(ValueError):self.viewer.face_review(sensitivity='anything')



class PersonReviewCompletionTests(unittest.TestCase):
    def test_hidden_people_lose_hints_and_covers_follow_confirmed_faces(self):
        from src import faces
        from tests import test_library
        f=test_library.LibraryTests();f.setUp();library=f.build()
        class Backend:
            identity='synthetic-review'
            def detect(self,image):return [{'box':[.1,.1,.5,.5],'vector':[1,0],'score':.9}]
        database=f.f.root/'faces.db';faces.index(f.f.db,database,Backend(),reader=lambda row:'synthetic');faces.publish_groups(database,Backend.identity)
        person=library.organize('person',{'person':'','name':'Grandma'})['data']['person']
        face=library.face_review()['groups'][0]['faces'][0]
        library.organize('faces',{'person':person,'faces':[{k:face[k] for k in ('face_id','content_hash')}]})
        listed=library.people()['people'][0]
        self.assertEqual((listed['face'],listed['cover_face'],listed['hidden'],listed['confirmed_faces']),(face['face_id'],None,False,1))
        with self.assertRaises(ValueError):library.organize('cover_face',{'person':person,'face_id':'0'*64})
        library.organize('cover_face',{'person':person,'face_id':face['face_id']})
        self.assertEqual(library.people()['people'][0]['cover_face'],face['face_id'])
        mine=next(x for x in library.photo_faces(face['item_id'])['faces'] if x['face_id']==face['face_id'])
        self.assertEqual((mine['person']['id'],mine['cover'],mine['ignored']),(person,True,False))
        library.organize('hide_person',{'person':person})
        self.assertTrue(library.people()['people'][0]['hidden'])
        self.assertTrue(all(g['looks_like'] is None for g in library.face_review()['groups']),'hidden people never hint')
        library.organize('show_person',{'person':person})
        self.assertFalse(library.people()['people'][0]['hidden'])
        library.organize('ignore_faces',{'faces':[face['face_id']]})
        self.assertIsNone(library.people()['people'][0]['cover_face'],'an ignored face cannot stay the cover')
        with self.assertRaises(ValueError):library.organize('hide_person',{'person':'f'*32})


class StackReviewTests(unittest.TestCase):
    def test_proposed_stacks_collapse_the_grid_until_the_owner_rules(self):
        from src import similar
        f=test_library.LibraryTests();f.setUp();library=f.build()
        photos=[i for i in library.items if i['kind']=='photo' and i['content_hash']]
        self.assertGreaterEqual(len(photos),3)
        first,second,third=photos[0]['content_hash'],photos[1]['content_hash'],photos[2]['content_hash']
        library.organize('set_date',{'contents':[first,second],'date':'2020-05-05'})
        hashes={first:'ff00ff00ff00ff00',second:'ff00ff00ff00ff01',third:'0000000000000000'}
        similar.index(f.f.db,f.f.root/'similar.db',reader=lambda row:row['content_hash'],hasher=lambda digest:hashes[digest])
        review=library.stacks()
        self.assertEqual((review['proposed'],review['confirmed'],review['ready']),(1,0,True))
        proposal=review['stacks'][0]
        self.assertEqual(sorted(proposal['contents']),sorted([first,second]))
        visible={i['content_hash'] for i in library.search()['items']}
        self.assertEqual(len(visible),len(photos)-1+sum(i['kind']!='photo' for i in library.items),'a proposal collapses to its top')
        self.assertIn(proposal['top'],visible);self.assertNotIn(next(c for c in proposal['contents'] if c!=proposal['top']),visible)
        self.assertEqual(library.search(stack='all')['total'],len(library.items))
        self.assertEqual([i['content_hash'] for i in library.search(kind='stacks')['items']],[proposal['top']])
        self.assertEqual(library.search(stack=proposal['id'])['total'],2)
        top=next(i for i in library.search()['items'] if i['content_hash']==proposal['top'])
        self.assertEqual((top['stack']['count'],top['stack']['top'],top['stack']['confirmed']),(2,True,False))
        library.organize('unstack',{'stack':proposal['id']})
        self.assertEqual(library.stacks()['proposed'],0,'keep separate silences the same proposal')
        self.assertEqual(library.search()['total'],len(library.items))
        other=next(c for c in proposal['contents'] if c!=proposal['top'])
        confirmed=library.organize('stack',{'stack':'','contents':[first,second],'top':other})['data']
        self.assertEqual(confirmed['stack'],similar.stack_id([first,second]))
        review=library.stacks()
        self.assertEqual((review['confirmed'],review['proposed'],review['stacks'][0]['top']),(1,0,other))
        self.assertIn(other,{i['content_hash'] for i in library.search()['items']})
        with self.assertRaises(ValueError):library.organize('stack',{'stack':'','contents':[first],'top':first})
        with self.assertRaises(ValueError):library.organize('stack',{'stack':'','contents':[first,second],'top':third})
        with self.assertRaises(ValueError):library.organize('unstack',{'stack':'0'*32})
        rebuilt=Library(f.f.catalog,f.f.db,organization=library.organization.path)
        self.assertEqual(rebuilt.stacks()['confirmed'],1,'confirmed stacks survive a rebuild')

    def test_a_raw_file_stacks_under_the_jpeg_shot_beside_it(self):
        from src import catalog
        f=test_library.LibraryTests();f.setUp()
        for name,payload in (('DSC_0042.JPG',b'jpeg bytes 42'),('DSC_0042.NEF',b'raw bytes 42'),('DSC_0043.NEF',b'raw bytes 43 alone')):
            (f.f.source/name).write_bytes(payload)
        source,conn=catalog.connect(f.f.source,f.f.catalog);catalog.inventory(source,conn);conn.close()
        library=f.build()
        pair=next(s for s in library.stacks()['stacks'] if s.get('raw_pair'))
        names={i['name']:i['top'] for i in pair['items']}
        self.assertEqual(names,{'DSC_0042.JPG':True,'DSC_0042.NEF':False})
        shown={i['name'] for i in library.search()['items']}
        self.assertIn('DSC_0042.JPG',shown);self.assertNotIn('DSC_0042.NEF',shown)
        self.assertIn('DSC_0043.NEF',shown,'a raw file with no JPEG beside it is an ordinary photo')
        library.organize('unstack',{'stack':pair['id']})
        self.assertIn('DSC_0042.NEF',{i['name'] for i in library.search()['items']})

    def test_undated_proposals_collapse_too_and_separate_with_one_verdict(self):
        """Every stack is a view, so undated near-duplicates (forms, screenshots) collapse like the rest."""
        from src import similar
        f=test_library.LibraryTests();f.setUp();library=f.build()
        photos=[i for i in library.items if i['kind']=='photo' and i['content_hash']]
        undated=[i['content_hash'] for i in photos if not i['day']][:2]
        if len(undated)<2:
            for c in [i['content_hash'] for i in photos][:2]:library.organize('reset_date',{'content_hash':c})
            undated=[i['content_hash'] for i in library.items if i['kind']=='photo' and i['content_hash'] and not i['day']][:2]
        self.assertEqual(len(undated),2)
        similar.index(f.f.db,f.f.root/'similar.db',reader=lambda row:row['content_hash'],hasher=lambda digest:'ff00ff00ff00ff00' if digest in undated else '0000000000000000')
        review=library.stacks()
        proposal=next(s for s in review['stacks'] if sorted(s['contents'])==sorted(undated))
        self.assertTrue(proposal['collapse'])
        self.assertEqual(library.search()['total'],len(library.items)-1,'an undated proposal collapses to its top like any other')
        self.assertEqual([i['content_hash'] for i in library.search(kind='stacks')['items']],[proposal['top']])
        library.organize('unstack',{'stack':proposal['id']})
        self.assertEqual(library.search()['total'],len(library.items),'separate brings every member back')
