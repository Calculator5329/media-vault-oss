"""Confirmed exemplars suggest candidates without creating new assignments."""
import unittest
from src.faces import suggest_person
from src import faces
from tests import test_library


def observation(key,vector=(1,0),content=None):
    return {'face_id':key,'content_hash':content or key,'vector':vector}

class PersonFollowupTests(unittest.TestCase):
    def test_ignores_assigned_ignored_and_same_photo_faces(self):
        rows=[observation('ref'),observation('candidate'),observation('same',content='ref'),observation('ignored'),observation('assigned')]
        assignments={'ref':{'person':'p'},'assigned':{'person':'q'}}
        # Another confirmed person with an identical vector makes it ambiguous.
        self.assertEqual(suggest_person(rows,assignments,{'ignored'},'p')['total'],0)
        result=suggest_person(rows,{'ref':{'person':'p'}},{'ignored','assigned'},'p')
        self.assertEqual([r['face_id'] for r in result['candidates']],['candidate'])
        self.assertEqual(result['reference_faces'],1)

    def test_requires_corroboration_and_competing_person_margin(self):
        rows=[observation('a'),observation('b',(0,1)),observation('c')]
        self.assertEqual(suggest_person(rows,{'a':{'person':'p'},'b':{'person':'p'}},set(),'p')['total'],0)
        rows=[observation('a'),observation('b',(.99,.1)),observation('c')]
        self.assertEqual(suggest_person(rows,{'a':{'person':'p'},'b':{'person':'q'}},set(),'p')['total'],0)
        self.assertEqual(suggest_person(rows,{},set(),'p')['reference_faces'],0)

    def test_owner_confirmation_updates_candidates_without_automatic_labels(self):
        f=test_library.LibraryTests();f.setUp();v=f.build()
        class Backend:
            identity='synthetic-followup'
            def detect(self,image):return [{'box':[.1,.1,.5,.5],'vector':[1,0],'score':.9}]
        database=f.f.root/'faces.db';faces.index(f.f.db,database,Backend(),reader=lambda row:'synthetic');faces.publish_groups(database,Backend.identity)
        person=v.organize('person',{'person':'','name':'Synthetic person'})['data']['person']
        self.assertEqual(v.person_suggestions(person)['reference_faces'],0)
        reference=v.face_review()['observations'][0]
        v.organize('faces',{'person':person,'faces':[{k:reference[k] for k in ('face_id','content_hash')}]})
        before=len(v.organization.path.read_text().splitlines());result=v.person_suggestions(person)
        self.assertEqual(result['total'],2);self.assertEqual(len(v.organization.path.read_text().splitlines()),before)
        chosen=result['candidates'][0]
        v.organize('faces',{'person':person,'faces':[{k:chosen[k] for k in ('face_id','content_hash')}]})
        self.assertEqual(v.person_suggestions(person)['total'],1)
        self.assertEqual(v.person_suggestions(person)['reference_faces'],2)
        with self.assertRaises(ValueError):v.person_suggestions('unknown')
