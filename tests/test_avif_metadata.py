"""AVIF recovery adds source evidence without erasing the failed extraction."""
import json
import sqlite3
import unittest
from unittest.mock import patch
from tests import test_metadata
from src import catalog,metadata,avif_metadata

class AVIFMetadataTests(unittest.TestCase):
    def test_misnamed_avif_metadata_survives_refresh_with_original_error(self):
        from PIL import Image
        f=test_metadata.MetadataTests();f.setUp();path=f.source/'a.jpg'
        exif=Image.Exif();exif[272]='Synthetic camera';exif[306]='2024:03:04 05:06:07'
        Image.new('RGB',(32,24)).save(path,format='AVIF',exif=exif)
        source,c=catalog.connect(f.source,f.catalog);catalog.inventory(source,c)
        original={'errors':[{'stage':'photo-metadata','type':'ToolError'}],'extractor':'catalog-1'}
        with c:c.execute('UPDATE files SET metadata=?',(json.dumps(original),))
        c.close();f.index()
        for _ in range(2):
            result=metadata.refresh(f.imports,f.exports,f.output)
            self.assertEqual(result['embedded_occurrences_recovered'],1)
        with sqlite3.connect(f.output) as c:
            facts=c.execute("SELECT value_json,extractor,source_span,extractor_version FROM metadata_facts WHERE attribute='raw_embedded'").fetchall()
        self.assertEqual(len(facts),2)
        old=next(json.loads(r[0]) for r in facts if r[1]=='catalog-1');self.assertEqual(old,original)
        new=next(r for r in facts if r[1]=='pillow-avif');raw=json.loads(new[0])
        self.assertEqual((raw['width'],raw['height'],raw['format']),(32,24,'AVIF'))
        self.assertEqual(raw['date']['value'],'2024-03-04T05:06:07')
        self.assertFalse(raw['date']['timezone_known'])
        self.assertEqual(json.loads(new[2])['basis'],'detected_avif')
        self.assertTrue(new[3].startswith('avif-1:pillow-'))
        with patch('src.avif_metadata.inspect',side_effect=ImportError):
            result=metadata.refresh(f.imports,f.exports,f.root/'without-pillow.db')
            self.assertEqual(result['embedded_recovery_unavailable'],1)
        path.write_bytes(b'changed source')
        with self.assertRaises(ValueError):metadata.refresh(f.imports,f.exports,f.output)
        with sqlite3.connect(f.output) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM metadata_facts WHERE attribute='raw_embedded'").fetchone()[0],2)

    def test_non_avif_is_not_relabelled(self):
        from PIL import Image
        f=test_metadata.MetadataTests();f.setUp();path=f.source/'a.jpg'
        Image.new('RGB',(16,16)).save(path)
        with self.assertRaises(ValueError):avif_metadata.inspect(path)
