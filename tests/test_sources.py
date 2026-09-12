"""Source settings retain revisions and reject cross-library or stale writes."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from src.sources import Sources
from src import server


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(tempfile.mkdtemp())
        self.app=self.root/'app';self.app.mkdir()
        self.state=self.app/'state';self.state.mkdir()
        self.photos=self.root/'photos';self.photos.mkdir()
        self.extra=self.root/'more-photos';self.extra.mkdir()
        self.config=self.app/'vault.config.json'
        self.config.write_text(json.dumps({'sources':[str(self.photos)],'tier':'personal'}))
        self.sources=Sources(self.config,self.state,self.photos)

    def test_add_retains_exact_prior_revision_and_rejects_stale_form(self):
        before=self.config.read_bytes();revision=self.sources.status()['revision']
        result=self.sources.add(str(self.extra),revision)
        self.assertEqual(len(result['sources']),2)
        self.assertFalse(result['sources'][1]['registered'])
        self.assertEqual(json.loads(self.config.read_text())['tier'],'personal')
        history=list((self.state/'source-config-history').glob('*.json'))
        self.assertEqual(len(history),1);self.assertEqual(history[0].read_bytes(),before)
        third=self.root/'third';third.mkdir()
        with self.assertRaisesRegex(ValueError,'Sources changed'):self.sources.add(str(third),revision)
        self.assertEqual(len(self.sources.status()['sources']),2)

    def test_overlap_state_relative_and_other_library_are_refused(self):
        revision=self.sources.status()['revision'];before=self.config.read_bytes()
        child=self.photos/'nested';child.mkdir()
        for path in (str(child),str(self.root),str(self.state),str(self.photos),'relative'):
            with self.assertRaises(ValueError):self.sources.add(path,revision)
        with self.assertRaises(ValueError):Sources(self.config,self.state,self.extra)
        self.assertEqual(self.config.read_bytes(),before)
        self.assertFalse((self.state/'source-config-history').exists())

    def test_history_symlink_cannot_redirect_configuration_writes(self):
        outside=self.root/'outside-history';outside.mkdir()
        try: (self.state/'source-config-history').symlink_to(outside,target_is_directory=True)
        except OSError: self.skipTest('symlinks unavailable')
        before=self.config.read_bytes()
        with self.assertRaisesRegex(ValueError,'history must stay'):
            self.sources.add(str(self.extra),self.sources.status()['revision'])
        self.assertEqual(self.config.read_bytes(),before)
        self.assertEqual(list(outside.iterdir()),[])

    def request(self,method,body=None,origin='http://127.0.0.1:8770'):
        klass=server.handler(object(),8770,self.sources);instance=object.__new__(klass)
        payload=json.dumps(body).encode() if body is not None else b''
        instance.path='/api/sources';instance.headers={'Host':'127.0.0.1:8770','Origin':origin,'Content-Type':'application/json','Content-Length':str(len(payload))}
        instance.rfile=io.BytesIO(payload);sent=[];instance.send=lambda *args:sent.append(args)
        getattr(instance,'do_'+method)();return sent[0]

    def test_http_same_origin_add_and_foreign_write_refusal(self):
        status,body=self.request('GET');self.assertEqual(status,200)
        value={'path':str(self.extra),'revision':json.loads(body)['revision']}
        self.assertEqual(self.request('POST',value,origin='https://foreign.example')[0],403)
        self.assertEqual(len(self.sources.status()['sources']),1)
        self.assertEqual(self.request('POST',value)[0],200)
        self.assertEqual(len(self.sources.status()['sources']),2)
