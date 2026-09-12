"""Synthetic video preparation, immutable sources and HTTP seeking."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from src.video import prepare,verified_copy,byte_range,Playback,probe
from src.server import handler


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp())

    def row(self,path,raw,member='',offset=0,crc=0):
        s=path.stat()
        return {'source':str(path),'source_size':s.st_size,'mtime_ns':s.st_mtime_ns,'size':len(raw),'member':member,'offset':offset,'crc':crc,'content_hash':hashlib.sha256(raw).hexdigest()}

    def test_zip_copy_uses_identity_and_never_member_as_destination(self):
        path=self.root/'test.zip';raw=b'synthetic video bytes'
        with zipfile.ZipFile(path,'w') as z:z.writestr('../../escape.mp4',raw)
        with zipfile.ZipFile(path) as z:info=z.infolist()[0]
        row=self.row(path,raw,info.filename,info.header_offset,info.CRC)
        copy=verified_copy(row,self.root/'cache');self.assertEqual(copy.read_bytes(),raw)
        self.assertEqual(copy.parent,self.root/'cache');self.assertFalse((self.root/'escape.mp4').exists())
        row['content_hash']='0'*64
        with self.assertRaises(ValueError):verified_copy(row,self.root/'bad-cache')
        self.assertFalse((self.root/'bad-cache'/('0'*64+'.source')).exists())

    def test_actual_ffmpeg_prepares_complete_clip_and_receipt(self):
        path=self.root/'synthetic.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=160x120:r=10','-t','1','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
        raw=path.read_bytes();row=self.row(path,raw);target=prepare([row],self.root/'cache')
        self.assertTrue(target.stat().st_size>0);self.assertEqual(path.read_bytes(),raw)
        receipt=json.loads(target.with_suffix('.json').read_text());self.assertEqual(receipt['content_hash'],row['content_hash'])
        playback=Playback(self.root/'cache');self.assertEqual(playback.status(row['content_hash'])['state'],'ready')
        self.assertEqual(playback.file(row['content_hash']),target)

    def test_http_range_streams_only_requested_bytes_and_refuses_invalid_ranges(self):
        path=self.root/'fixture.mp4';path.write_bytes(b'0123456789')
        for header,expected_status,body in [('bytes=2-5',206,b'2345'),('bytes=-3',206,b'789'),('bytes=8-',206,b'89'),(None,200,b'0123456789'),('bytes=50-',416,b''),('bytes=0-2,4-5',416,b'')]:
            instance=object.__new__(handler(None,8771));instance.headers={'Range':header} if header else {};instance.wfile=io.BytesIO();headers={};statuses=[]
            instance.send_response=lambda status:statuses.append(status);instance.send_header=lambda k,v:headers.update({k:v});instance.end_headers=lambda:None
            instance.send_video(path)
            self.assertEqual(statuses,[expected_status]);self.assertEqual(instance.wfile.getvalue(),body)
            self.assertEqual(int(headers['Content-Length']),len(body))
        with self.assertRaises(ValueError):byte_range('bytes=-0',10)


class LongPlaybackTests(unittest.TestCase):
    setUp=VideoTests.setUp
    row=VideoTests.row
    def fixture(self,codec='libx264',duration=7210):
        path=self.root/'long.mkv'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=32x32:r=1/10','-t',str(duration),'-c:v',codec,'-threads','1','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
        raw=path.read_bytes()
        return path,raw,self.row(path,raw)

    def test_long_copy_preserves_packets_and_supports_late_seeking(self):
        path,raw,row=self.fixture();target=prepare([row],self.root/'cache')
        receipt=json.loads(target.with_suffix('.json').read_text())
        self.assertEqual(receipt['method'],'stream-copy');self.assertEqual(probe(target),7210)
        self.assertEqual(path.read_bytes(),raw)
        def packet_count(path):
            r=subprocess.run(['ffprobe','-v','error','-count_packets','-select_streams','v:0','-show_entries','stream=nb_read_packets','-of','json',str(path)],check=True,capture_output=True)
            return json.loads(r.stdout)['streams'][0]['nb_read_packets']
        self.assertEqual(packet_count(path),packet_count(target))
        r=subprocess.run(['ffmpeg','-v','error','-ss','7190','-i',str(target),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],check=True,capture_output=True)
        self.assertEqual(len(r.stdout),32*32*3)

    def test_incompatible_long_video_does_not_launch_transcode(self):
        path,raw,row=self.fixture(codec='mpeg4')
        with self.assertRaisesRegex(ValueError,'H.264'):prepare([row],self.root/'cache')
        self.assertFalse(list((self.root/'cache').glob('*.mp4')))
        self.assertEqual(path.read_bytes(),raw)

    def test_long_copy_keeps_derivative_space_reserve(self):
        path,raw,row=self.fixture();cache=self.root/'cache';verified_copy(row,cache)
        with patch('src.video.shutil.disk_usage',return_value=type('Space',(),{'free':12*1024**3+row['size']})()):
            with self.assertRaisesRegex(RuntimeError,'space'):prepare([row],cache)
        self.assertFalse(list(cache.glob('*.mp4')))

    def test_duration_over_six_hours_remains_unsupported(self):
        path,raw,row=self.fixture(duration=21610)
        with self.assertRaisesRegex(ValueError,'duration'):prepare([row],self.root/'cache')
        self.assertFalse(list((self.root/'cache').glob('*.mp4')))


class LongAudioTests(unittest.TestCase):
    setUp=VideoTests.setUp
    row=VideoTests.row
    fixture=LongPlaybackTests.fixture

    def ac3_fixture(self):
        path,raw,row=self.fixture()
        source=self.root/'long-audio.mkv'
        # Sparse long video with one second of synthetic audio exercises muxing cheaply.
        subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','lavfi','-t','1','-i','anullsrc=r=32000:cl=5.1','-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','ac3','-threads','1',str(source)],check=True,capture_output=True)
        raw=source.read_bytes()
        return source,raw,self.row(source,raw)

    def test_long_ac3_converts_audio_and_keeps_video(self):
        path,raw,row=self.ac3_fixture();target=prepare([row],self.root/'cache')
        receipt=json.loads(target.with_suffix('.json').read_text())
        self.assertEqual(receipt['method'],'video-copy-audio-transcode')
        self.assertIn('stereo',receipt['settings']);self.assertEqual(path.read_bytes(),raw)
        def streams(path):
            r=subprocess.run(['ffprobe','-v','error','-count_packets','-show_entries','stream=codec_type,codec_name,channels,nb_read_packets','-of','json',str(path)],check=True,capture_output=True)
            return json.loads(r.stdout)['streams']
        before,after=streams(path),streams(target)
        self.assertEqual(before[0]['nb_read_packets'],after[0]['nb_read_packets'])
        self.assertEqual(after[1]['codec_name'],'aac');self.assertEqual(after[1]['channels'],2)
        self.assertAlmostEqual(probe(path),probe(target),delta=1)

    def test_missing_converted_audio_is_not_published(self):
        path,raw,row=self.ac3_fixture();run=subprocess.run
        def without_audio(command,**kwargs):
            if command[0]=='ffmpeg':command=command[:-1]+['-an',command[-1]]
            return run(command,**kwargs)
        with patch('src.video.subprocess.run',side_effect=without_audio):
            with self.assertRaisesRegex(RuntimeError,'audio'):prepare([row],self.root/'cache')
        self.assertFalse((self.root/'cache'/(row['content_hash']+'.json')).exists())
        self.assertFalse((self.root/'cache'/(row['content_hash']+'.mp4')).exists())
