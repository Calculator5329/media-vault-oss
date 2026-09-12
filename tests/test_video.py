"""Synthetic video preparation, immutable sources and HTTP seeking."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from src.video import prepare,verified_copy,byte_range,Playback,SourceUnavailable,MAX_SOURCE
import time
from src.server import handler
from tests.scratch import scratch


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.root=scratch()

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

    def test_unmounted_archive_reports_disconnected_drive_not_limits(self):
        # A 21 MB H.264 clip was once reported as beyond the local limits while its archive drive was simply not mounted.
        missing=self.root/'unmounted-drive'/'IMG_1679.MOV';raw=b'synthetic video bytes'
        row={'source':str(missing),'source_size':len(raw),'mtime_ns':0,'size':len(raw),'member':'','offset':-1,'crc':None,'content_hash':hashlib.sha256(raw).hexdigest()}
        with self.assertRaises(SourceUnavailable):prepare([row],self.root/'cache')
        playback=Playback(self.root/'cache');result=playback.start(row['content_hash'],[row])
        self.assertEqual(result['state'],'error');self.assertIn('not connected',result['message']);self.assertNotIn('limits',result['message'])
        self.assertIsNone(playback.active);self.assertEqual(playback.status(row['content_hash'],[row])['state'],'error')
        # The drive returns: the stale verdict clears and direct playback is judged afresh instead of from the cached miss.
        missing.parent.mkdir();missing.write_bytes(raw)
        self.assertEqual(playback.status(row['content_hash'],[row])['state'],'pending')
        # The other half: a reachable file that really is over the size limit names that limit.
        big=self.row(missing,raw);big['size']=MAX_SOURCE+1
        self.assertEqual(playback.start(big['content_hash'],[big])['state'],'preparing')
        for _ in range(500):
            if playback.active is None:break
            time.sleep(.01)
        message=playback.status(big['content_hash'],[big])['message']
        self.assertIn('limits',message);self.assertNotIn('not connected',message)

    def test_http_range_streams_only_requested_bytes_and_refuses_invalid_ranges(self):
        path=self.root/'fixture.mp4';path.write_bytes(b'0123456789')
        for header,expected_status,body in [('bytes=2-5',206,b'2345'),('bytes=-3',206,b'789'),('bytes=8-',206,b'89'),(None,200,b'0123456789'),('bytes=50-',416,b''),('bytes=0-2,4-5',416,b'')]:
            instance=object.__new__(handler(None,8771));instance.headers={'Range':header} if header else {};instance.wfile=io.BytesIO();headers={};statuses=[]
            instance.send_response=lambda status:statuses.append(status);instance.send_header=lambda k,v:headers.update({k:v});instance.end_headers=lambda:None
            instance.send_video(path)
            self.assertEqual(statuses,[expected_status]);self.assertEqual(instance.wfile.getvalue(),body)
            self.assertEqual(int(headers['Content-Length']),len(body))
        with self.assertRaises(ValueError):byte_range('bytes=-0',10)


class DirectPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.root=scratch()

    def clip(self,name,*args):
        path=self.root/name
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=160x120:r=10','-t','1',*args,str(path)],check=True,capture_output=True)
        return path

    def test_playable_originals_stream_unchanged_and_others_wait_for_preparation(self):
        from src.video import browser_playable,direct_source
        playable=self.clip('h264.mp4','-c:v','libx264','-pix_fmt','yuv420p');other=self.clip('mpeg4.avi','-c:v','mpeg4')
        self.assertTrue(browser_playable(playable));self.assertFalse(browser_playable(other));self.assertFalse(browser_playable(self.root/'missing.mp4'))
        rows=lambda path:[VideoTests.row(self,path,path.read_bytes())]
        self.assertEqual(direct_source(rows(playable)),playable);self.assertIsNone(direct_source(rows(other)))
        zipped=dict(rows(playable)[0],member='clip.mp4');self.assertIsNone(direct_source([zipped]))
        playback=Playback(self.root/'cache');digest=rows(playable)[0]['content_hash']
        self.assertEqual(playback.status(digest)['state'],'pending')
        status=playback.status(digest,rows(playable));self.assertEqual((status['state'],status.get('direct')),('ready',True))
        self.assertEqual(playback.file(digest,rows(playable)),playable)
        self.assertEqual(playback.status(rows(other)[0]['content_hash'],rows(other))['state'],'pending')
        self.assertFalse((self.root/'cache').exists())

    def test_prepare_reads_plain_files_in_place_and_records_the_encoder(self):
        from src import video
        other=self.clip('mpeg4.avi','-c:v','mpeg4');row=VideoTests.row(self,other,other.read_bytes());raw=other.read_bytes()
        target=prepare([row],self.root/'cache')
        receipt=json.loads(target.with_suffix('.json').read_text())
        self.assertIn(receipt['encoder'],video.ENCODERS);self.assertEqual(receipt['extractor_version'],'2')
        self.assertFalse((self.root/'cache'/(row['content_hash']+'.source')).exists())
        self.assertEqual(other.read_bytes(),raw)
