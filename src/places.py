"""Offline nearest-settlement labels, distinct from owner names and GPS facts."""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import zipfile
from .imports import database,now
from .kit import create_fact_table,insert_fact


class Gazetteer:
    def __init__(self,root):
        import numpy as np
        root=Path(root);self.np=np;self.rows=[]
        receipt=json.loads((root/'acquisition.json').read_text())
        for record in receipt['files']:
            if hashlib.sha256((root/record['file']).read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Gazetteer changed')
        self.identity=hashlib.sha256(json.dumps(receipt['files'],sort_keys=True).encode()).hexdigest()
        regions={row[0]:row[1] for line in (root/'admin1CodesASCII.txt').read_text().splitlines() if len(row:=line.split('\t'))>=2}
        countries={row[0]:row[4] for line in (root/'countryInfo.txt').read_text().splitlines() if not line.startswith('#') and len(row:=line.split('\t'))>=5}
        with zipfile.ZipFile(root/'cities500.zip') as archive:
            with archive.open('cities500.txt') as stream:
                for line in io.TextIOWrapper(stream,encoding='utf-8'):
                    r=line.rstrip('\n').split('\t')
                    if len(r)<19:raise ValueError('Invalid settlement row')
                    lat,lon=float(r[4]),float(r[5])
                    if not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Invalid settlement position')
                    self.rows.append({'id':r[0],'name':r[1],'region':regions.get(r[8]+'.'+r[10],r[10]),'country':countries.get(r[8],r[8]),'lat':lat,'lon':lon})
        coords=np.radians([[r['lat'],r['lon']] for r in self.rows]);lat,lon=coords[:,0],coords[:,1]
        self.vectors=np.column_stack((np.cos(lat)*np.cos(lon),np.cos(lat)*np.sin(lon),np.sin(lat)))

    def nearest(self,lat,lon):
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('Invalid coordinates')
        a,b=math.radians(lat),math.radians(lon);v=self.np.asarray([math.cos(a)*math.cos(b),math.cos(a)*math.sin(b),math.sin(a)])
        dots=self.vectors@v;index=int(self.np.argmax(dots));distance=6371*math.acos(max(-1,min(1,float(dots[index]))))
        return {**self.rows[index],'distance_km':round(distance,2),'meaning':'Nearest listed settlement to this browsing area; not a boundary or exact address.'}


def refresh(groups,gazetteer,output):
    revision=gazetteer.identity+':'+hashlib.sha256(json.dumps(sorted((g['id'],g['lat'],g['lon']) for g in groups)).encode()).hexdigest()
    with database(output,[]) as conn:
        create_fact_table(conn,'place_labels',{'place_key':'TEXT NOT NULL','gazetteer':'TEXT NOT NULL','value_json':'TEXT NOT NULL'})
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS place_label_identity ON place_labels(place_key,gazetteer)')
        with conn:
            for group in groups:
                value=gazetteer.nearest(group['lat'],group['lon'])
                if conn.execute('SELECT 1 FROM place_labels WHERE place_key=? AND gazetteer=?',(group['id'],revision)).fetchone():continue
                insert_fact(conn,'place_labels',{'place_key':group['id'],'gazetteer':revision,'value_json':json.dumps(value),'source_path':'https://download.geonames.org/export/dump/','source_span':json.dumps({'geonameid':value['id'],'area_lat':group['lat'],'area_lon':group['lon'],'dataset_sha256':gazetteer.identity}), 'extractor':'nearest-geonames-settlement','extractor_version':'1','confidence':0.5,'derived_at':now(),'tier':'personal'})
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('places_current',?)",(revision,))
    return {'areas':len(groups),'settlements':len(gazetteer.rows),'dataset':gazetteer.identity}


def main():
    from .library import Library
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--catalog',type=Path,required=True);p.add_argument('--imports',type=Path,required=True);p.add_argument('--gazetteer',type=Path,required=True);p.add_argument('--database',type=Path,required=True);a=p.parse_args()
    library=Library(a.catalog,a.imports)
    print(json.dumps(refresh(library.places()['places'],Gazetteer(a.gazetteer),a.database)))


if __name__=='__main__':main()
