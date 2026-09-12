import hashlib,tempfile,unittest
from pathlib import Path
from src.export import Exporter,safe_name
from test_library import LibraryTests

class ExportTests(unittest.TestCase):
    def build(self):
        f=LibraryTests();f.setUp()
        if hasattr(f,'tearDown'):self.addCleanup(f.tearDown)
        return f.build()

    def test_each_bucket_becomes_a_folder_of_verified_copies(self):
        library=self.build();items=library.search()['items'];first,second=items[0],items[1]
        bucket=library.organize('bucket',{'bucket':'','name':'Cute videos: little Jacob?'})['data']['bucket']
        library.organize('bucket_add',{'bucket':bucket,'contents':[first['content_hash'],second['content_hash']]})
        library.organize('bucket',{'bucket':'','name':'Empty'})
        with tempfile.TemporaryDirectory() as tmp:
            destination=Path(tmp)/'Buckets';exporter=Exporter()
            exporter.start(library,str(destination));exporter.thread.join(30)
            status=exporter.snapshot()
            self.assertEqual(status['state'],'done',status)
            self.assertEqual((status['copied'],status['existing'],status['failed'],status['total']),(2,0,0,2))
            folder=destination/'Cute videos little Jacob'
            self.assertTrue(folder.is_dir());self.assertTrue((destination/'Empty').is_dir())
            copies=sorted(folder.iterdir())
            self.assertEqual({hashlib.sha256(c.read_bytes()).hexdigest() for c in copies},{first['content_hash'],second['content_hash']})
            self.assertFalse(list(folder.glob('*.part')))
            again=Exporter();again.start(library,str(destination));again.thread.join(30)
            self.assertEqual((again.snapshot()['copied'],again.snapshot()['existing']),(0,2))
            self.assertEqual(sorted(folder.iterdir()),copies)

    def test_destination_never_points_into_the_archive_or_catalog(self):
        library=self.build();exporter=Exporter()
        library.organize('bucket',{'bucket':'','name':'Anything'})
        for bad in (str(library.source),str(Path(library.source)/'sub'),str(library.catalog_directory),'relative/path',''):
            with self.assertRaises(ValueError):exporter.start(library,bad)
        self.assertEqual(exporter.snapshot()['state'],'idle')

    def test_folder_names_are_filesystem_safe(self):
        self.assertEqual(safe_name('  A/B:C*D?  ','x'),'A B C D')
        self.assertEqual(safe_name('...','fallback'),'fallback')
        self.assertEqual(len(safe_name('n'*200,'x')),80)


    def test_missing_parent_names_the_folder_suggests_case_twin_and_can_create(self):
        library=self.build();exporter=Exporter()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'Pictures').mkdir()
            with self.assertRaises(ValueError) as caught:exporter.check_destination(library,str(Path(tmp)/'pictures'/'buckets'))
            self.assertIn('Did you mean',str(caught.exception));self.assertIn(str(Path(tmp)/'Pictures'/'buckets'),str(caught.exception))
            with self.assertRaises(ValueError) as caught:exporter.check_destination(library,str(Path(tmp)/'new'/'deeper'/'buckets'))
            self.assertIn(str(Path(tmp)/'new'),str(caught.exception));self.assertIn('Create folders',str(caught.exception))
            self.assertEqual(exporter.check_destination(library,str(Path(tmp)/'new'/'deeper'/'buckets'),create_parents=True),Path(tmp)/'new'/'deeper'/'buckets')

if __name__=='__main__':unittest.main()
