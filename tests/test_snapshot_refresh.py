"""Background preparation uses the same fresh snapshot and single builder."""
import contextlib,io,json,threading,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from src.library import Library
from src import jobs
from tests import test_jobs


class RefreshTests(unittest.TestCase):
    def test_prewarm_sees_new_import_and_next_request_reuses_snapshot(self):
        f=test_jobs.JobTests();f.setUp();v=f.viewer
        (f.f.f.source/'added.jpg').write_bytes(b'synthetic added media')
        f.cycle();stop=threading.Event();ready=threading.Event();original=v.snapshot
        def observed():
            value=original();ready.set();return value
        with patch.object(v,'snapshot',side_effect=observed):
            worker=threading.Thread(target=v.refresh_until_stopped,args=(stop,))
            worker.start()
            try:self.assertTrue(ready.wait(5))
            finally:stop.set();worker.join(5)
        self.assertFalse(worker.is_alive())
        with patch('src.library.Library',side_effect=AssertionError('Unexpected second build')):
            self.assertEqual(v.snapshot().search(query='added.jpg')['total'],1)
        # Unavailable sources retain the last published snapshot.
        before=v.snapshot();f.f.f.source.rename(f.f.f.root/'offline')
        self.assertEqual(f.cycle()['state'],'waiting_for_sources')
        self.assertIs(v.snapshot(),before)

    def test_foreground_and_background_share_one_builder(self):
        f=test_jobs.JobTests();f.setUp();v=f.viewer;f.cycle()
        entered=threading.Event();release=threading.Event();other_started=threading.Event()
        def build(*args,**kwargs):
            entered.set()
            if not release.wait(5):raise TimeoutError('synthetic builder wait')
            return Library(*args,**kwargs)
        def request():other_started.set();return v.snapshot()
        with patch('src.library.Library',side_effect=build) as factory,ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(v.snapshot)
            try:
                self.assertTrue(entered.wait(5));second=pool.submit(request);self.assertTrue(other_started.wait(5))
            finally:release.set()
            self.assertIs(first.result(5),second.result(5));self.assertEqual(factory.call_count,1)

    def test_failure_backs_off_without_logging_content_or_replacing_snapshot(self):
        f=test_jobs.JobTests();f.setUp();v=f.viewer;f.cycle();before=v.snapshot();f.cycle()
        # Publish a distinct checkpoint even if fixture timestamps coincide.
        jobs.record(f.f.f.root,{'state':'complete','generation':'synthetic-next'})
        class Stop:
            def is_set(self):return False
            def wait(self,seconds):self.delay=seconds;return True
        stop=Stop();out=io.StringIO()
        with patch('src.library.Library',side_effect=RuntimeError('private synthetic path')),contextlib.redirect_stdout(out):
            v.refresh_until_stopped(stop)
        self.assertIs(v.replacement,before);self.assertEqual(stop.delay,30)
        self.assertEqual(json.loads(out.getvalue()),{'phase':'snapshot_refresh','state':'error','error':'RuntimeError'})
        # Failure does not poison later recovery.
        self.assertNotEqual(v.snapshot().generation,before.generation)

    def test_stopped_loop_does_not_build(self):
        f=test_jobs.JobTests();f.setUp();stop=threading.Event();stop.set()
        with patch.object(f.viewer,'snapshot') as snapshot:
            f.viewer.refresh_until_stopped(stop);snapshot.assert_not_called()


if __name__=='__main__':unittest.main()
