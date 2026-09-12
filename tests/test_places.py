"""Nearest names are suggestions; owner labels and original GPS stay authoritative."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from src.places import Gazetteer,refresh
from src.library import Library
from tests import test_library


class PlaceTests(unittest.TestCase):
    def gazetteer(self):
        root=Path(tempfile.mkdtemp())
        def row(i,name,lat,lon):return '\t'.join([str(i),name,name,'',str(lat),str(lon),'P','PPL','XX','','AA','','','','1000','','','Etc/UTC','2026-09-05'])
        with zipfile.ZipFile(root/'cities500.zip','w') as z:z.writestr('cities500.txt',row(1,'Synthetic Town',40,-90)+'\n'+row(2,'Dateline Town',0,179.9)+'\n')
        (root/'admin1CodesASCII.txt').write_text('XX.AA\tExample Region\n')
        (root/'countryInfo.txt').write_text('XX\tXXX\t00\tXX\tExample Country\n')
        (root/'acquisition.json').write_text(json.dumps({'files':[{'file':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in root.iterdir()]}))
        return Gazetteer(root)

    def test_spherical_nearest_handles_dateline_and_preserves_coordinates(self):
        g=self.gazetteer();self.assertEqual(g.nearest(0,-179.9)['name'],'Dateline Town')
        self.assertLess(g.nearest(0,-179.9)['distance_km'],23)
        with self.assertRaises(ValueError):g.nearest(float('nan'),0)
        f=test_library.LibraryTests();f.setUp();v=f.build();groups=v.places()['places'];refresh(groups,g,f.f.root/'places.db')
        rebuilt=Library(f.f.catalog,f.f.db,organization=v.organization.path)
        self.assertEqual(rebuilt.search(query='Synthetic Town')['total'],1)
        self.assertEqual(rebuilt.search(query='Example Country')['total'],1)
        place=rebuilt.places()['places'][0];self.assertEqual(place['name'],'Near Synthetic Town')
        rebuilt.organize('place',{'place':place['id'],'name':'Our special place'})
        self.assertEqual(rebuilt.places()['places'][0]['name'],'Our special place')
        self.assertEqual(rebuilt.search(query='Our special place')['items'][0]['location']['lat'],40)
