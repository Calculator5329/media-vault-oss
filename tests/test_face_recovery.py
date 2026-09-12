"""Recovered faces remain reviewable suggestions with verified source pixels."""
from contextlib import closing
import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from src import faces,face_recovery
from tests import test_recovered_previews


class Backend:
    identity='synthetic-recovered-faces'
    embedding_dimension=2
    def detect(self,image):return [{'box':[.1,.1,.5,.5],'score':.95,'vector':[1,0]}]


class FaceRecoveryTests(unittest.TestCase):
    def fixture(self):
        f=test_recovered_previews.RecoveredPreviewTests();f.fixture();self.f=f;self.db=f.f.f.f.root/'faces.db';self.imports=f.f.f.f.db;self.backend=Backend()
        def read(row):
            if row['content_hash']==f.digest:raise OSError('synthetic original decode failure')
            return Image.new('RGB',(40,30),'green')
        with patch('src.face_recovery.run',return_value={'processed':0,'remaining':0}):faces.index(self.imports,self.db,self.backend,reader=read)
    def test_group_review_crop_and_confirmed_exemplar_use_recovery(self):
        self.fixture();result=faces.index(self.imports,self.db,self.backend)
        self.assertEqual(result['errors'],0);self.assertEqual(result['observations'],3)
        published=faces.publish_groups(self.db,self.backend.identity)
        review=self.f.viewer.face_review();self.assertEqual(len(review['observations']),3)
        face=next(r for r in review['observations'] if r['content_hash']==self.f.digest)
        self.assertTrue(face['recovered_input']);self.assertEqual(published['groups'],1)
        with Image.open(self.f.viewer.face_preview(face['face_id'])) as crop:self.assertEqual(crop.size,(16,12))
        person=self.f.viewer.organize('person',{'person':'','name':'Synthetic person'})['data']['person']
        self.assertEqual(self.f.viewer.person_suggestions(person)['reference_faces'],0)
        self.f.viewer.organize('faces',{'person':person,'faces':[{k:face[k] for k in ('face_id','content_hash')}]})
        self.assertEqual(self.f.viewer.person_suggestions(person)['total'],2)
        self.assertEqual(self.f.viewer.face_review()['confirmed'],1)
        with patch.object(self.backend,'detect',side_effect=AssertionError('Repeated')):self.assertEqual(faces.index(self.imports,self.db,self.backend)['processed_this_run'],0)
        self.assertEqual(faces.publish_groups(self.db,self.backend.identity),published)
        self.assertEqual(self.f.viewer.face_review()['confirmed'],1)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM face_work WHERE status='error'").fetchone()[0],1)
            self.assertEqual(face_recovery.observations(c,'other-model'),[])
        Path(self.f.fact['artifact_path']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.f.viewer.face_preview(face['face_id'])
    def test_zero_faces_is_complete_and_original_zero_takes_precedence(self):
        self.fixture()
        with patch.object(self.backend,'detect',return_value=[]):result=faces.index(self.imports,self.db,self.backend)
        self.assertEqual(result['photos_without_detected_faces'],1);self.assertEqual(result['errors'],0)
        with closing(sqlite3.connect(self.db)) as c:
            self.assertEqual(len(face_recovery.completed(c,self.backend.identity)),1)
            c.execute("UPDATE face_work SET status='complete',error=NULL WHERE content_hash=?",(self.f.digest,));c.commit()
            self.assertEqual(face_recovery.completed(c,self.backend.identity),{})
        self.assertEqual(faces.index(self.imports,self.db,self.backend)['processed_this_run'],0)
    def test_invalid_partial_batch_and_dimensions_never_publish(self):
        for bad in ([{'box':[0,0,2,2],'score':.9,'vector':[1,0]}],[{'box':[.1,.1,.5,.5],'score':.9,'vector':[1,0,0]}]):
            self.fixture()
            with patch.object(self.backend,'detect',return_value=self.backend.detect(None)+bad):self.assertEqual(faces.index(self.imports,self.db,self.backend)['errors'],1)
            with closing(sqlite3.connect(self.db)) as c:self.assertEqual(face_recovery.observations(c,self.backend.identity),[])
            self.assertEqual(faces.index(self.imports,self.db,self.backend)['processed_this_run'],0)
    def test_corruption_and_interruption_do_not_retry(self):
        self.fixture();Path(self.f.fact['artifact_path']).write_bytes(b'corrupt')
        with patch.object(self.backend,'detect') as detect:
            self.assertEqual(faces.index(self.imports,self.db,self.backend)['errors'],1);detect.assert_not_called()
        self.fixture()
        with patch.object(self.backend,'detect',side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):faces.index(self.imports,self.db,self.backend)
        self.assertEqual(faces.index(self.imports,self.db,self.backend)['processed_this_run'],0)


if __name__=='__main__':unittest.main()
