"""Assistant queries resolve names explicitly and preserve output privacy options."""
import unittest
from src.query import execute,parser,remote


class QueryTests(unittest.TestCase):
    def test_saved_trip_and_person_resolve_to_exact_filters(self):
        def request(endpoint,params):
            if endpoint=='people':return {'people':[{'id':'a'*32,'name':'Example Person'}]}
            if endpoint=='trips':return {'trips':[{'id':'b'*32,'name':'Example Trip','after':'2026-01-01','before':'2026-01-03','place':'1:2'}]}
            self.assertEqual(endpoint,'items');self.assertEqual(params['person'],'a'*32);self.assertEqual(params['place'],'1:2');self.assertEqual(params['after'],'2026-01-01')
            return {'items':[{'id':'c'*64,'content_hash':'d'*64,'name':'private filename'}],'total':1}
        args=parser().parse_args(['--person','Example Person','--trip','Example Trip','--ids']);result=execute(args,request)
        self.assertNotIn('name',result['items'][0]);self.assertEqual(result['total'],1)

    def test_modes_and_count_do_not_return_private_text(self):
        def request(endpoint,params):
            self.assertEqual(endpoint,'moments');return {'items':[{'name':'private','moment':{'text':'private words'}}],'total':4,'processed_videos':9}
        result=execute(parser().parse_args(['--mode','moments','--count']),request)
        self.assertEqual(result,{'total':4,'processed_videos':9})

    def test_remote_requests_reject_nonlocal_targets_before_network(self):
        for base in ('https://example.com','http://127.0.0.1:8770@evil.example','http://127.0.0.1:8770/extra','http://localhost'):
            with self.assertRaises(ValueError):remote(base,'summary',{})
