"""Duration recovery requires measured EOF; old successful samples stay usable."""
from contextlib import contextmanager
from types import SimpleNamespace
import json
import sqlite3
import unittest
from src import frames,scenes
from tests import test_video_moments
from tests.test_video_moments import SampleBackend,reader

class DurationRecoveryTests(unittest.TestCase):
    def sampler(self,times):
        @contextmanager
        def opened(stream,**kwargs):
            video=SimpleNamespace(thread_count=0)
            yield SimpleNamespace(streams=SimpleNamespace(video=[video]),start_time=0,decode=lambda v:iter([SimpleNamespace(time=t,duration=1,time_base=.1,width=32,height=32) for t in times]))
        sampler=frames.Sampler.__new__(frames.Sampler);sampler.av=SimpleNamespace(open=opened,time_base=1000000)
        return sampler

    def test_extent_uses_timestamps_and_refuses_empty_or_invalid_timeline(self):
        self.assertAlmostEqual(self.sampler([0,.5,1]).decoded_extent(None),1.1)
        for times in ([],[None],[0,float('nan')],[1,0],[7201]):
            with self.assertRaises(ValueError):self.sampler(times).decoded_extent(None)

    def test_retries_failed_duration_without_rebuilding_good_samples(self):
        fixture=test_video_moments.VideoMomentTests();fixture.setUp();root=fixture.f.f.root
        frames.index(fixture.f.f.db,root/'frames.db',SampleBackend(),reader=reader)
        with sqlite3.connect(root/'frames.db') as conn:
            good=conn.execute('SELECT content_hash FROM frame_work').fetchone()[0]
            conn.execute("INSERT INTO frame_work VALUES(?,?,?,?,?)",('f'*64,'synthetic-sampler',0,'ValueError','earlier'))
        class Compatible(SampleBackend):
            identity='synthetic-recovery';compatible_models=['synthetic-sampler']
            def extract(self,*args):raise AssertionError('Successful content must not be resampled')
        result=frames.index(fixture.f.f.db,root/'frames.db',Compatible(),reader=reader)
        self.assertEqual(result['processed_this_run'],0)
        self.assertEqual(len(scenes.observations(root/'frames.db')[0]),2)
        with sqlite3.connect(root/'frames.db') as conn:
            conn.execute("UPDATE frame_work SET error='ValueError',frames=0 WHERE content_hash=?",(good,))
        class Recovery(SampleBackend):
            identity='synthetic-recovery';compatible_models=['synthetic-sampler']
            def extract(self,*args):return []
        result=frames.index(fixture.f.f.db,root/'frames.db',Recovery(),reader=reader)
        self.assertEqual(result['processed_this_run'],1)
        with sqlite3.connect(root/'frames.db') as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM frame_work WHERE content_hash=?',(good,)).fetchone()[0],2)
            self.assertEqual(conn.execute('SELECT error FROM frame_work WHERE content_hash=? AND sampler=?',(good,'synthetic-sampler')).fetchone()[0],'ValueError')
        self.assertEqual(frames.index(fixture.f.f.db,root/'frames.db',Recovery(),reader=reader)['processed_this_run'],0)


class EncodingRetryTests(unittest.TestCase):
    def test_configuration_retries_only_the_measured_error_family(self):
        fixture=test_video_moments.VideoMomentTests();fixture.setUp();root=fixture.f.f.root
        frames.index(fixture.f.f.db,root/'frames.db',SampleBackend(),reader=reader)
        with sqlite3.connect(root/'frames.db') as conn:conn.execute("UPDATE frame_work SET error='ValueError'")
        class Encoding(SampleBackend):
            identity='encoding-recovery';compatible_models=['synthetic-sampler'];retry_errors={'UnicodeDecodeError'}
            def extract(self,*args):return []
        self.assertEqual(frames.index(fixture.f.f.db,root/'frames.db',Encoding(),reader=reader)['processed_this_run'],0)
        with sqlite3.connect(root/'frames.db') as conn:conn.execute("UPDATE frame_work SET error='UnicodeDecodeError'")
        self.assertEqual(frames.index(fixture.f.f.db,root/'frames.db',Encoding(),reader=reader)['processed_this_run'],1)
        with sqlite3.connect(root/'frames.db') as conn:self.assertEqual(conn.execute("SELECT count(*) FROM frame_work WHERE error='UnicodeDecodeError'").fetchone()[0],1)
