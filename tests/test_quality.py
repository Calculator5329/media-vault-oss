"""Quality scores retain identity, explicit failures and bounded recovery state."""
from contextlib import closing
import sqlite3
import unittest
from src import quality
from src.kit import validate_row
from tests import test_library

class Backend:
    identity='synthetic-quality'
    def score(self,image):return 72.5

class QualityTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_library.LibraryTests();self.fixture.setUp();self.viewer=self.fixture.build();self.f=self.fixture.f;self.output=self.f.root/'quality.db'

    def test_bounded_resume_dedup_and_provenance(self):
        first=quality.index(self.f.db,self.output,Backend(),limit=1,reader=lambda row:'fixture')
        self.assertEqual((first['processed_this_run'],first['indexed'],first['remaining']),(1,1,2))
        second=quality.index(self.f.db,self.output,Backend(),reader=lambda row:'fixture')
        self.assertEqual((second['processed_this_run'],second['indexed'],second['remaining']),(2,3,0))
        self.assertEqual(quality.index(self.f.db,self.output,Backend(),reader=lambda row:self.fail('Repeated read'))['processed_this_run'],0)
        with closing(sqlite3.connect(self.output)) as c:
            c.row_factory=sqlite3.Row
            for row in c.execute('SELECT * FROM quality_facts'):
                validate_row('quality_facts',dict(row));self.assertEqual(row['score'],72.5)

    def test_missing_and_nonfinite_scores_remain_errors_not_zero(self):
        class Invalid(Backend):
            def score(self,image):return float('nan')
        result=quality.index(self.f.db,self.output,Invalid(),reader=lambda row:'fixture')
        self.assertEqual((result['indexed'],result['errors'],result['remaining']),(0,3,0))
        self.assertEqual(quality.index(self.f.db,self.output,Backend(),reader=lambda row:self.fail('Implicit retry'))['processed_this_run'],0)
        newer=Backend();newer.identity='new-model'
        self.assertEqual(quality.index(self.f.db,self.output,newer,reader=lambda row:'fixture')['indexed'],3)
        with closing(sqlite3.connect(self.output)) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM quality_work WHERE error='ValueError'").fetchone()[0],3)

    def test_interrupted_attempt_retained_and_other_inputs_advance(self):
        def interrupt(row):raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):quality.index(self.f.db,self.output,Backend(),reader=interrupt)
        result=quality.index(self.f.db,self.output,Backend(),reader=lambda row:'fixture')
        self.assertEqual((result['indexed'],result['errors'],result['remaining']),(2,1,0))
        with closing(sqlite3.connect(self.output)) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM quality_work WHERE error='InterruptedAttempt'").fetchone()[0],1)

    def test_real_reader_verifies_changed_bytes_and_separate_output(self):
        with self.assertRaises(ValueError):quality.index(self.f.db,self.f.db,Backend())
        with closing(sqlite3.connect(self.f.db)) as c:
            c.execute("UPDATE occurrences SET content_hash=?",('0'*64,));c.commit()
        result=quality.index(self.f.db,self.output,Backend())
        self.assertEqual(result['indexed'],0);self.assertGreater(result['errors'],0)
