"""Missing dates and places get evidence-backed proposals; accepting writes ordinary log events that survive a rebuild."""
import json
import unittest
import zipfile
from src.library import Library
from tests import test_library


class GapTests(unittest.TestCase):
    def setUp(self):
        self.f=test_library.LibraryTests();self.f.setUp()
        with zipfile.ZipFile(self.f.f.zip,'a') as z:
            for name,stamp in (('IMG_0001.jpg','1767225600'),('IMG_0003.jpg','1767398400')):
                z.writestr('Google Photos/'+name,('bytes of '+name).encode())
                z.writestr('Google Photos/'+name+'.json',json.dumps({'title':name,'photoTakenTime':{'timestamp':stamp}}))
            z.writestr('Google Photos/IMG_0002.jpg',b'bytes of IMG_0002')
            z.writestr('Google Photos/Screenshot_1.png',b'screen bytes')
            z.writestr('Google Photos/hr-tree-02.png',b'asset bytes')
        self.library=self.f.build()

    def named(self,library,name):
        return library.search(query=name)['items'][0]

    def group(self,data,group):
        return next(g for g in data['groups'] if g['id']==group)

    def test_date_proposals_group_by_rule_and_accepting_writes_set_date(self):
        data=self.library.gaps('date')
        self.assertEqual([g['id'] for g in data['groups']],['sequence','modified','not-a-capture','asset'])
        self.assertEqual(data['coverage']['missing'],5)
        between=self.library.gaps('date',group='sequence')['group']
        self.assertEqual(between['action'],'accept');self.assertEqual(between['strength'],'strong')
        row=between['items'][0]
        self.assertEqual((row['name'],row['proposal']),('IMG_0002.jpg',{'date':'2026-01-01'}))
        self.assertIn('IMG_0001.jpg (2026-01-01)',row['evidence'])
        self.assertEqual(self.group(data,'not-a-capture')['action'],'dismiss')
        self.assertEqual(self.group(data,'asset')['count'],1)
        result=self.library.fill_gaps('date','sequence','accept')
        self.assertEqual((result['items'],result['events']),(1,1))
        filled=self.named(self.library,'IMG_0002')
        self.assertEqual((filled['day'],filled['date']['source'],filled['date_confirmed']),('2026-01-01','Owner-set date',True))
        events=[json.loads(l) for l in self.f.f.root.joinpath('corrections/organization.jsonl').read_text().splitlines()]
        self.assertEqual((events[-1]['op'],events[-1]['data']['date']),('set_date','2026-01-01'))
        rebuilt=Library(self.f.f.catalog,self.f.f.db,organization=self.library.organization.path)
        self.assertEqual(self.named(rebuilt,'IMG_0002')['day'],'2026-01-01')
        self.assertEqual(rebuilt.gaps('date')['coverage']['missing'],4)
        with self.assertRaises(ValueError):self.library.fill_gaps('date','sequence','accept')

    def test_not_expected_leaves_the_missing_count_and_can_be_restored(self):
        before=self.library.summary()['missing_date']
        self.library.fill_gaps('date','not-a-capture','dismiss')
        data=self.library.gaps('date')
        self.assertEqual((data['coverage']['not_expected'],data['coverage']['missing']),(1,before-1))
        self.assertEqual(self.library.summary()['missing_date'],before-1)
        self.assertEqual(self.library.summary()['not_expected_date'],1)
        parked=self.group(data,'not-expected')
        self.assertEqual((parked['action'],parked['count']),('restore',1))
        self.library.fill_gaps('date','not-expected','restore')
        data=self.library.gaps('date')
        self.assertEqual(data['coverage']['not_expected'],0)
        self.assertEqual(self.group(data,'not-a-capture')['count'],1)

    def test_location_proposals_follow_located_files_from_the_same_day(self):
        data=self.library.gaps('location')
        self.assertEqual(data['groups'][0]['id'],'same-day:area-400-900')
        self.assertEqual(data['coverage']['undated'],4)
        row=data['group']['items'][0]
        self.assertEqual((row['name'],row['proposal']['lat'],row['proposal']['lon']),('IMG_0001.jpg',40,-90))
        self.assertIn('1 located file that day, all in area 400:-900',row['evidence'])
        missing=self.library.summary()['missing_location']
        self.library.fill_gaps('location','same-day:area-400-900','accept')
        filled=self.named(self.library,'IMG_0001')
        self.assertEqual((filled['location']['lat'],filled['location']['source'],filled['location_confirmed']),(40,'Owner-set location',True))
        self.assertEqual(self.library.summary()['missing_location'],missing-1)
        self.assertEqual(next(p for p in self.library.places()['places'] if p['id']=='400:-900')['count'],2)
        rebuilt=Library(self.f.f.catalog,self.f.f.db,organization=self.library.organization.path)
        self.assertEqual(self.named(rebuilt,'IMG_0001')['location']['lon'],-90)
        rebuilt.organize('reset_location',{'content_hash':filled['content_hash']})
        self.assertIsNone(self.named(rebuilt,'IMG_0001')['location'])

    def test_skips_stay_out_and_bad_input_refuses(self):
        digest=self.named(self.library,'IMG_0002')['content_hash']
        with self.assertRaises(ValueError):self.library.fill_gaps('date','sequence','accept',skip=[digest])
        with self.assertRaises(ValueError):self.library.fill_gaps('date','no-such-group','accept')
        with self.assertRaises(ValueError):self.library.fill_gaps('year','sequence','accept')
        with self.assertRaises(ValueError):self.library.organize('set_location',{'contents':[digest],'lat':91,'lon':0})
        with self.assertRaises(ValueError):self.library.organize('set_location',{'contents':[digest],'lat':True,'lon':0})
        with self.assertRaises(ValueError):self.library.organize('dismiss_gap',{'gap':'camera','contents':[digest]})
        self.assertIsNone(self.named(self.library,'IMG_0002')['day'])
        page=self.library.gaps('date',group='missing',offset=0,limit=1)
        self.assertEqual((page['group']['id'],len(page['group']['items'])),('sequence',1))


if __name__=='__main__':
    unittest.main()
