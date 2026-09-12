"""OCR words remain linked to verified bytes and empty recognition is resumable."""
import unittest
from tests import test_library
from src import ocr,imports
from src.kit import validate_row


class FakeOCR:
    identity='synthetic-ocr'
    def extract(self,image):
        return {'text':'SYNTHETIC RECEIPT','words':[{'text':'SYNTHETIC','score':.9,'box':[0,0,.2,.2]}],'language':'eng','minimum_search_score':.45}


class OCRTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_library.LibraryTests();self.fixture.setUp();self.viewer=self.fixture.build();self.f=self.fixture.f;self.output=self.f.root/'ocr.db'

    def test_word_geometry_and_low_score_exclusion(self):
        tsv='level\tleft\ttop\twidth\theight\tconf\ttext\n5\t10\t20\t30\t10\t92\tRECEIPT\n5\t0\t0\t5\t5\t12\tnoise\n'
        value=ocr.parse_tsv(tsv,100,100)
        self.assertEqual(value['text'],'RECEIPT');self.assertEqual(len(value['words']),2)
        self.assertEqual(value['words'][0]['box'],[.1,.2,.4,.3])
        with self.assertRaises(ValueError):ocr.parse_tsv(tsv,10,10)

    def test_dedup_resume_search_and_metadata_provenance(self):
        result=ocr.index(self.f.db,self.output,FakeOCR(),reader=lambda row:'fixture')
        self.assertEqual(result['indexed'],3)
        self.assertEqual(ocr.index(self.f.db,self.output,FakeOCR(),reader=lambda row:'fixture')['processed_this_run'],0)
        items=self.viewer.search(query='synthetic receipt')['items'];self.assertEqual(len(items),3)
        fact=self.viewer.metadata(items[0]['id'])['ocr'];self.assertEqual(fact['text'],'SYNTHETIC RECEIPT')
        with imports.database(self.output,[self.f.source,self.f.exports]) as conn:
            conn.row_factory=__import__('sqlite3').Row
            for row in conn.execute('SELECT * FROM ocr_facts'):validate_row('ocr_facts',dict(row))

    def test_failed_reads_are_not_empty_recognition_or_implicitly_retried(self):
        def bad(row):raise ValueError('synthetic bad media')
        result=ocr.index(self.f.db,self.output,FakeOCR(),reader=bad)
        self.assertEqual(result['errors'],3);self.assertEqual(result['indexed'],0)
        self.assertEqual(ocr.index(self.f.db,self.output,FakeOCR(),reader=lambda row:'fixture')['processed_this_run'],0)
