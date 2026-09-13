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


class LiveGroupingTests(unittest.TestCase):
    def test_cluster_follows_sensitivity_and_never_joins_faces_from_one_photo(self):
        rows=[{'face_id':'a'*64,'content_hash':'1'*64,'vector':[1,0]},{'face_id':'b'*64,'content_hash':'2'*64,'vector':[0.95,0.31]},
              {'face_id':'c'*64,'content_hash':'1'*64,'vector':[0.99,0.1]},{'face_id':'d'*64,'content_hash':'3'*64,'vector':[0,1]}]
        groups=faces.cluster(rows,0.5);members={g['id']:set(g['faces']) for g in groups}
        self.assertEqual(sorted(len(m) for m in members.values()),[1,1,2])
        joined=next(m for m in members.values() if len(m)==2)
        self.assertIn('b'*64,joined);self.assertTrue(joined<={'a'*64,'b'*64,'c'*64})
        self.assertFalse({'a'*64,'c'*64}<=joined,'two faces from one photo cannot be one person')
        self.assertEqual(len(faces.cluster(rows,0.999)),4)
        self.assertEqual(faces.cluster([],0.5),[])


class FakeSampler:
    """Two retained samples per video, written as real JPEGs so face previews can crop them."""
    identity='synthetic-samples'
    def extract(self,stream,root,digest):
        from PIL import Image
        rows=[]
        for i in range(2):
            name=f'{digest[:12]}-f{i}.jpg';Image.new('RGB',(64,48),(200,120,80)).save(root/name,'JPEG')
            rows.append({'frame_id':f'{digest}-{i}','timestamp':float(i*5),'filename':name,'image_hash':'0'*64,'interval':5.0,'duration':10.0})
        return rows


def _open_source(row):
    return open(row['source'],'rb')


def _write_clip(path):
    """A one second green video, the same recipe tests/test_library.py uses."""
    import subprocess
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=160x120:r=10','-t','1','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)


class VideoFaceTests(unittest.TestCase):
    """Faces on retained video samples: wait for the sampler, one face per person per video, photo identities untouched."""
    def setUp(self):
        self.f=test_vision.VisionTests();self.f.setUp();f=self.f.fixture;self.root=f.root;self.output=self.root/'faces.db'
        _write_clip(f.source/'clip.mp4');f.refresh()
        with f.connect() as conn:
            imports.inventory(conn,f.catalog,f.exports);imports.hash_pending(conn)
            self.video=conn.execute("SELECT content_hash FROM occurrences WHERE kind='video' AND content_hash IS NOT NULL LIMIT 1").fetchone()[0]

    def test_video_waits_for_sampler_then_gets_one_face_per_person(self):
        import hashlib,json
        from src import frames
        f=self.f.fixture
        class Backend(FakeFaces):
            def detect(self,image):
                if image=='first':return super().detect(image)
                if isinstance(image,str) and image.endswith('-f0.jpg'):return [{'box':[0.2,0.2,0.4,0.5],'score':0.9,'vector':[1,0]}]
                if isinstance(image,str) and image.endswith('-f1.jpg'):return [{'box':[0.2,0.2,0.4,0.5],'score':0.8,'vector':[1,0]},{'box':[0.6,0.2,0.8,0.5],'score':0.7,'vector':[0,1]}]
                return []
        backend=Backend();read=lambda path:path.name
        before=faces.index(f.db,self.output,backend,reader=self.f.reader,frame_reader=read)
        self.assertEqual(before['processed_videos'],0,'no samples yet, so the video is left for a later pass');self.assertGreaterEqual(before['remaining'],1)
        frames.index(f.db,self.root/'frames.db',FakeSampler(),reader=_open_source)
        after=faces.index(f.db,self.output,backend,reader=self.f.reader,frame_reader=read)
        self.assertEqual((after['processed_videos'],after['remaining'],after['processed_this_run']),(1,0,1))
        with imports.database(self.output,[f.source,f.exports]) as conn:
            conn.row_factory=__import__('sqlite3').Row
            rows=[dict(r) for r in conn.execute('SELECT * FROM face_observations WHERE content_hash=? ORDER BY confidence DESC',(self.video,))]
            photo=[dict(r) for r in conn.execute('SELECT * FROM face_observations WHERE content_hash=?',(self.f.first,))][0]
        self.assertEqual(len(rows),2,'one person seen in two samples is one face; a second person is another')
        for row in rows:validate_row('face_observations',row)
        spans=[json.loads(r['source_span']) for r in rows]
        self.assertEqual([s['timestamp'] for s in spans],[0.0,5.0])
        self.assertTrue(all(s['frame_file'].endswith('.jpg') and s['frame_id'] for s in spans))
        self.assertEqual({r['extractor'] for r in rows},{'opencv-yunet-sface-frames'})
        self.assertEqual(photo['face_id'],hashlib.sha256((self.f.first+backend.identity+photo['box_json']).encode()).hexdigest(),'photo identities are unchanged by video support')
        self.assertEqual(faces.index(f.db,self.output,backend,reader=self.f.reader,frame_reader=read)['processed_this_run'],0)

    def test_viewer_shows_video_faces_and_person_search_returns_the_video(self):
        from src import frames
        from tests import test_library
        t=test_library.LibraryTests();t.setUp();f=t.f;_write_clip(f.source/'clip.mp4');f.refresh();library=t.build()
        class Backend:
            identity='synthetic-review'
            def detect(self,image):return [{'box':[.1,.1,.5,.5],'vector':[1,0],'score':.9}]
        frames.index(f.db,f.root/'frames.db',FakeSampler(),reader=_open_source)
        store=f.root/'faces.db';faces.index(f.db,store,Backend(),reader=lambda row:'synthetic');faces.publish_groups(store,Backend.identity)
        video=next(i for i in library.items if i['kind']=='video')
        found=library.photo_faces(video['id'])['faces']
        self.assertEqual(len(found),1);self.assertEqual(found[0]['timestamp'],0.0)
        preview=library.face_preview(found[0]['face_id'])
        self.assertTrue(preview.is_file() and preview.stat().st_size>0,'the face chip is cropped from the retained sample')
        person=library.organize('person',{'person':'','name':'Grandma'})['data']['person']
        library.organize('faces',{'person':person,'faces':[{'face_id':found[0]['face_id'],'content_hash':video['content_hash']}]})
        self.assertIn(video['id'],[i['id'] for i in library.search(person=person)['items']],'a named video face puts the video in that person\'s results')
        self.assertEqual(library.people()['people'][0]['count'],1)
        # Tagging the whole video, the detail panel's "Add a person", and removing it again.
        other=library.organize('person',{'person':'','name':'Grandpa'})['data']['person']
        library.organize('add',{'person':other,'contents':[video['content_hash']]})
        self.assertIn(video['id'],[i['id'] for i in library.search(person=other)['items']])
        library.organize('remove',{'person':other,'contents':[video['content_hash']]})
        self.assertEqual(library.search(person=other)['total'],0)
        with self.assertRaises(ValueError):library.organize('album',{'album':'','name':'x','contents':[video['content_hash']]})
