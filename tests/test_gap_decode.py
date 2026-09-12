"""Synthetic only: actual timestamps, stable IDs and bounded resume behavior."""
import hashlib,importlib.util,json,shutil,subprocess,tempfile,unittest
from pathlib import Path
from src.gap_decode import GapDecoder,validate


def plan():return {'status':'planned','revision':'a'*64,'duration_seconds':6.,'targets':[.3,1.1,3.9],'requested_count':3}


class ValidationTests(unittest.TestCase):
    def test_invalid_or_over_budget_plans_are_rejected(self):
        for change in ({'status':'over_budget'},{'targets':[1,1,2]},{'targets':[0,1,2]},{'targets':[1,2,6]},{'requested_count':2},{'duration_seconds':float('nan')}):
            with self.assertRaises(ValueError):validate({**plan(),**change},0,64,120)
        for args in ((-1,64,120),(0,65,120),(0,64,121),(True,1,1)):
            with self.assertRaises(ValueError):validate(plan(),*args)
        validate(plan(),3,1,1)


@unittest.skipUnless(importlib.util.find_spec('av') and shutil.which('ffmpeg'),'PyAV and FFmpeg runtime required')
class DecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(tempfile.mkdtemp(prefix='mv-gap-decode-'));cls.source=cls.root/'synthetic.mp4'
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-n','-f','lavfi','-i','testsrc2=size=128x96:rate=5','-t','6','-c:v','libx264','-preset','ultrafast','-g','15','-keyint_min','15','-sc_threshold','0','-threads','2',str(cls.source)],check=True)
        cls.digest=hashlib.sha256(cls.source.read_bytes()).hexdigest()
    def test_actual_times_hashes_and_resume_match_whole_batch(self):
        backend=GapDecoder()
        with self.source.open('rb') as stream:first=backend.decode(stream,self.root/'samples',self.digest,plan(),limit=1)
        with self.source.open('rb') as stream:rest=backend.decode(stream,self.root/'samples',self.digest,plan(),start_index=first['next_index'])
        with self.source.open('rb') as stream:whole=backend.decode(stream,self.root/'samples',self.digest,plan())
        self.assertEqual(first['remaining'],2);self.assertEqual(rest['remaining'],0)
        self.assertEqual([r['frame_id'] for r in first['frames']+rest['frames']],[r['frame_id'] for r in whole['frames']])
        self.assertEqual([round(r['timestamp'],1) for r in whole['frames']],[.4,1.2,4.])
        for r in whole['frames']:
            self.assertGreaterEqual(r['timestamp'],r['requested_timestamp']);self.assertEqual(r['plan_revision'],plan()['revision'])
            self.assertEqual(hashlib.sha256((self.root/'samples'/r['filename']).read_bytes()).hexdigest(),r['image_hash'])
        self.assertTrue(all(r['status']=='complete' for r in whole['outcomes']))
    def test_missing_positions_are_errors_not_fabricated_frames(self):
        value={**plan(),'duration_seconds':10,'targets':[9],'requested_count':1}
        with self.source.open('rb') as stream:result=GapDecoder().decode(stream,self.root/'samples',self.digest,value)
        self.assertEqual(result['frames'],[]);self.assertEqual(result['outcomes'][0]['status'],'error');self.assertEqual(result['remaining'],0)


if __name__=='__main__':unittest.main()
