"""One bounded timeout retry preserves successes and prior failure records."""
import json
import sqlite3
import subprocess
import unittest
from tests import test_ocr
from src import ocr

class OCRRetryTests(unittest.TestCase):
    def test_successful_retry_keeps_initial_error_and_source_evidence(self):
        f=test_ocr.OCRTests();f.setUp()
        class Initial(test_ocr.FakeOCR):
            def extract(self,image):raise subprocess.TimeoutExpired('synthetic',45)
        ocr.index(f.f.db,f.output,Initial(),reader=lambda row:'fixture')
        class Recovery(test_ocr.FakeOCR):
            def extract(self,image):raise AssertionError('Only timeout retry is expected')
            def retry(self,image):return test_ocr.FakeOCR().extract(image)
        first=ocr.index(f.f.db,f.output,Recovery(),limit=1,reader=lambda row:'fixture')
        self.assertEqual(first['remaining'],2)
        result=ocr.index(f.f.db,f.output,Recovery(),reader=lambda row:'fixture')
        self.assertEqual((result['indexed'],result['errors']),(3,0))
        self.assertEqual(ocr.index(f.f.db,f.output,Recovery(),reader=lambda row:'fixture')['processed_this_run'],0)
        with sqlite3.connect(f.output) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM ocr_errors').fetchone()[0],3)
            self.assertEqual(c.execute('SELECT count(*) FROM ocr_retry_work WHERE error IS NULL').fetchone()[0],3)
            self.assertEqual(json.loads(c.execute('SELECT source_span FROM ocr_facts LIMIT 1').fetchone()[0])['timeout_seconds'],90)
        self.assertEqual(f.viewer.progress()['ocr']['errors'],0)

    def test_failed_retry_stops(self):
        f=test_ocr.OCRTests();f.setUp()
        class Backend(test_ocr.FakeOCR):
            def extract(self,image):raise subprocess.TimeoutExpired('synthetic',45)
            def retry(self,image):raise subprocess.TimeoutExpired('synthetic',90)
        backend=Backend();ocr.index(f.f.db,f.output,backend,reader=lambda row:'fixture')
        result=ocr.index(f.f.db,f.output,backend,reader=lambda row:'fixture');self.assertEqual(result['errors'],3)
        self.assertEqual(ocr.index(f.f.db,f.output,backend,reader=lambda row:'fixture')['processed_this_run'],0)
        with sqlite3.connect(f.output) as c:self.assertEqual(c.execute('SELECT count(*) FROM ocr_retry_work').fetchone()[0],3)
