"""Verified container types survive inventory while original names stay intact."""
import hashlib
import sqlite3
import subprocess
import unittest
from src import imports,media_types
from src.library import Library
from tests import test_library

class MediaTypeTests(unittest.TestCase):
    def test_misnamed_video_is_verified_reclassified_and_reapplied(self):
        f=test_library.LibraryTests();f.setUp();path=f.f.source/'misnamed.jpg'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=red:s=160x120:r=10','-t','1','-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-f','mp4',str(path)],check=True)
        before=path.read_bytes();f.f.refresh();v=f.build();digest=hashlib.sha256(before).hexdigest();vision=f.f.root/'vision.db'
        with sqlite3.connect(vision) as c:c.execute('CREATE TABLE visual_errors(content_hash TEXT)');c.execute('INSERT INTO visual_errors VALUES(?)',(digest,))
        from src import faces
        class EmptyFaces:
            identity='synthetic-empty-faces'
            def detect(self,image):return []
        faces.index(f.f.db,f.f.root/'faces.db',EmptyFaces(),reader=lambda row:'synthetic')
        faces.publish_groups(f.f.root/'faces.db',EmptyFaces.identity)
        with f.f.connect() as conn:
            result=media_types.recover(conn,vision,f.f.root/'probe-cache')
            self.assertEqual(result['reclassified'],1)
            imports.inventory(conn,f.f.catalog,f.f.exports)
            self.assertEqual(conn.execute('SELECT kind FROM occurrences WHERE content_hash=?',(digest,)).fetchone()[0],'video')
            self.assertEqual(media_types.recover(conn,vision,f.f.root/'probe-cache')['reclassified'],0)
        rebuilt=Library(f.f.catalog,f.f.db,organization=v.organization.path)
        # Videos are face candidates too, but the work recorded while this file passed as a photo does not count: the next face pass redoes it from its samples.
        self.assertEqual(rebuilt.face_review()['processed_photos'],3)
        self.assertEqual(rebuilt.face_review()['candidate_photos'],4)
        item=rebuilt.by_content[digest];self.assertEqual(item['kind'],'video');self.assertEqual(item['name'],'misnamed.jpg');self.assertEqual(item['extension'],'.jpg')
        fact=rebuilt.metadata(item['id'])['detected_type'];self.assertEqual(fact['original_kind'],'photo');self.assertEqual(fact['detected_kind'],'video')
        self.assertEqual(path.read_bytes(),before)
        # A different byte identity at the same path must not inherit the video fact.
        path.write_bytes(b'different synthetic bytes');f.f.refresh()
        with f.f.connect() as conn:
            imports.inventory(conn,f.f.catalog,f.f.exports);imports.hash_pending(conn)
            row=conn.execute('SELECT kind,content_hash FROM occurrences WHERE source=?',(str(path),)).fetchone()
            self.assertEqual(row['kind'],'photo');self.assertNotEqual(row['content_hash'],digest)
            self.assertEqual(len(media_types.facts(conn)),1)

    def test_noncontainer_failures_do_not_get_invented_types(self):
        f=test_library.LibraryTests();f.setUp();v=f.build();vision=f.f.root/'vision.db'
        with sqlite3.connect(vision) as c:
            c.execute('CREATE TABLE visual_errors(content_hash TEXT)')
            c.executemany('INSERT INTO visual_errors VALUES(?)',[(i['content_hash'],) for i in v.items])
        with f.f.connect() as conn:
            self.assertEqual(media_types.recover(conn,vision,f.f.root/'probe-cache')['reclassified'],0)
            self.assertEqual(media_types.facts(conn),{})
        self.assertFalse((f.f.root/'probe-cache').exists())
