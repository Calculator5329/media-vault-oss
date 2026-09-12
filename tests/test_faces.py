"""Fresh face identity, source provenance and resumable empty/error outcomes."""
import unittest
from src import faces,imports
from src.kit import validate_row
from tests import test_vision


class FakeFaces:
    identity='synthetic-faces-1'
    def detect(self,image):
        return [{'box':[0.1,0.1,0.3,0.4],'score':0.95,'vector':[1,0]}] if image=='first' else []


class FaceTests(unittest.TestCase):
    def setUp(self):
        self.f=test_vision.VisionTests();self.f.setUp();self.output=self.f.fixture.root/'faces.db'

    def test_verified_duplicates_share_observation_and_resume_keeps_ids(self):
        f=self.f.fixture;backend=FakeFaces()
        first=faces.index(f.db,self.output,backend,reader=self.f.reader)
        self.assertEqual(first['processed_photos'],2);self.assertEqual(first['observations'],1)
        self.assertEqual(first['photos_without_detected_faces'],1)
        second=faces.index(f.db,self.output,backend,reader=self.f.reader)
        self.assertEqual(second['processed_this_run'],0)
        with imports.database(self.output,[f.source,f.exports]) as conn:
            conn.row_factory=__import__('sqlite3').Row
            rows=[dict(r) for r in conn.execute('SELECT * FROM face_observations')]
            validate_row('face_observations',rows[0]);self.assertEqual(rows[0]['content_hash'],self.f.first)
            self.assertNotIn('name',rows[0])

    def test_failed_decode_is_not_reported_as_no_faces_or_retried(self):
        def bad(row):raise ValueError('synthetic decode failure')
        f=self.f.fixture;result=faces.index(f.db,self.output,FakeFaces(),reader=bad)
        self.assertEqual(result['errors'],2);self.assertEqual(result['photos_without_detected_faces'],0)
        self.assertEqual(faces.index(f.db,self.output,FakeFaces(),reader=self.f.reader)['processed_this_run'],0)

    def test_failed_inference_does_not_publish_partial_observations(self):
        class Bad(FakeFaces):
            def detect(self,image):return super().detect('first')+[{'box':[0,0,2,2],'score':1,'vector':[1,0]}]
        result=faces.index(self.f.fixture.db,self.output,Bad(),reader=self.f.reader)
        self.assertEqual(result['observations'],0);self.assertEqual(result['errors'],2)


@unittest.skipUnless(__import__('importlib.util',fromlist=['find_spec']).find_spec('numpy'),'NumPy runtime required')
class GroupTests(unittest.TestCase):
    def test_similar_faces_group_but_same_photo_does_not(self):
        rows=[{'face_id':str(i),'content_hash':digest,'vector':vector} for i,digest,vector in [(1,'a',[1,0]),(2,'b',[0.99,0.1]),(3,'a',[1,0]),(4,'c',[0,1])]]
        groups=faces.suggest_groups(rows)
        self.assertIn(['1','2'],[g['faces'] for g in groups])
        self.assertFalse(any('1' in g['faces'] and '3' in g['faces'] for g in groups))

    def test_similarity_chain_cannot_merge_dissimilar_endpoints(self):
        import math
        rows=[{'face_id':str(i),'content_hash':str(i),'vector':[math.cos(math.radians(deg)),math.sin(math.radians(deg))]} for i,deg in enumerate((0,40,80))]
        groups=faces.suggest_groups(rows)
        self.assertFalse(any(len(g['faces'])==3 for g in groups))
        self.assertEqual(groups,faces.suggest_groups(list(reversed(rows))))

    def test_published_groups_are_repeatable_and_provenance_bearing(self):
        fixture=test_vision.VisionTests();fixture.setUp();f=fixture.fixture;output=f.root/'faces.db';backend=FakeFaces()
        faces.index(f.db,output,backend,reader=fixture.reader)
        result=faces.publish_groups(output,backend.identity)
        self.assertEqual(result,faces.publish_groups(output,backend.identity))
        with imports.database(output,[f.source,f.exports]) as conn:
            conn.row_factory=__import__('sqlite3').Row
            rows=[dict(r) for r in conn.execute('SELECT * FROM face_groups')]
            self.assertEqual(len(rows),1);validate_row('face_groups',rows[0])
