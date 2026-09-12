"""Equivalent timestamps are not conflicts; owner choices survive rebuilding."""
import unittest
from src.dates import evidence
from src.library import Library
from tests import test_library


def fact(value,meaning='capture'):
    return {'attribute':'date','value':{'value':value,'meaning':meaning},'source_path':'synthetic','source_span':'embedded','extractor':'fixture'}


class DateTests(unittest.TestCase):
    def test_explicit_offsets_compare_as_instants(self):
        result=evidence([fact('2026-01-01T00:00:00Z'),fact('2025-12-31T18:00:00-06:00')])
        self.assertEqual(result['status'],'equivalent')
        self.assertEqual(len(result['choices']),2)
        self.assertEqual(evidence([fact('2026-01-01T00:00:00Z'),fact('2026-01-01T01:00:00Z')])['status'],'conflict')

    def test_unknown_timezone_is_never_assumed_and_creation_is_separate(self):
        self.assertEqual(evidence([fact('2026-01-01T00:00:00'),fact('2026-01-01T00:00:00Z')])['status'],'timezone_unknown')
        self.assertEqual(evidence([fact('2026-01-01T00:00:00Z'),fact('2025-01-01T00:00:00Z','Google Photos creation')])['status'],'single')
        self.assertEqual(evidence([fact('2026-02-30T00:00:00Z')])['status'],'invalid')

    def test_owner_choice_changes_filters_and_survives_rebuild_then_reset(self):
        f=test_library.LibraryTests();f.setUp();viewer=f.build();photo=viewer.search(query='new.jpg')['items'][0];digest=photo['content_hash']
        original=viewer.facts[digest][:]
        extra=fact('2024-06-15T12:00:00Z');viewer.facts[digest].append(extra)
        viewer.by_id[photo['id']]['date_status']='conflict'
        self.assertEqual(viewer.search(kind='date_review')['total'],1)
        choice=next(c for c in viewer.date_review(photo['id'])['choices'] if c['date']['value'].startswith('2024'))
        viewer.organize('date',{'content_hash':digest,'choice':choice['id']})
        self.assertEqual(viewer.search(year='2024')['total'],1)
        self.assertEqual(viewer.search(kind='date_review')['total'],0)
        self.assertEqual(viewer.slideshow(year='2024')['total'],1)
        rebuilt=Library(f.f.catalog,f.f.db,organization=viewer.organization.path)
        self.assertEqual(rebuilt.search(year='2024')['total'],1)
        self.assertEqual(rebuilt.metadata(photo['id'])['dates']['selected']['date']['value'],'2024-06-15T12:00:00Z')
        rebuilt.organize('reset_date',{'content_hash':digest})
        self.assertEqual(rebuilt.search(year='2026')['total'],1)
        self.assertEqual(len(viewer.organization.path.read_text().splitlines()),2)
        self.assertEqual(rebuilt.facts[digest],original)
        with self.assertRaises(ValueError):rebuilt.organize('date',{'content_hash':digest,'choice':'nonexistent'})
        self.assertEqual(len(viewer.organization.path.read_text().splitlines()),2)


class InferredDateTests(unittest.TestCase):
    def test_file_names_yield_ranked_candidates_and_nothing_else(self):
        from src.dates import infer
        found=infer(['IMG_20230412_101500.jpg','2021-07-04 party.png','20191225_dinner.heic','1580000000000.jpg','random.png','99991231.jpg','IMG_20231399_000000.jpg'],latest_year=2026)
        self.assertEqual([c['value'][:10] for c in found],['2023-04-12','2021-07-04','2019-12-25','2020-01-26'])
        self.assertEqual([c['confidence'] for c in found],[0.8,0.7,0.6,0.6])
        self.assertTrue(all(c['meaning']=='capture' and c['field'].startswith('file name') for c in found))
        self.assertEqual(len(infer(['IMG_20230412_101500.jpg','IMG_20230412_101500 (1).jpg'])),1)

    def test_inferred_dates_show_as_inferred_and_owner_dates_override_then_reset(self):
        import zipfile
        f=test_library.LibraryTests();f.setUp()
        with zipfile.ZipFile(f.f.zip,'a') as z:z.writestr('Google Photos/IMG_20230412_101500.jpg',b'named by a phone camera')
        viewer=f.build();photo=viewer.search(query='IMG_2023')['items'][0];digest=photo['content_hash']
        self.assertTrue(photo['date_inferred']);self.assertEqual(photo['day'],'2023-04-12')
        self.assertEqual(photo['date']['source'],'Inferred from file name')
        self.assertEqual(viewer.search(year='2023')['total'],1);self.assertNotIn(digest,{i['content_hash'] for i in viewer.search(kind='undated')['items']})
        review=viewer.date_review(photo['id'])
        self.assertTrue(review['inferred']);self.assertEqual(review['status'],'missing')
        inferred=[c for c in review['choices'] if c.get('inferred')]
        self.assertEqual([c['date']['value'] for c in inferred],['2023-04-12T10:15:00'])
        viewer.organize('date',{'content_hash':digest,'choice':inferred[0]['id']})
        confirmed=viewer.search(query='IMG_2023')['items'][0]
        self.assertTrue(confirmed['date_confirmed']);self.assertFalse(confirmed['date_inferred'])
        viewer.organize('set_date',{'contents':[digest],'date':'2020-02-29'})
        moved=viewer.search(query='IMG_2023')['items'][0]
        self.assertEqual((moved['day'],moved['date']['source'],moved['date_inferred']),('2020-02-29','Owner-set date',False))
        self.assertEqual(viewer.search(year='2020')['total'],1)
        rebuilt=Library(f.f.catalog,f.f.db,organization=viewer.organization.path)
        self.assertEqual(rebuilt.search(year='2020')['total'],1)
        self.assertEqual(rebuilt.date_review(photo['id'])['selected']['source'],'owner')
        rebuilt.organize('reset_date',{'content_hash':digest})
        back=rebuilt.search(query='IMG_2023')['items'][0]
        self.assertEqual((back['day'],back['date_inferred']),('2023-04-12',True))
        with self.assertRaises(ValueError):rebuilt.organize('set_date',{'contents':['0'*64],'date':'2020-02-29'})
        with self.assertRaises(ValueError):rebuilt.organize('set_date',{'contents':[digest],'date':'2020-02-30'})
        with self.assertRaises(ValueError):rebuilt.organize('set_date',{'contents':[digest],'date':'yesterday'})
