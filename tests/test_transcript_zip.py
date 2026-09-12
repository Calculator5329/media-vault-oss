"""ZIP speech decoding gets a verified seekable file, retaining source identity."""
import json
import sqlite3
import unittest
from tests import test_library,test_transcripts
from src import transcripts

class TranscriptZIPTests(unittest.TestCase):
    def test_zip_uses_verified_file_and_original_fact_provenance(self):
        import zipfile
        f=test_library.LibraryTests();f.setUp()
        archive=f.f.exports/'takeout-speech-001.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('Google Photos/clip.mp4',b'synthetic speech bytes')
        f.f.refresh();f.build();db=f.f.root/'transcripts.db'
        class Backend:
            identity='synthetic-zip-speech'
            def transcribe(self,stream):
                self.stream_name=stream.name
                stream.seek(-5,2);assert stream.read()==b'bytes'
                stream.seek(0);assert stream.read()==b'synthetic speech bytes'
                return test_transcripts.transcript()
        backend=Backend();result=transcripts.index(f.f.db,db,backend)
        self.assertEqual(result['processed_this_run'],1)
        self.assertTrue(str(backend.stream_name).startswith(str(f.f.root/'playback')))
        with sqlite3.connect(db) as c:
            source,span=c.execute('SELECT source_path,source_span FROM transcript_facts LIMIT 1').fetchone()
        self.assertEqual(source,str(archive));self.assertEqual(json.loads(span)['member'],'Google Photos/clip.mp4')
        with f.f.connect() as c:row=dict(c.execute("SELECT * FROM occurrences WHERE member='Google Photos/clip.mp4'").fetchone())
        row['content_hash']='0'*64
        with self.assertRaises(ValueError):
            with transcripts.transcript_stream(row,f.f.root/'playback'):pass
