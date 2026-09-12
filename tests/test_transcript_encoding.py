"""A scoped encoding retry keeps the prior failure and never loops."""
from contextlib import contextmanager
import io
import sqlite3
import unittest
from src import transcripts
from tests import test_library

class TranscriptEncodingTests(unittest.TestCase):
    def fixture(self):
        f=test_library.LibraryTests();f.setUp();(f.f.source/'clip.mp4').write_bytes(b'synthetic video');f.f.refresh();f.build()
        return f,f.f.root/'transcripts.db'

    def test_recovered_attempt_preserves_original_error(self):
        f,db=self.fixture()
        class Original:
            identity='encoding-test'
            def transcribe(self,stream):raise UnicodeDecodeError('utf8',b'\xff',0,1,'synthetic')
        @contextmanager
        def reader(row):yield io.BytesIO(b'synthetic')
        transcripts.index(f.f.db,db,Original(),reader=reader)
        class Recovery:
            identity=Original.identity;retry_policy='encoding-1'
            def transcribe(self,stream):return {'duration':15,'language':None,'segments':[],'status':'no_speech'}
        self.assertEqual(transcripts.index(f.f.db,db,Recovery(),reader=reader)['processed_this_run'],1)
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute('SELECT status,error FROM transcript_work').fetchone(),('no_speech',None))
            self.assertEqual(c.execute('SELECT status,error,policy FROM transcript_attempt_history').fetchone(),('error','UnicodeDecodeError','encoding-1'))
        self.assertEqual(transcripts.index(f.f.db,db,Recovery(),reader=reader)['processed_this_run'],0)

    def test_failed_retry_is_not_repeated(self):
        f,db=self.fixture()
        class Backend:
            identity='encoding-failure';retry_policy='encoding-1'
            def transcribe(self,stream):raise UnicodeDecodeError('utf8',b'\xff',0,1,'synthetic')
        @contextmanager
        def reader(row):yield io.BytesIO(b'synthetic')
        for _ in range(2):self.assertEqual(transcripts.index(f.f.db,db,Backend(),reader=reader)['processed_this_run'],1)
        self.assertEqual(transcripts.index(f.f.db,db,Backend(),reader=reader)['processed_this_run'],0)
        with sqlite3.connect(db) as c:self.assertEqual(c.execute('SELECT count(*) FROM transcript_attempt_history').fetchone()[0],1)
