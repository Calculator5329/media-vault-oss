"""Viewer contract tests with synthetic files only; no bound listener needed."""
import os
os.environ.setdefault('MEDIA_VAULT_EXTERNAL_ROOTS', '/run/media')  # the tests use /run/media as the example removable root
import io
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src import catalog, server


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='mv-viewer-test-'))
        source = self.root/'source'
        source.mkdir()
        for name,content in [('a.jpg',b'ABC'),('b.jpg',b'ABC'),('c.mp4',b'Video')]:
            (source/name).write_bytes(content)
        self.source,conn = catalog.connect(source,self.root/'catalog.db')
        catalog.inventory(source,conn)
        data={'date':{'value':'2024-06-21T12:00:00-05:00','source':'exif:DateTimeOriginal','meaning':'capture','timezone_known':True},'location':{'lat':40.0,'lon':-90.0,'source':'exif:GPS'},'exif':{'Model':'Demo camera'},'errors':[]}
        conn.execute('UPDATE files SET metadata=? WHERE path=?',(json.dumps(data),'a.jpg'))
        conn.commit()
        catalog.hash_candidates(source,conn)
        conn.close()
        self.viewer=server.Viewer(self.root/'catalog.db')

    def test_search_and_filters_are_measured(self):
        self.assertEqual(self.viewer.search(query='Demo')['total'],1)
        self.assertEqual(self.viewer.search(kind='duplicates')['total'],2)
        self.assertEqual(self.viewer.search(kind='undated')['total'],2)
        self.assertEqual(self.viewer.search(kind='located')['total'],1)
        self.assertEqual(self.viewer.search(kind='video')['total'],1)
        self.assertEqual(self.viewer.search(year='2024')['total'],1)
        self.assertEqual(self.viewer.summary()['missing_location'],2)
        self.assertFalse(self.viewer.summary()['capabilities']['faces'])

    def test_pagination_has_no_overlaps(self):
        first=self.viewer.search(limit=2)
        second=self.viewer.search(limit=2,offset=first['next_offset'])
        self.assertEqual(len(first['items'])+len(second['items']),3)
        self.assertIsNone(second['next_offset'])
        self.assertFalse({i['id'] for i in first['items']}&{i['id'] for i in second['items']})

    def test_date_ranges_and_radius_exclude_unknowns(self):
        self.assertEqual(self.viewer.search(after='2024-06-21',before='2024-06-21')['total'],1)
        self.assertEqual(self.viewer.search(after='2025-01-01')['total'],0)
        self.assertEqual(self.viewer.search(near='40,-90,1')['total'],1)
        self.assertEqual(self.viewer.search(near='0,0,1')['total'],0)
        for filters in [{'after':'2024-02-30'},{'after':'2025-01-01','before':'2024-01-01'}, {'near':'nan,0,1'},{'near':'91,0,1'}]:
            with self.assertRaises(ValueError):self.viewer.search(**filters)

    def test_slideshow_uses_complete_query_and_reports_limits(self):
        result=self.viewer.slideshow()
        self.assertEqual(result['total'],2)
        self.assertTrue(all(i['kind']=='photo' for i in result['items']))
        self.assertEqual(result['items'][0]['name'],'a.jpg')
        self.assertTrue(self.viewer.slideshow(limit=1)['truncated'])
        self.assertEqual(self.viewer.slideshow(near='40,-90,1')['total'],1)
        status,body,_=self.request('/api/slideshow?after=2024-01-01&before=2024-12-31')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['total'],1)
        self.assertEqual(self.request('/api/slideshow?limit=10000')[0],400)

    def test_no_arbitrary_paths_or_changed_sources(self):
        with self.assertRaises(FileNotFoundError):self.viewer.source_path('../../etc/passwd')
        item=self.viewer.search(query='a.jpg')['items'][0]
        self.assertEqual(self.viewer.source_path(item['id']),self.source/'a.jpg')
        (self.source/'a.jpg').write_bytes(b'changed')
        with self.assertRaises(FileNotFoundError):self.viewer.source_path(item['id'])

    def test_source_symlink_is_refused(self):
        item=self.viewer.search(query='a.jpg')['items'][0]
        (self.source/'a.jpg').rename(self.root/'archive.jpg')
        try: (self.source/'a.jpg').symlink_to(self.root/'archive.jpg')
        except OSError: self.skipTest('symlinks unavailable')
        with self.assertRaises(FileNotFoundError):self.viewer.source_path(item['id'])

    def test_preview_cache_cannot_be_in_source(self):
        with self.assertRaises(ValueError):server.Viewer(self.root/'catalog.db',self.source/'previews')
        with self.assertRaises(ValueError):server.Viewer(self.root/'catalog.db','/run/media/new-previews')

    def test_thumbnail_writer_only_targets_cache_and_keeps_original(self):
        item=self.viewer.search(query='a.jpg')['items'][0]
        original=(self.source/'a.jpg').read_bytes()
        def run(command,**kwargs):
            target=Path(command[-1]);self.assertEqual(target.parent,self.viewer.thumbnails)
            target.write_bytes(b'synthetic-preview')
            return type('Result',(),{'returncode':0})()
        with patch.object(server.subprocess,'run',side_effect=run) as process:
            result=self.viewer.thumbnail(item['id'])
            self.assertEqual(result.read_bytes(),b'synthetic-preview')
            self.assertEqual(self.viewer.thumbnail(item['id']),result)
            self.assertEqual(process.call_count,1)
        self.assertEqual((self.source/'a.jpg').read_bytes(),original)

    def request(self,path,host='127.0.0.1:8770',origin=None):
        klass=server.handler(self.viewer,8770)
        instance=object.__new__(klass)
        instance.path=path;instance.headers={'Host':host}
        if origin:instance.headers['Origin']=origin
        response=[]
        instance.send=lambda status,body,mime='application/json':response.append((status,body,mime))
        instance.do_GET()
        return response[0]

    def test_http_entrypoint_rejects_foreign_hosts_origins_and_paths(self):
        self.assertEqual(self.request('/api/summary',host='evil.example:8770')[0],403)
        self.assertEqual(self.request('/api/summary',origin='https://evil.example')[0],403)
        self.assertEqual(self.request('/preview/../../etc/passwd')[0],404)
        self.assertEqual(self.request('/api/items?limit=100000')[0],400)
        self.assertEqual(self.request('/api/items?offset=-1')[0],400)
        status,body,_=self.request('/api/items?kind=duplicates')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['total'],2)

    def test_html_and_summary_are_available_from_same_origin(self):
        status,body,mime=self.request('/')
        self.assertEqual(status,200);self.assertIn('text/html',mime)
        self.assertIn(b'Timeline',body)
        status,body,_=self.request('/api/summary')
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['files'],3)

    def test_worker_progress_is_live_and_missing_stores_are_not_zero(self):
        import sqlite3
        self.assertEqual(self.viewer.progress()['vision'],{'available':False})
        path=self.root/'vision.db'
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute('CREATE TABLE visual_facts(content_hash TEXT)')
            conn.execute('CREATE TABLE visual_errors(content_hash TEXT)')
            conn.execute("INSERT INTO visual_facts VALUES('synthetic')")
        self.assertEqual(self.viewer.progress()['vision']['contents'],1)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("INSERT INTO visual_facts VALUES('second')")
        status,body,_=self.request('/api/progress')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['vision']['contents'],2)


if __name__=='__main__':unittest.main()
