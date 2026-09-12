"""Only measured duration ranges get one retry, with earlier failures retained."""
from contextlib import contextmanager
import io
import sqlite3
import unittest
from src import transcripts
from tests import test_library,test_transcripts

class TranscriptRetryTests(unittest.TestCase):
    def test_scoped_retry_retains_failure_and_validates_result(self):
        f=test_library.LibraryTests();f.setUp();(f.f.source/'clip.mp4').write_bytes(b'synthetic video');f.f.refresh();f.build();db=f.f.root/'transcripts.db'
        class Initial:
            identity='duration-retry'
            def transcribe(self,stream):raise ValueError('Invalid timestamps')
        @contextmanager
        def reader(row):yield io.BytesIO(b'synthetic')
        transcripts.index(f.f.db,db,Initial(),reader=reader)
        with sqlite3.connect(db) as c:digest=c.execute('SELECT content_hash FROM transcript_work').fetchone()[0]
        with sqlite3.connect(f.f.root/'frames.db') as c:
            c.execute('CREATE TABLE frame_facts(content_hash TEXT,duration REAL)');c.execute('INSERT INTO frame_facts VALUES(?,?)',(digest,20))
        class Retry:
            identity=Initial.identity;retry_timestamps=True
            def transcribe(self,stream):return test_transcripts.transcript()
        result=transcripts.index(f.f.db,db,Retry(),reader=reader)
        self.assertEqual(result['processed_this_run'],1)
        with sqlite3.connect(db) as c:
            self.assertEqual(c.execute('SELECT policy,error FROM transcript_attempt_history').fetchone(),('timestamp-recovery-1','ValueError'))
            self.assertEqual(c.execute('SELECT status FROM transcript_work').fetchone()[0],'complete')
        self.assertEqual(transcripts.index(f.f.db,db,Retry(),reader=reader)['processed_this_run'],0)

    def test_eligibility_excludes_unknown_short_long_large_and_retried(self):
        f=test_library.LibraryTests();f.setUp();frames=f.f.root/'frames.db'
        with sqlite3.connect(frames) as c:
            c.execute('CREATE TABLE frame_facts(content_hash TEXT,duration REAL)')
            c.executemany('INSERT INTO frame_facts VALUES(?,?)',[('good',60),('short',1),('long',7201),('large',60),('retried',60),('mixed',60),('mixed',7201)])
        with sqlite3.connect(':memory:') as c:
            c.execute('CREATE TABLE transcript_work(content_hash TEXT,model TEXT,error TEXT)')
            c.execute('CREATE TABLE transcript_attempt_history(content_hash TEXT,model TEXT,policy TEXT)')
            names=('good','short','long','large','retried','mixed','missing')
            c.executemany('INSERT INTO transcript_work VALUES(?,?,?)',[(n,'test','ValueError') for n in names])
            c.execute("INSERT INTO transcript_attempt_history VALUES('retried','test','timestamp-recovery-1')")
            backend=type('Backend',(),{'identity':'test','retry_timestamps':True})()
            candidates={n:[{'size':5*1024**3 if n=='large' else 100}] for n in names}
            self.assertEqual(transcripts.timestamp_retry_candidates(c,backend,candidates,frames),{'good'})
