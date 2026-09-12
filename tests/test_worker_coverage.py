"""Coverage describes current suggestions and separates videos from excerpts."""
from contextlib import closing
import sqlite3
import unittest
from src import quality
from tests import test_library,test_transcript_chunks


class CoverageTests(unittest.TestCase):
    def test_quality_provenance_missing_failure_and_historical_models(self):
        f=test_library.LibraryTests();f.setUp();v=f.build();db=f.f.root/'quality.db'
        self.assertIsNone(v.quality_details(v.items[0]['id']));self.assertFalse(v.progress()['quality']['available'])
        class Backend:
            identity='synthetic-current'
            def score(self,image):return 73.25
        quality.index(f.f.db,db,Backend(),reader=lambda row:object())
        key=v.items[0]['id'];detail=v.metadata(key)['quality']
        self.assertEqual(detail['status'],'complete');self.assertEqual(detail['fact']['score'],73.25);self.assertIn('source_path',detail['fact']);self.assertIn('source_span',detail['fact'])
        with closing(sqlite3.connect(db)) as c:
            c.execute("INSERT INTO quality_work VALUES('historic','old','error','OSError','then')")
            c.execute("UPDATE quality_work SET status='error' WHERE content_hash=? AND model=?",(v.items[0]['content_hash'],'synthetic-current'))
            c.execute("UPDATE quality_facts SET model='old' WHERE content_hash=?",(v.items[0]['content_hash'],));c.commit()
        detail=v.quality_details(key);self.assertEqual(detail['status'],'error');self.assertIsNone(detail['fact'])
        progress=v.progress()['quality'];self.assertEqual(progress['contents'],2);self.assertEqual(progress['errors'],1);self.assertEqual(progress['attempted'],3)
        with closing(sqlite3.connect(db)) as c:c.execute("UPDATE settings SET value='not-yet-processed' WHERE key='quality_current'");c.commit()
        detail=v.quality_details(key);self.assertEqual(detail['status'],'pending');self.assertIsNone(detail['fact']);self.assertEqual(v.progress()['quality']['contents'],0)
    def test_current_transcripts_count_partial_videos_and_failed_chunks(self):
        f=test_transcript_chunks.ChunkTests();f.fixture();f.run_batch()
        with closing(sqlite3.connect(f.db)) as c:
            c.execute("INSERT INTO transcript_work VALUES('historic','old','error',0,'ValueError','then')")
            c.execute("UPDATE transcript_chunks SET status='error',error='ValueError' WHERE start=600")
            c.commit()
        result=f.library.progress()['transcripts']
        self.assertEqual(result['contents'],1);self.assertEqual(result['partial'],1);self.assertEqual(result['errors'],0);self.assertEqual(result['completed'],0);self.assertEqual(result['segments'],1)
        self.assertEqual(result['chunks'],{'total':13,'completed':1,'failed':1,'pending':11})
    def test_fingerprint_coverage_excludes_historical_models_and_separates_flat_inputs(self):
        from src import fingerprints
        from tests.test_fingerprints import scene
        f=test_library.LibraryTests();f.setUp();v=f.build();db=f.f.root/'fingerprints.db'
        self.assertFalse(v.progress()['fingerprints']['available'])
        backend=fingerprints.Fingerprint();fingerprints.index(f.f.db,db,backend,reader=lambda row:scene())
        with closing(sqlite3.connect(db)) as c:
            c.execute("UPDATE fingerprints_facts SET gray_range=0 WHERE content_hash=(SELECT min(content_hash) FROM fingerprints_facts)")
            c.execute("INSERT INTO fingerprints_work VALUES('historic','old','error','OSError','then')");c.commit()
        result=v.progress()['fingerprints'];self.assertEqual(result['contents'],3);self.assertEqual(result['flat'],1);self.assertEqual(result['errors'],0);self.assertEqual(result['attempted'],3)

    def test_uninitialized_optional_stores_are_unavailable_without_mutation(self):
        f=test_library.LibraryTests();f.setUp();v=f.build();(f.f.root/'quality.db').touch()
        self.assertIsNone(v.quality_details(v.items[0]['id']));self.assertFalse(v.progress()['quality']['available']);self.assertEqual((f.f.root/'quality.db').stat().st_size,0)


if __name__=='__main__':unittest.main()
