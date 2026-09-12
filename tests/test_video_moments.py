"""Synthetic video samples preserve timestamps, source evidence and query scope."""
from contextlib import contextmanager
import hashlib
import io
import json
import unittest
from PIL import Image
from src import frames,scenes
from tests import test_library

class SampleBackend:
    identity='synthetic-sampler'
    def extract(self,stream,root,digest):
        rows=[]
        for n,color in enumerate(('red','blue')):
            key=hashlib.sha256((digest+str(n)).encode()).hexdigest();filename=key+'.'+'a'*32+'.jpg';path=root/filename
            Image.new('RGB',(32,32),color).save(path)
            rows.append({'frame_id':key,'timestamp':float(n),'filename':filename,'image_hash':hashlib.sha256(path.read_bytes()).hexdigest(),'interval':5.,'duration':2.})
        return rows

class Encoder:
    identity='synthetic-video-encoder'
    def image(self,image):return [1,0] if image.getpixel((0,0))[0]>100 else [0,1]
    def text(self,text):return [1,0]

@contextmanager
def reader(row):yield io.BytesIO(b'synthetic')

class VideoMomentTests(unittest.TestCase):
    def setUp(self):
        self.f=test_library.LibraryTests();self.f.setUp();(self.f.f.source/'clip.mp4').write_bytes(b'synthetic video');self.f.f.refresh();self.v=self.f.build();self.v.encoder=Encoder()

    def test_sample_index_resume_search_and_timestamp_filters(self):
        root=self.f.f.root
        result=frames.index(self.f.f.db,root/'frames.db',SampleBackend(),reader=reader)
        self.assertEqual(result['frames'],2);self.assertEqual(result['remaining'],0)
        self.assertEqual(frames.index(self.f.f.db,root/'frames.db',SampleBackend(),reader=reader)['processed_this_run'],0)
        indexed=scenes.index(self.f.f.db,root/'scenes.db',Encoder());self.assertEqual(indexed['indexed'],2)
        found=self.v.video_moments('red car');self.assertEqual(found['total'],2)
        self.assertEqual([i['moment']['start'] for i in found['items']],[0,1])
        self.assertTrue(self.v.frame_preview(found['items'][0]['frame_id']).is_file())
        self.assertEqual(self.v.video_moments('red car',year='2026')['total'],0)
        rows,_=scenes.observations(root/'frames.db')
        self.assertEqual(json.loads(rows[0]['source_span'])['timestamp'],rows[0]['timestamp'])
        self.assertEqual(scenes.index(self.f.f.db,root/'scenes.db',Encoder())['processed_this_run'],0)
        with self.assertRaises(FileNotFoundError):self.v.frame_preview('f'*64)

    def test_mutated_samples_do_not_publish_embeddings(self):
        root=self.f.f.root;frames.index(self.f.f.db,root/'frames.db',SampleBackend(),reader=reader)
        rows,_=scenes.observations(root/'frames.db');path=scenes.sample_path(root,rows[0]);path.write_bytes(b'changed')
        result=scenes.index(self.f.f.db,root/'scenes.db',Encoder())
        self.assertEqual(result['indexed'],1);self.assertEqual(result['errors'],1)
        with self.assertRaises(ValueError):scenes.sample_path(root,{**rows[1],'filename':'../escape.jpg'})

    def test_failed_source_keeps_no_frame_facts(self):
        @contextmanager
        def changing(row):
            yield io.BytesIO(b'synthetic')
            raise ValueError('Source changed after decode')
        root=self.f.f.root;result=frames.index(self.f.f.db,root/'frames.db',SampleBackend(),reader=changing)
        self.assertEqual(result['frames'],0);self.assertEqual(result['errors'],1)
        self.assertEqual(scenes.observations(root/'frames.db')[0],[])
        self.assertTrue(list((root/'video-samples').iterdir()))
