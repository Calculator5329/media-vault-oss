"""Direct version suggestions do not become identity groups or cross model runs."""
from contextlib import closing
import io,json,sqlite3,unittest
from src import fingerprints
from tests import test_library,test_fingerprints,test_server


class VersionTests(unittest.TestCase):
    def fixture(self):
        f=test_library.LibraryTests();f.setUp();v=f.build();self.f=f;self.v=v;self.db=f.f.root/'fingerprints.db'
        fingerprints.index(f.f.db,self.db,fingerprints.Fingerprint(),reader=lambda row:test_fingerprints.scene())
        self.photos=sorted(v.items,key=lambda i:i['content_hash'])
    def test_only_current_direct_present_verified_photos(self):
        self.fixture();a,b,c=self.photos
        with closing(sqlite3.connect(self.db)) as conn:
            for p,h in zip(self.photos,('0000000000000000','00000000000000ff','000000000000ffff')):conn.execute('UPDATE fingerprints_facts SET gray_hash=?,gray_range=40 WHERE content_hash=?',(h,p['content_hash']))
            conn.commit()
        result=self.v.similar_versions(a['id']);self.assertEqual([i['id'] for i in result['items']],[b['id']]);self.assertEqual(result['indexed'],3)
        self.assertEqual(len(self.v.similar_versions(b['id'])['items']),2)
        self.v.by_content.pop(b['content_hash']);self.assertEqual(self.v.similar_versions(a['id'])['total'],0)
        with closing(sqlite3.connect(self.db)) as conn:conn.execute("UPDATE settings SET value='other-recipe' WHERE key='fingerprints_current'");conn.commit()
        self.assertEqual(self.v.similar_versions(a['id'])['state'],'pending')
    def test_missing_flat_failed_and_bounded_results(self):
        self.fixture();key=self.photos[0]['id'];digest=self.photos[0]['content_hash']
        self.assertEqual(len(self.v.similar_versions(key,limit=1)['items']),1)
        with self.assertRaises(ValueError):self.v.similar_versions(key,limit=1000)
        with closing(sqlite3.connect(self.db)) as conn:conn.execute('UPDATE fingerprints_facts SET gray_range=0 WHERE content_hash=?',(digest,));conn.commit()
        self.assertEqual(self.v.similar_versions(key)['state'],'flat');self.assertEqual(self.v.similar_versions(key)['total'],0)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE fingerprints_facts SET model='old' WHERE content_hash=?",(digest,));conn.execute("UPDATE fingerprints_work SET status='error' WHERE content_hash=?",(digest,));conn.commit()
        self.assertEqual(self.v.similar_versions(key)['state'],'failed')
        f=test_library.LibraryTests();f.setUp();v=f.build();self.assertEqual(v.similar_versions(v.items[0]['id'])['state'],'unavailable');self.assertFalse((f.f.root/'fingerprints.db').exists())
    def test_http_contract_and_no_corrections_written(self):
        self.fixture();f=test_server.ViewerTests();f.viewer=self.v
        status,body,_=f.request('/api/similar-versions/'+self.photos[0]['id']);self.assertEqual(status,200);self.assertEqual(json.loads(body)['total'],2)
        self.assertEqual(f.request('/api/similar-versions/'+self.photos[0]['id']+'?limit=1000')[0],400)
        self.assertEqual(f.request('/api/similar-versions/../../etc/passwd')[0],404)
        self.assertFalse((self.f.f.root/'corrections/organization.jsonl').exists())


if __name__=='__main__':unittest.main()
