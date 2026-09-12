"""A terminated attempt is retained and does not trap later work behind it."""
from contextlib import contextmanager
import io
import sqlite3
import unittest
from src import transcripts
from tests import test_library,test_transcripts

class TranscriptInterruptionTests(unittest.TestCase):
    def test_interrupted_input_is_visible_and_later_input_completes(self):
        f=test_library.LibraryTests();f.setUp()
        (f.f.source/'first.mp4').write_bytes(b'short')
        (f.f.source/'second.mp4').write_bytes(b'longer synthetic')
        f.f.refresh();f.build();db=f.f.root/'transcripts.db'
        class Interrupted:
            identity='synthetic-interruption'
            def transcribe(self,stream):raise KeyboardInterrupt()
        @contextmanager
        def reader(row):yield io.BytesIO(b'synthetic')
        with self.assertRaises(KeyboardInterrupt):transcripts.index(f.f.db,db,Interrupted(),reader=reader)
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute('SELECT status FROM transcript_work').fetchone()[0],'running')
            self.assertEqual(c.execute('SELECT count(*) FROM transcript_facts').fetchone()[0],0)
        class Healthy:
            identity=Interrupted.identity
            def transcribe(self,stream):return test_transcripts.transcript()
        result=transcripts.index(f.f.db,db,Healthy(),reader=reader)
        self.assertEqual((result['processed_this_run'],result['processed_videos'],result['remaining']),(1,2,0))
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM transcript_work WHERE error='InterruptedAttempt' AND status='error'").fetchone()[0],1)
            self.assertEqual(c.execute('SELECT count(*) FROM transcript_facts').fetchone()[0],2)
        self.assertEqual(transcripts.index(f.f.db,db,Healthy(),reader=reader)['processed_this_run'],0)
