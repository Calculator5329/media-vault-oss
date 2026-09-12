"""Verified sources become one timeline entry without modifying originals."""
import json
import unittest
import zipfile
from unittest.mock import patch

from tests import test_imports
from src import imports,metadata
from src.library import Library


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.f=test_imports.ImportTests();self.f.setUp()
        with zipfile.ZipFile(self.f.zip,'a') as z:
            z.writestr('Google Photos/new.jpg',b'new additional image')
            z.writestr('Google Photos/new.jpg.json',json.dumps({'title':'new.jpg',
                'photoTakenTime':{'timestamp':'1767225600'},'geoData':{'latitude':40,'longitude':-90}}))

    def build(self,limit=None):
        with self.f.connect() as conn:
            imports.inventory(conn,self.f.catalog,self.f.exports)
            imports.hash_pending(conn,limit=limit)
        metadata.refresh(self.f.db,self.f.exports,self.f.root/'metadata.db')
        return Library(self.f.catalog,self.f.db,organization=self.f.root/'corrections/organization.jsonl')

    def test_verified_copies_collapse_but_sources_and_metadata_survive(self):
        library=self.build()
        self.assertEqual(len(library.items),3)
        self.assertEqual(library.summary()['source_occurrences'],4)
        original=library.search(query='a.jpg')['items'][0]
        self.assertEqual(original['source_count'],2)
        self.assertEqual(len(library.metadata(original['id'])['sources']),2)
        new=library.search(query='new.jpg')['items'][0]
        self.assertEqual(new['day'],'2026-01-01')
        self.assertEqual(new['location']['lat'],40)
        self.assertEqual(library.slideshow(year='2026')['total'],1)
        self.assertTrue(library.metadata(new['id'])['facts'])
        self.assertEqual((self.f.source/'a.jpg').read_bytes(),b'same image bytes')

    def test_unverified_master_is_not_silently_deduplicated(self):
        library=self.build(limit=1)
        self.assertEqual(library.summary()['unverified_items'],1)
        self.assertEqual(len(library.items),2)

    def test_zip_preview_writes_only_cache_and_reuses_it(self):
        library=self.build()
        item=library.search(query='new.jpg')['items'][0]
        class SyntheticImage:
            def thumbnail(self,size): pass
            def save(self,path,**kwargs): path.write_bytes(b'synthetic jpeg')
            def close(self): pass
        with patch('src.vision.read_image',return_value=SyntheticImage()) as reader:
            path=library.thumbnail(item['id'])
            self.assertEqual(path.parent,library.thumbnails)
            self.assertEqual(library.thumbnail(item['id']),path)
            self.assertEqual(reader.call_count,1)

    def test_zip_video_preview_renders_a_frame_from_an_extracted_copy(self):
        import subprocess
        clip=self.f.root/'clip.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=green:s=160x120:r=10','-t','1','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(clip)],check=True,capture_output=True)
        with zipfile.ZipFile(self.f.zip,'a') as z:
            z.write(clip,'Google Photos/clip.mp4')
        library=self.build()
        item=library.search(query='clip.mp4')['items'][0]
        self.assertEqual((item['kind'],item['source_type']),('video','zip'))
        path=library.thumbnail(item['id'])
        self.assertEqual(path,library.thumbnails/(item['id']+'.jpg'))
        self.assertGreater(path.stat().st_size,0)
        self.assertEqual([p.name for p in library.thumbnails.iterdir() if p.suffix!='.jpg'],[],'the extracted copy must not outlive the render')
        self.assertEqual(library.thumbnail(item['id']),path)

    def test_visual_filters_apply_before_ranking_and_return_current_items(self):
        library=self.build()
        library.encoder=object()
        library.visual_database.touch()
        item=library.search(year='2026')['items'][0]
        def search(query,limit,allowed):
            self.assertEqual(allowed,{item['content_hash']})
            return {'results':[{'content_hash':item['content_hash'],'similarity':0.5}],
                    'indexed_contents':3,'eligible_contents':1,'meaning':'Synthetic ranking'}
        with patch('src.vision.SearchIndex.search',side_effect=search):
            result=library.visual('red car',year='2026')
        self.assertEqual(result['items'][0]['id'],item['id'])
        self.assertEqual(result['items'][0]['similarity'],0.5)
        self.assertIsNone(result['next_offset'])
        library.visual_lock.acquire()
        try:
            with self.assertRaises(RuntimeError):library.visual('red car')
        finally:library.visual_lock.release()


if __name__=='__main__':
    unittest.main()


class TripPathTests(LibraryTests):
    def test_trip_paths_join_located_photos_in_time_order_and_skip_the_rest(self):
        library=self.build();located=[i for i in library.items if i.get('location')];self.assertEqual(len(located),1);day=located[0]['day']
        library.organization.append('trip',{'trip':'a'*32,'name':'Test trip','after':day,'before':day,'place':''})
        library.organization.append('trip',{'trip':'b'*32,'name':'Empty trip','after':'1990-01-01','before':'1990-01-02','place':''})
        paths=library.trip_paths();self.assertIn('meaning',paths);data=library.trips();covering=[t for t in data['trips'] if t['id']=='a'*32]
        self.assertEqual(len(covering),1);self.assertEqual(paths['paths']['b'*32]['points'],[])
        for trip in covering:
            path=paths['paths'][trip['id']];self.assertEqual(path['points'],[[40.0,-90.0]]);self.assertEqual(path['located'],1);self.assertEqual(path['days'],1)
        self.assertIs(library.trip_paths(),paths,'memoised until the organization log changes')
        for trip_id,path in paths['paths'].items():
            if trip_id not in {t['id'] for t in covering}:self.assertEqual(path['points'],[])
        self.assertIsNone(paths['paths']['a'*32]['from']);self.assertIsNone(paths['paths']['a'*32]['back'])

    def _add_located(self,library,base,day,lat,lon,suffix):
        item={**base,'id':base['id']+suffix,'content_hash':(base['content_hash'] or 'c'*64)[:-len(suffix)]+suffix,'day':day,'date':{**(base['date'] or {}),'value':day+'T12:00:00'},'location':{'lat':lat,'lon':lon},'name':base['name']+suffix}
        library.items.append(item);library.by_content[item['content_hash']]=item;return item

    def test_trip_paths_add_dashed_legs_to_the_nearest_located_photos_before_and_after(self):
        library=self.build();base=[i for i in library.items if i.get('location')][0]
        self._add_located(library,base,'2024-06-01',30.0,-100.0,'home1');self._add_located(library,base,'2024-06-10',37.0,-122.0,'trip1');self._add_located(library,base,'2024-06-11',37.1,-122.1,'trip2');self._add_located(library,base,'2024-06-20',30.0,-100.0,'home2')
        library.organization.append('trip',{'trip':'d'*32,'name':'West','after':'2024-06-10','before':'2024-06-11','place':''})
        path=library.trip_paths()['paths']['d'*32]
        self.assertEqual(path['points'],[[37.0,-122.0],[37.1,-122.1]]);self.assertEqual(path['from'],[30.0,-100.0]);self.assertEqual(path['back'],[30.0,-100.0])

    def test_trips_span_several_recorded_areas_and_swallow_covered_suggestions(self):
        library=self.build();base=[i for i in library.items if i.get('location')][0]
        for n,day in enumerate(('2024-07-01','2024-07-02','2024-07-03')):self._add_located(library,base,day,37.0,-122.0,'sj%d'%n)
        for n,day in enumerate(('2024-07-04','2024-07-05','2024-07-06')):self._add_located(library,base,day,37.5,-121.5,'ef%d'%n)
        before=library.trips();names={p['place'] for p in before['proposals']};self.assertTrue({'370:-1220','375:-1215'}<=names,names)
        event=library.organize('trip',{'trip':'','name':'Bay week','after':'2024-07-01','before':'2024-07-06','place':'375:-1215,370:-1220'})
        self.assertEqual(event['data']['place'],'370:-1220,375:-1215','areas are ordered and deduplicated')
        with self.assertRaises(ValueError):library.organize('trip',{'trip':'','name':'Bad','after':'2024-07-01','before':'2024-07-02','place':'370:-1220,1:1'})
        after=library.trips();self.assertFalse([p for p in after['proposals'] if p['place'] in ('370:-1220','375:-1215')],'suggestions inside the saved trip leave the pile')
        found=library.search(trip=event['data']['trip'],limit=50)['items'];self.assertEqual(len(found),6)

    def test_highlights_accept_the_everything_search_mode(self):
        library=self.build();result=library.highlights(mode='everything',limit=5)
        self.assertEqual(result['total'],len([i for i in library._select() if i['kind']=='photo' and i['content_hash']]))

class TimelineTests(unittest.TestCase):
    def test_timeline_offsets_index_into_search_order(self):
        f=LibraryTests();f.setUp()
        with zipfile.ZipFile(f.f.zip,'a') as z:
            z.writestr('Google Photos/IMG_20230412_101500.jpg',b'april phone photo')
            z.writestr('Google Photos/IMG_20230501_090000.jpg',b'may phone photo')
        library=f.build();timeline=library.timeline();everything=library.search(limit=200)['items']
        self.assertEqual(timeline['total'],len(everything))
        self.assertEqual([y['year'] for y in timeline['years']],sorted({i['day'][:4] for i in everything if i['day']},reverse=True))
        for year in timeline['years']:
            self.assertEqual(everything[year['offset']]['day'][:4],year['year'])
            self.assertEqual(sum(m['count'] for m in year['months']),year['count'])
            for month in year['months']:self.assertEqual(everything[month['offset']]['day'][:7],month['month'])
        april=next(m for y in timeline['years'] for m in y['months'] if m['month']=='2023-04')
        self.assertEqual(library.search(offset=april['offset'],limit=1)['items'][0]['name'],'IMG_20230412_101500.jpg')
        self.assertEqual(timeline['undated']['count'],sum(not i['day'] for i in everything))
        self.assertTrue(all(not i['day'] for i in everything[timeline['undated']['offset']:]))
        self.assertEqual(library.timeline(query='IMG_2023')['total'],2)


class EverythingSearchTests(unittest.TestCase):
    def test_one_query_fans_out_by_kind_and_dates_become_jumps(self):
        f=LibraryTests();f.setUp()
        with zipfile.ZipFile(f.f.zip,'a') as z:z.writestr('Google Photos/IMG_20230412_101500.jpg',b'april phone photo')
        library=f.build();digest=library.search(query='new.jpg')['items'][0]['content_hash']
        library.organize('person',{'person':'','name':'Grandma June'});person=library.people()['people'][0]
        library.organize('add',{'person':person['id'],'contents':[digest]})
        library.organize('album',{'album':'','name':'June visit','contents':[digest]})
        result=library.everything('june')
        self.assertEqual([p['name'] for p in result['people']['items']],['Grandma June'])
        self.assertEqual([a['name'] for a in result['albums']['items']],['June visit'])
        self.assertEqual(result['files']['total'],1,'a tagged name is searchable as a file detail')
        self.assertEqual(result['dates']['total'],0)
        self.assertEqual(result['text']['total'],0)
        april=library.everything('April 2023')['dates']['items']
        self.assertEqual([(h['kind'],h['value'],h['count']) for h in april],[('month','2023-04',1)])
        self.assertEqual(library.search(offset=april[0]['offset'],limit=1)['items'][0]['day'],'2023-04-12')
        self.assertEqual(library.everything('2023-04-12')['dates']['items'][0]['kind'],'day')
        self.assertEqual(library.everything('2023')['dates']['items'][0]['kind'],'year')
        self.assertEqual(library.everything('1999')['dates']['total'],0)
        with self.assertRaises(ValueError):library.everything('   ')


class SuggestTests(unittest.TestCase):
    def test_suggestions_group_by_kind_and_explain_what_search_covers(self):
        library=LibraryTests();library.setUp();library=library.build()
        digest=library.search(query='new.jpg')['items'][0]['content_hash']
        person=library.organize('person',{'person':'','name':'Grandma'})['data']['person']
        library.organize('add',{'person':person,'contents':[digest]})
        empty=library.suggest('')
        self.assertEqual(set(empty),{'query','people','places','trips','albums','buckets','dates','filters','tags','tags_meaning','text','meaning'})
        self.assertIn('photo',[f['id'] for f in empty['filters']])
        hit=library.suggest('gra')
        self.assertEqual([p['name'] for p in hit['people']],['Grandma'])
        self.assertEqual(hit['people'][0]['count'],1)
        self.assertEqual(library.suggest('zzz-no-such-thing')['people'],[])
        year=library.suggest('2026')
        self.assertEqual([(d['kind'],d['value']) for d in year['dates']],[('year','2026')])


class OrganizationCacheTests(unittest.TestCase):
    def test_replay_runs_once_per_log_version(self):
        library=LibraryTests();library.setUp();library=library.build()
        org=library.organization;org._cache=None
        with patch.object(type(org),'replay',wraps=org.replay) as replay:
            before=org.read();org.read()
            self.assertEqual(replay.call_count,1)
            person=library.organize('person',{'person':'','name':'Someone'})['data']['person']
            self.assertIn(person,org.read()['people'])
            self.assertNotIn(person,before['people'])
            org.read();org.read()
            self.assertEqual(replay.call_count,3)  # one to validate the append under its lock, one for the first read after it


class FavoriteAndHideTests(unittest.TestCase):
    def test_favorite_and_hide_are_reversible_filters_that_never_touch_originals(self):
        f=LibraryTests();f.setUp();library=f.build()
        items=library.search()['items'];digest=items[0]['content_hash'];total=len(items)
        self.assertEqual(library.search(kind='favorites')['items'],[])
        library.organize('favorite',{'contents':[digest]})
        favorites=library.search(kind='favorites')['items']
        self.assertEqual([i['content_hash'] for i in favorites],[digest]);self.assertTrue(favorites[0]['favorite'])
        library.organize('hide',{'contents':[digest]})
        self.assertEqual(len(library.search()['items']),total-1)
        self.assertNotIn(digest,{i['content_hash'] for i in library.search(kind='favorites')['items']})
        self.assertEqual(library.timeline()['total'],total-1)
        hidden=library.search(kind='hidden')['items']
        self.assertEqual([i['content_hash'] for i in hidden],[digest]);self.assertTrue(hidden[0]['hidden'])
        library.organize('unhide',{'contents':[digest]});library.organize('unfavorite',{'contents':[digest]})
        self.assertEqual(len(library.search()['items']),total)
        self.assertEqual(library.search(kind='favorites')['items'],[]);self.assertEqual(library.search(kind='hidden')['items'],[])
        with self.assertRaises(ValueError):library.organize('hide',{'contents':['0'*64]})
        self.assertIn('favorites',[x['id'] for x in library.suggest('')['filters']])
        self.assertTrue(library.summary()['capabilities']['favorites'])
        self.assertEqual((f.f.source/'a.jpg').read_bytes(),b'same image bytes')


class BucketTests(unittest.TestCase):
    def test_buckets_are_owner_managed_collections_of_photos_and_videos(self):
        f=LibraryTests();f.setUp();library=f.build()
        items=library.search()['items'];first,second=items[0]['content_hash'],items[1]['content_hash']
        self.assertEqual(library.buckets()['buckets'],[])
        made=library.organize('bucket',{'bucket':'','name':'  Cute videos of little Jacob '})
        bucket=made['data']['bucket']
        rows=library.buckets()['buckets']
        self.assertEqual([(b['name'],b['count'],b['cover']) for b in rows],[('Cute videos of little Jacob',0,None)])
        library.organize('bucket_add',{'bucket':bucket,'contents':[first]})
        library.organize('bucket_add',{'bucket':bucket,'contents':[second,first]})
        inside=library.search(bucket=bucket)['items']
        self.assertEqual({i['content_hash'] for i in inside},{first,second})
        self.assertTrue(all(i['buckets']==[bucket] for i in inside))
        self.assertEqual(library.buckets()['buckets'][0]['count'],2)
        self.assertEqual({i['content_hash'] for i in library.search(query='jacob')['items']},{first,second})
        self.assertEqual([s['id'] for s in library.suggest('cute')['buckets']],[bucket])
        self.assertEqual(library.everything('jacob')['buckets']['total'],1)
        library.organize('bucket',{'bucket':bucket,'name':'Jacob'})
        self.assertEqual(library.buckets()['buckets'][0]['count'],2,'renaming keeps the members')
        library.organize('bucket_remove',{'bucket':bucket,'contents':[first]})
        self.assertEqual([i['content_hash'] for i in library.search(bucket=bucket)['items']],[second])
        self.assertEqual(library.search()['items'][0]['buckets'],[])
        library.organize('archive_bucket',{'bucket':bucket})
        self.assertEqual(library.buckets()['buckets'],[]);self.assertEqual(library.buckets(archived=True)['buckets'][0]['id'],bucket)
        self.assertEqual(library.search()['items'][1]['buckets'],[],'archived buckets stop tagging items')
        library.organize('restore_bucket',{'bucket':bucket})
        self.assertEqual(library.buckets()['buckets'][0]['id'],bucket)
        with self.assertRaises(ValueError):library.organize('bucket_add',{'bucket':'0'*32,'contents':[first]})
        with self.assertRaises(ValueError):library.organize('bucket_add',{'bucket':bucket,'contents':['0'*64]})
        with self.assertRaises(ValueError):library.organize('bucket',{'bucket':'','name':' '})
        with self.assertRaises(ValueError):library.search(bucket='0'*32)
        self.assertTrue(library.summary()['capabilities']['buckets'])
        self.assertEqual((f.f.source/'a.jpg').read_bytes(),b'same image bytes')
