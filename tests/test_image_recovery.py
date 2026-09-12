"""Strict recovery keeps source provenance and never silently repeats failures."""
from contextlib import closing
import hashlib,json,sqlite3,unittest
from unittest.mock import patch
from PIL import Image
from src import image_recovery as recovery,quality
from src.kit import validate_row
from tests import test_quality


class RecoveryTests(unittest.TestCase):
    def fixture(self):
        f=test_quality.QualityTests();f.setUp()
        def failed(row):raise OSError('synthetic decode failure')
        quality.index(f.f.db,f.output,test_quality.Backend(),reader=failed)
        self.f=f;self.out=f.f.root/'image-recovery.db'

    def test_finite_work_and_retained_original_errors(self):
        self.fixture()
        class Decoder:
            identity='synthetic-recovery';contract={'policy':'fixture'}
            def decode(self,source,output):
                Image.new('RGB',(4,3),'red').save(output)
                return {'width':4,'height':3,'raw_exif_orientation':None}
        decoder=Decoder()
        result=recovery.index(self.f.f.db,self.f.output,self.out,decoder,limit=1)
        self.assertEqual(result['processed_this_run'],1);self.assertEqual(result['remaining'],2)
        self.assertEqual(recovery.index(self.f.f.db,self.f.output,self.out,decoder)['statuses'],{'complete':3})
        with patch.object(decoder,'decode',side_effect=AssertionError('Repeated')):
            self.assertEqual(recovery.index(self.f.f.db,self.f.output,self.out,decoder)['processed_this_run'],0)
        with closing(sqlite3.connect(self.out)) as c:
            c.row_factory=sqlite3.Row
            for row in c.execute('SELECT * FROM image_recovery_facts'):
                row=dict(row);validate_row('image_recovery_facts',row)
                self.assertEqual(recovery.checksum(row['artifact_path']),row['artifact_sha256'])
                self.assertEqual(json.loads(row['source_span'])['original_error'],'OSError')
        with closing(sqlite3.connect(self.f.output)) as c:self.assertEqual(c.execute("SELECT count(*) FROM quality_work WHERE status='error'").fetchone()[0],3)

    def test_interrupted_and_failed_attempts_stay_terminal(self):
        self.fixture()
        class Decoder:
            identity='synthetic-interrupted';contract={}
            def decode(self,*args):raise KeyboardInterrupt()
        decoder=Decoder()
        with self.assertRaises(KeyboardInterrupt):recovery.index(self.f.f.db,self.f.output,self.out,decoder,limit=1)
        with patch.object(decoder,'decode',side_effect=ValueError('Bad pixels')):
            self.assertEqual(recovery.index(self.f.f.db,self.f.output,self.out,decoder)['statuses'],{'error':3})
            self.assertEqual(recovery.index(self.f.f.db,self.f.output,self.out,decoder)['processed_this_run'],0)
        with closing(sqlite3.connect(self.out)) as c:self.assertEqual(c.execute("SELECT count(*) FROM image_recovery_work WHERE error='InterruptedAttempt'").fetchone()[0],1)

    def test_real_strict_decoder_native_size_orientation_and_rejections(self):
        self.fixture();root=self.f.f.root;decoder=recovery.StrictJPEG()
        source=root/'synthetic.jpg';im=Image.new('RGB',(30,20),'red');exif=Image.Exif();exif[274]=6;im.save(source,exif=exif)
        result=decoder.decode(source,root/'oriented.png')
        self.assertEqual(result['raw_exif_orientation'],6);self.assertEqual((result['width'],result['height']),(20,30))
        source.write_bytes(source.read_bytes()[:200])
        with self.assertRaises(Exception):decoder.decode(source,root/'bad.png')
        source.write_bytes(b'not an image')
        with self.assertRaises(ValueError):decoder.decode(source,root/'unknown.png')

    def test_source_identity_mismatch_never_reaches_decoder(self):
        self.fixture()
        with closing(sqlite3.connect(self.f.f.db)) as c:
            c.row_factory=sqlite3.Row;row=dict(c.execute('SELECT * FROM occurrences WHERE present=1 LIMIT 1').fetchone())
        row['content_hash']='0'*64
        decoder=recovery.StrictJPEG()
        with patch.object(decoder,'decode') as decode:
            with self.assertRaises(ValueError):recovery.prepare(row,self.f.f.root/'mismatch',decoder)
            decode.assert_not_called()
        with self.assertRaises(ValueError):recovery.index(self.f.f.db,self.f.output,self.f.output,decoder)


if __name__=='__main__':unittest.main()
