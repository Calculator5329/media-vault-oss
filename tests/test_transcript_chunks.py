"""Synthetic long-video checkpoints; no real speech leaves the local pipeline."""
from contextlib import contextmanager,closing
import io
import json
import sqlite3
import unittest
from unittest.mock import patch
from src import transcripts,transcript_chunks as chunks
from tests import test_library


class Backend:
    identity='synthetic-chunks'
    chunk_long_videos=True
    def transcribe(self,stream):
        window=json.loads(stream.read())
        return {'duration':window,'language':'en','status':'complete','segments':[{'start':1.,'end':min(3.,window),'text':'synthetic red car','avg_logprob':-.2,'no_speech_prob':.01}]}


class ChunkTests(unittest.TestCase):
    def fixture(self,duration=7205):
        fixture=test_library.LibraryTests();fixture.setUp()
        (fixture.f.source/'long.mp4').write_bytes(b'synthetic long video');fixture.f.refresh()
        library=fixture.build();self.root=fixture.f.root;self.imports=fixture.f.db;self.db=self.root/'transcripts.db';self.backend=Backend();self.library=library
        with closing(sqlite3.connect(self.imports)) as conn:
            conn.row_factory=sqlite3.Row;self.row=dict(conn.execute("SELECT * FROM occurrences WHERE kind='video'").fetchone());self.digest=self.row['content_hash']
        @contextmanager
        def fail(row):raise ValueError('synthetic long input');yield
        transcripts.index(self.imports,self.db,self.backend,reader=fail)
        with closing(sqlite3.connect(self.root/'frames.db')) as conn:
            conn.execute('CREATE TABLE frame_facts(content_hash TEXT,duration REAL)');conn.execute('INSERT INTO frame_facts VALUES(?,?)',(self.digest,duration));conn.commit()
        self.windows=[]
    @contextmanager
    def reader(self,row,start,end,duration,cache):
        self.windows.append((start,end));yield io.BytesIO(json.dumps(end-start).encode())
    def run_batch(self,limit=1,reader=None):
        return transcripts.index(self.imports,self.db,self.backend,limit=limit,chunk_reader=reader or self.reader)
    def test_partial_offsets_restart_atomicity_and_final_short_chunk(self):
        self.fixture();r=self.run_batch();self.assertEqual(r['remaining'],1);self.assertEqual(r['chunks_this_run'],1)
        key=self.library.by_content[self.digest]['id'];detail=self.library.transcript(key)
        self.assertEqual(detail['status'],'partial');self.assertEqual(detail['coverage'],{'completed':1,'total':13,'pending':12,'failed':0,'window_seconds':600})
        self.assertEqual(self.library.moments('red car')['items'][0]['moment']['transcript_status'],'partial')
        self.run_batch();self.assertEqual([s['start_seconds'] for s in self.library.transcript(key)['segments']],[1,601])
        self.run_batch(limit=20);detail=self.library.transcript(key)
        self.assertEqual(detail['status'],'complete');self.assertEqual(len(detail['segments']),13);self.assertEqual(self.windows[-1],(7200,7205));self.assertEqual(detail['segments'][-1]['end_seconds'],7203)
        self.assertEqual(json.loads(detail['segments'][-1]['source_span'])['chunk_start'],7200)
        self.assertEqual(self.run_batch()['chunks_this_run'],0)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT error,policy FROM transcript_attempt_history').fetchall(),[('ValueError',chunks.POLICY)])
    def test_interrupted_and_invalid_chunks_stay_failed_without_duplicates(self):
        self.fixture();self.run_batch()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE transcript_chunks SET status='running' WHERE start=600");conn.commit()
        def invalid(stream):
            value=Backend().transcribe(stream);value['segments'][0]['end']=601.5;return value
        with patch.object(self.backend,'transcribe',side_effect=invalid):self.run_batch()
        self.run_batch(limit=20)
        detail=self.library.transcript(self.library.by_content[self.digest]['id']);self.assertEqual(detail['status'],'partial');self.assertEqual(detail['coverage']['failed'],2);self.assertEqual(len(detail['segments']),11)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT error FROM transcript_chunks WHERE start=600").fetchone()[0],'InterruptedAttempt')
        self.assertEqual(self.run_batch()['chunks_this_run'],0)
    def test_no_speech_is_not_failed_source_and_pending_work_is_finite(self):
        self.fixture()
        def silent(stream):return {'duration':json.loads(stream.read()),'language':None,'status':'no_speech','segments':[]}
        with patch.object(self.backend,'transcribe',side_effect=silent):self.run_batch(limit=20)
        detail=self.library.transcript(self.library.by_content[self.digest]['id']);self.assertEqual(detail['status'],'no_speech');self.assertEqual(detail['coverage']['failed'],0)
        self.fixture()
        @contextmanager
        def changed(*args):raise ValueError('Source identity changed');yield
        r=self.run_batch(limit=20,reader=changed);self.assertEqual(r['remaining'],0)
        detail=self.library.transcript(self.library.by_content[self.digest]['id']);self.assertEqual(detail['status'],'error');self.assertEqual(detail['coverage']['failed'],13)
    def test_inconsistent_or_oversized_sources_do_not_enroll(self):
        self.fixture()
        with closing(sqlite3.connect(self.root/'frames.db')) as conn:
            conn.execute('INSERT INTO frame_facts VALUES(?,?)',(self.digest,8000));conn.commit()
        self.assertEqual(self.run_batch()['chunks_this_run'],0)
        self.fixture()
        with closing(sqlite3.connect(self.imports)) as conn:
            conn.execute('UPDATE occurrences SET size=? WHERE content_hash=?',(5*1024**3,self.digest));conn.commit()
        self.assertEqual(self.run_batch()['chunks_this_run'],0)
    def test_existing_whisper_tolerance_is_bounded_by_final_source_extent(self):
        self.fixture()
        def boundary(stream):
            value=Backend().transcribe(stream);value['segments'][0]['end']=value['duration']+.52;return value
        with patch.object(self.backend,'transcribe',side_effect=boundary):self.run_batch(limit=20)
        detail=self.library.transcript(self.library.by_content[self.digest]['id'])
        self.assertEqual(detail['status'],'complete');self.assertAlmostEqual(detail['segments'][-1]['end_seconds'],7205.52)
        self.assertTrue(all(s['end_seconds']<=7206 for s in detail['segments']))

    def test_extraction_refuses_source_mismatch_before_ffmpeg(self):
        self.fixture();self.row['content_hash']='0'*64
        with patch('src.transcript_chunks.subprocess.run') as run:
            with self.assertRaises(ValueError):
                with chunks.excerpt(self.row,0,600,7205,self.root):pass
            run.assert_not_called()


if __name__=='__main__':unittest.main()
