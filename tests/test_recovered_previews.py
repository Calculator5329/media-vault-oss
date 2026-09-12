"""Recovered previews verify pixels and never borrow original-cache identity."""
from contextlib import closing
import json,sqlite3,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from src import image_recovery as recovery
from tests import test_image_recovery


class RecoveredPreviewTests(unittest.TestCase):
    def fixture(self):
        f=test_image_recovery.RecoveryTests();f.fixture();self.f=f;self.db=f.out;self.viewer=f.f.viewer
        decoder=recovery.StrictJPEG()
        def synthetic(source,output):
            Image.new('RGB',(40,30),(62,90,68)).save(output)
            return {'width':40,'height':30,'raw_exif_orientation':None}
        with patch.object(decoder,'decode',side_effect=synthetic):recovery.index(f.f.f.db,f.f.output,f.out,decoder,limit=1)
        with closing(sqlite3.connect(f.out)) as c:
            c.row_factory=sqlite3.Row;self.fact=dict(c.execute('SELECT * FROM image_recovery_facts').fetchone())
        self.digest=self.fact['content_hash'];self.key=self.viewer.by_content[self.digest]['id']
    def test_preview_and_metadata_use_verified_recovery(self):
        self.fixture();self.viewer.thumbnails.mkdir(parents=True,exist_ok=True)
        original=self.viewer.thumbnails/(self.key+'.jpg');original.write_bytes(b'old preview')
        path=self.viewer.thumbnail(self.key)
        self.assertIn('.recovered-',path.name);self.assertNotEqual(path,original)
        with Image.open(path) as image:self.assertEqual(image.size,(40,30))
        detail=self.viewer.metadata(self.key)['recovered_preview'];self.assertEqual(detail['status'],'ready');self.assertEqual(detail['artifact_sha256'],self.fact['artifact_sha256'])
        self.assertEqual(original.read_bytes(),b'old preview')
        with closing(sqlite3.connect(self.f.f.output)) as c:self.assertEqual(c.execute("SELECT count(*) FROM quality_work WHERE status='error'").fetchone()[0],3)
    def test_artifact_corruption_rejected_even_when_thumbnail_cached(self):
        self.fixture();self.viewer.thumbnail(self.key)
        Path(self.fact['artifact_path']).write_bytes(b'changed recovery pixels')
        with self.assertRaises(ValueError):self.viewer.thumbnail(self.key)
        self.assertEqual(self.viewer.metadata(self.key)['recovered_preview']['status'],'unavailable')
    def test_contract_and_content_mismatch_are_rejected(self):
        self.fixture();original=json.loads(self.fact['details_json'])
        for mutate in (lambda r:r.update(content_hash='0'*64),lambda r:r['contract'].update(policy='invented-policy')):
            record=json.loads(json.dumps(original));mutate(record)
            with closing(sqlite3.connect(self.db)) as c:c.execute('UPDATE image_recovery_facts SET details_json=?',(json.dumps(record),));c.commit()
            with self.assertRaises(ValueError):recovery.load(self.db,self.digest)
    def test_artifact_path_must_remain_inside_recovery_store(self):
        self.fixture();outside=self.f.f.f.root/'outside.png';outside.write_bytes(Path(self.fact['artifact_path']).read_bytes())
        with closing(sqlite3.connect(self.db)) as c:c.execute('UPDATE image_recovery_facts SET artifact_path=?',(str(outside),));c.commit()
        with self.assertRaises(ValueError):recovery.load(self.db,self.digest)
    def test_absent_or_failed_recovery_is_not_presented_as_ready(self):
        self.fixture();self.assertIsNone(recovery.load(self.db,'0'*64))
        with closing(sqlite3.connect(self.db)) as c:c.execute("UPDATE image_recovery_work SET status='error'");c.commit()
        self.assertIsNone(recovery.load(self.db,self.digest))


if __name__=='__main__':unittest.main()
