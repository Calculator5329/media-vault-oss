"""Owner tags survive restart; sources remain unchanged and untrusted writes refuse."""
import os
os.environ.setdefault('MEDIA_VAULT_EXTERNAL_ROOTS', '/run/media')  # the tests use /run/media as the example removable root
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
        review=self.viewer.face_review();face=review['observations'][0];face_id=face['face_id']
        self.viewer.organize('ignore_faces',{'faces':[face_id]})
        self.assertEqual(self.viewer.face_review(ignored=True)['groups'][0]['faces'],[face_id])
        self.viewer.organize('restore_faces',{'faces':[face_id]})
        self.assertFalse(self.viewer.face_review(ignored=True)['groups'])
        person=self.person()
        self.viewer.organize('faces',{'person':person,'faces':[{'face_id':face_id,'content_hash':face['content_hash']}]})
        self.assertEqual(self.viewer.face_review()['confirmed'],1)
        self.assertEqual(self.viewer.search(person=person)['total'],1)
        with self.assertRaises(ValueError):self.viewer.organize('faces',{'person':person,'faces':[{'face_id':'0'*64,'content_hash':face['content_hash']}]})
