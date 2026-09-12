from pathlib import Path
"""Missing metadata may use matching decoded evidence, never an unrelated hint."""
import json
import subprocess
import unittest
from unittest.mock import patch
from src import video
from tests import test_video

class PlaybackRecoveryTests(unittest.TestCase):
    def test_measured_hint_prepares_and_mismatch_refuses(self):
        f=test_video.VideoTests();f.setUp();path=f.root/'synthetic.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=160x120:r=10','-t','1','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
        raw=path.read_bytes();row=f.row(path,raw);actual=video.probe
        def probe(path):
            if path.suffix=='.source' or Path(path).name=='synthetic.mp4':raise video.MissingDuration()  # the original is now read in place
            return actual(path)
        hint={'content_hash':row['content_hash'],'basis':'decoded_video_extent','duration':1.,'sampler':'synthetic'}
        with patch('src.video.probe',side_effect=probe):
            for bad in (None,{**hint,'content_hash':'f'*64},{**hint,'basis':'guess'},{**hint,'duration':-1}):
                with self.assertRaises(video.MissingDuration):video.prepare([row],f.root/'cache',bad)
            target=video.prepare([row],f.root/'cache',hint)
        receipt=json.loads(target.with_suffix('.json').read_text())
        self.assertEqual(receipt['duration_basis'],'decoded_video_extent')
        self.assertEqual(receipt['duration_evidence'],hint)
        self.assertEqual(path.read_bytes(),raw)

    def test_probe_distinguishes_missing_duration_from_invalid_stream(self):
        for data,error in [({'streams':[{'codec_type':'video'}],'format':{}},video.MissingDuration),({'streams':[],'format':{}},ValueError),({'streams':[{'codec_type':'video'}],'format':{'duration':'-1'}},ValueError)]:
            with patch('src.video.subprocess.run',return_value=type('Result',(),{'stdout':json.dumps(data).encode()})()):
                with self.assertRaises(error):video.probe('synthetic')
