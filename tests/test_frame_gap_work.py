"""Persistent gap batches never publish unverified sources or planned times."""
from contextlib import contextmanager,closing
import hashlib,io,json,sqlite3,time,unittest,uuid
from pathlib import Path
from PIL import Image
from src import frames,scenes,frame_gap_work as work
from tests import test_video_moments as demo


class Decoder:
    identity='synthetic-gap-decoder'
    def decode(self,stream,root,digest,plan,start_index=0,limit=64,seconds=120):
        facts=[];outcomes=[];end=min(len(plan['targets']),start_index+limit)
        for index in range(start_index,end):
            target=plan['targets'][index];key=hashlib.sha256((digest+plan['revision']+str(index)).encode()).hexdigest();filename=key+'.'+uuid.uuid4().hex+'.jpg'
            Image.new('RGB',(32,32),'red').save(root/filename);sha=hashlib.sha256((root/filename).read_bytes()).hexdigest()
            facts.append({'frame_id':key,'timestamp':target+.1,'requested_timestamp':target,'request_index':index,'filename':filename,'image_hash':sha,'decoder':self.identity,'plan_revision':plan['revision'],'pts':index,'time_base':'1/10','duration':plan['duration_seconds']})
            outcomes.append({'request_index':index,'status':'complete','frame_id':key})
        return {'frames':facts,'outcomes':outcomes,'next_index':end}


@contextmanager
def reader(row,cache):yield io.BytesIO(b'synthetic')


class GapWorkTests(unittest.TestCase):
    def fixture(self):
        f=demo.VideoMomentTests();f.setUp();self.f=f;self.root=f.f.f.root;self.db=self.root/'frames.db'
        frames.index(f.f.f.db,self.db,demo.SampleBackend(),reader=demo.reader)
        with closing(sqlite3.connect(self.db)) as c:c.execute('UPDATE frame_facts SET duration=100');c.commit()
        with closing(sqlite3.connect(f.f.f.db)) as c:
            c.row_factory=sqlite3.Row;self.candidates={r['content_hash']:[dict(r)] for r in c.execute("SELECT * FROM occurrences WHERE kind='video' AND present=1")}
    def run_batch(self,limit=64,source=reader,backend=None):
        with closing(sqlite3.connect(self.db)) as c:return work.run(c,self.candidates,self.root/'video-samples',time.monotonic()+60,limit,backend=backend or Decoder(),reader=source)
    def test_resume_scene_search_and_finite_requests(self):
        self.fixture();first=self.run_batch(1);self.assertEqual(first['processed'],1);self.assertGreater(first['remaining'],0)
        with closing(sqlite3.connect(self.db)) as c:
            fact=work.observations(c)[0];self.assertAlmostEqual(fact['timestamp']-fact['requested_timestamp'],.1)
            self.assertIn('offset',json.loads(fact['source_span']));self.assertEqual(work.coverage(c)['completed'],1)
        rest=self.run_batch();self.assertEqual(rest['remaining'],0);self.assertEqual(self.run_batch()['processed'],0)
        rows,_=scenes.observations(self.db);self.assertEqual(len(rows),2+rest['frames'])
        scenes.index(self.f.f.f.db,self.root/'scenes.db',demo.Encoder())
        self.assertEqual(self.f.v.video_moments('red car')['total'],len(rows))
    def test_source_exit_failure_rejects_every_frame_and_retains_attempts(self):
        self.fixture()
        @contextmanager
        def changed(row,cache):
            yield io.BytesIO(b'synthetic')
            raise ValueError('Source changed after decode')
        result=self.run_batch(source=changed);self.assertEqual(result['frames'],0)
        with closing(sqlite3.connect(self.db)) as c:
            coverage=work.coverage(c);self.assertEqual(coverage['failed'],coverage['requested']);self.assertEqual(coverage['completed'],0)
        self.assertEqual(self.run_batch()['processed'],0)
        self.assertGreater(len(list((self.root/'video-samples').iterdir())),2)
    def test_interrupted_reservation_is_terminal_but_unreserved_requests_resume(self):
        self.fixture()
        class Interrupted(Decoder):
            def decode(self,*args,**kwargs):raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.run_batch(1,backend=Interrupted())
        result=self.run_batch();self.assertEqual(result['remaining'],0)
        with closing(sqlite3.connect(self.db)) as c:
            coverage=work.coverage(c);self.assertEqual(coverage['failed'],1);self.assertGreater(coverage['completed'],0)
            self.assertEqual(c.execute('SELECT error FROM frame_gap_requests WHERE request_index=0').fetchone()[0],'InterruptedAttempt')
    def test_verified_local_stream_rejects_cache_change_during_decode(self):
        self.fixture();source=next(iter(self.candidates.values()))[0]
        with self.assertRaises(ValueError):
            with work.source_stream(source,self.root/'playback') as stream:
                self.assertEqual(hashlib.sha256(stream.read()).hexdigest(),source['content_hash'])
                Path(stream.name).write_bytes(b'changed synthetic cache')

    def test_corrupt_decoder_result_and_retired_plan_are_excluded(self):
        self.fixture()
        class Corrupt(Decoder):
            def decode(self,*args,**kwargs):
                result=super().decode(*args,**kwargs);result['frames'][0]['timestamp']+=2;return result
        self.assertEqual(self.run_batch(1,backend=Corrupt())['frames'],0)
        self.run_batch()
        with closing(sqlite3.connect(self.db)) as c:
            self.assertTrue(work.observations(c));c.execute('UPDATE frame_facts SET duration=2');c.commit()
            work.run(c,self.candidates,self.root/'video-samples',time.monotonic()+60,64)
            self.assertEqual(work.observations(c),[]);self.assertGreater(c.execute('SELECT count(*) FROM frame_gap_facts').fetchone()[0],0)


if __name__=='__main__':unittest.main()
