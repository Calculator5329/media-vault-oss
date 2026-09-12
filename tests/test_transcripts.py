"""Timestamp search preserves the video identity and source evidence."""
from contextlib import contextmanager
import io
import json
import unittest
from src import transcripts
from src.library import Library
from tests import test_library


def transcript():return {'duration':20,'language':'en','status':'complete','segments':[{'start':1.,'end':3.,'text':'the red car is here','avg_logprob':-.2,'no_speech_prob':.01},{'start':10.,'end':12.,'text':'the red car leaves','avg_logprob':-.3,'no_speech_prob':.02}]}


class TranscriptTests(unittest.TestCase):
    def test_timestamp_search_resume_and_source_provenance(self):
        f=test_library.LibraryTests();f.setUp();(f.f.source/'clip.mp4').write_bytes(b'synthetic video');f.f.refresh();v=f.build()
        class Backend:
            identity='synthetic-transcript'
            def transcribe(self,stream):return transcript()
        @contextmanager
        def reader(row):yield io.BytesIO(b'synthetic')
        db=f.f.root/'transcripts.db';result=transcripts.index(f.f.db,db,Backend(),reader=reader)
        self.assertEqual(result['processed_videos'],1)
        found=v.moments('red car');self.assertEqual(found['total'],2);self.assertEqual([i['moment']['start'] for i in found['items']],[1,10])
        detail=v.metadata(found['items'][0]['id'])['transcript'];self.assertEqual(detail['status'],'complete');self.assertEqual(len(detail['segments']),2)
        self.assertEqual(json.loads(detail['segments'][0]['source_span'])['start_seconds'],1)
        self.assertEqual(v.moments('red car',year='2020')['total'],0)
        self.assertEqual(transcripts.index(f.f.db,db,Backend(),reader=reader)['processed_this_run'],0)

    def test_invalid_timestamps_refuse_and_verified_stream_detects_source_changes(self):
        value=transcript();value['segments'][0]['end']=30
        with self.assertRaises(ValueError):transcripts.validate(value)
        f=test_library.LibraryTests();f.setUp();f.build()
        with f.f.connect() as conn:row=dict(conn.execute("SELECT * FROM occurrences WHERE member='' LIMIT 1").fetchone())
        with transcripts.video_stream(row) as stream:self.assertEqual(stream.read(),b'same image bytes')
        row['content_hash']='0'*64
        with self.assertRaises(ValueError):
            with transcripts.video_stream(row):pass
