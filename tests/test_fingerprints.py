"""Fresh feature facts and conservative direct-pair behavior with synthetic images."""
from contextlib import closing
import io,json,sqlite3,unittest
from PIL import Image,ImageDraw
from src import fingerprints
from tests import test_library


def scene(color=(220,35,25)):
    image=Image.new('RGB',(256,192),(20,20,20));d=ImageDraw.Draw(image);d.rectangle((35,50,220,130),fill=color);d.ellipse((50,115,90,155),fill='white');return image


class FingerprintTests(unittest.TestCase):
    def test_recompressed_copy_retained_color_collision_and_flat_images_refused(self):
        backend=fingerprints.Fingerprint()
        with scene() as original,scene((25,90,220)) as other:
            a=backend.describe(original);b=backend.describe(other)
            self.assertEqual(a['gray_hash'],b['gray_hash']);self.assertIsNone(fingerprints.compare(a,b))
            small=original.resize((128,96));stream=io.BytesIO();small.save(stream,format='JPEG',quality=50);small.close();stream.seek(0)
            with Image.open(stream) as compressed:self.assertIsNotNone(fingerprints.compare(a,backend.describe(compressed)))
        with Image.new('RGB',(64,64),'red') as flat:
            value=backend.describe(flat);self.assertIsNone(fingerprints.compare(value,value))
    def test_direct_pairs_do_not_imply_transitive_match_or_accept_invalid_color(self):
        value={'gray_hash':'0000000000000000','gray_range':40,'color_json':json.dumps([.5]*48),'aspect_ratio':1.}
        b={**value,'gray_hash':'00000000000000ff'};c={**value,'gray_hash':'000000000000ffff'}
        self.assertIsNotNone(fingerprints.compare(value,b));self.assertIsNotNone(fingerprints.compare(b,c));self.assertIsNone(fingerprints.compare(value,c))
        for change in ({'color_json':'[NaN]'}, {'aspect_ratio':float('inf')},{'aspect_ratio':2.},{'gray_hash':'oops'}):self.assertIsNone(fingerprints.compare(value,{**value,**change}))
    def test_bounded_resume_keeps_facts_failures_and_current_feature_identity(self):
        f=test_library.LibraryTests();f.setUp();f.build();db=f.f.root/'fingerprints.db';backend=fingerprints.Fingerprint()
        result=fingerprints.index(f.f.db,db,backend,limit=1,reader=lambda row:scene())
        self.assertEqual(result['indexed'],1);self.assertEqual(result['remaining'],2)
        def failed(row):raise OSError('synthetic unreadable')
        result=fingerprints.index(f.f.db,db,backend,reader=failed);self.assertEqual(result['errors'],2)
        self.assertEqual(fingerprints.index(f.f.db,db,backend,reader=lambda row:self.fail('retried terminal attempt'))['processed_this_run'],0)
        with closing(sqlite3.connect(db)) as c:
            c.row_factory=sqlite3.Row;row=dict(c.execute('SELECT * FROM fingerprints_facts').fetchone());self.assertIn('source_path',row);self.assertIn('member',json.loads(row['source_span']));self.assertEqual(row['model'],backend.identity)
            c.execute("UPDATE fingerprints_work SET status='running' WHERE status='error'");c.commit()
        fingerprints.index(f.f.db,db,backend,reader=lambda row:self.fail('retried interrupted attempt'))
        with closing(sqlite3.connect(db)) as c:self.assertEqual(c.execute("SELECT count(*) FROM fingerprints_work WHERE error='InterruptedAttempt'").fetchone()[0],2)
        backend.identity+='-new-recipe'
        result=fingerprints.index(f.f.db,db,backend,reader=lambda row:scene());self.assertEqual(result['indexed'],3)
        with closing(sqlite3.connect(db)) as c:self.assertEqual(c.execute('SELECT count(*) FROM fingerprints_facts').fetchone()[0],4)


if __name__=='__main__':unittest.main()
