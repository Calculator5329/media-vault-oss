"""Offline basemap for the Map and Trips views, built from Natural Earth (public domain).

Two products:
  web/basemap/*.json            world-wide, low detail, committed with the viewer
  <catalog>/basemap-detail.json all roads and urban areas within reach of the
                                library's recorded places; built per library, not committed

Coordinates are [lon, lat] rounded to three decimals and simplified with
Douglas-Peucker so the whole world stays a few megabytes. The viewer draws these
on canvas tiles; no network is needed once the files exist. Street-level tiles from
OpenStreetMap are an optional overlay on top (see src/tiles.py)."""
import argparse,json,math,os,sys,urllib.request
from pathlib import Path

SOURCE='https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/'
LAYERS=['ne_50m_admin_0_countries','ne_50m_lakes','ne_50m_rivers_lake_centerlines','ne_50m_admin_1_states_provinces_lines','ne_10m_urban_areas','ne_10m_roads','ne_10m_populated_places_simple']
ROAD_KINDS={'Major Highway':1,'Beltway':1,'Bypass':2,'Secondary Highway':2,'Road':3,'Track':4,'Unknown':3}

def simplify(points,tolerance):
    """Douglas-Peucker on [x,y] pairs; keeps end points."""
    if len(points)<3 or tolerance<=0:return points
    keep=[False]*len(points);keep[0]=keep[-1]=True;stack=[(0,len(points)-1)]
    while stack:
        a,b=stack.pop();ax,ay=points[a];bx,by=points[b];dx,dy=bx-ax,by-ay;norm=math.hypot(dx,dy);best=-1;index=None
        for i in range(a+1,b):
            px,py=points[i]
            d=abs(dy*px-dx*py+bx*ay-by*ax)/norm if norm else math.hypot(px-ax,py-ay)
            if d>best:best=d;index=i
        if index is not None and best>tolerance:keep[index]=True;stack.append((a,index));stack.append((index,b))
    return [p for p,k in zip(points,keep) if k]

def rnd(c,d=3):return [round(c[0],d),round(c[1],d)]
def dedupe(line):
    out=[]
    for c in line:
        if not out or out[-1]!=c:out.append(c)
    return out
def clean(line,tol,d=3):return dedupe([rnd(c,d) for c in simplify(line,tol)])
def rings(geom,tol,d=3):
    polys=[geom['coordinates']] if geom['type']=='Polygon' else geom['coordinates']
    out=[]
    for poly in polys:
        for r in poly:
            r=clean(r,tol,d)
            if len(r)>=4:out.append(r)
    return out
def lines(geom,tol,d=3):
    ls=[geom['coordinates']] if geom['type']=='LineString' else geom['coordinates']
    return [l for l in (clean(l,tol,d) for l in ls) if len(l)>=2]

def bbox(rows):
    xs=[c[0] for r in rows for c in r];ys=[c[1] for r in rows for c in r]
    return (min(xs),min(ys),max(xs),max(ys))

def load(source,name):
    path=Path(source)/f'{name}.geojson'
    if not path.is_file():
        path.parent.mkdir(parents=True,exist_ok=True)
        print(f'fetching {name} from Natural Earth',file=sys.stderr)
        with urllib.request.urlopen(SOURCE+f'{name}.geojson',timeout=300) as r,path.open('wb') as out:out.write(r.read())
    return json.load(path.open())['features']

def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w') as out:json.dump(obj,out,separators=(',',':'))
    return os.path.getsize(path)

def build_world(source,target):
    sizes={}
    land=[]
    for f in load(source,'ne_50m_admin_0_countries'):
        p=f['properties'];land.append({'n':p['NAME'],'r':p.get('LABELRANK',5),'l':[round(p.get('LABEL_Y',0),2),round(p.get('LABEL_X',0),2)],'p':rings(f['geometry'],0.01,2)})
    sizes['land']=write(target/'land.json',land)
    water={'lakes':[],'rivers':[]}
    for f in load(source,'ne_50m_lakes'):
        p=f['properties'];water['lakes'].append({'n':p.get('name') or '','z':p.get('min_zoom',0),'p':rings(f['geometry'],0.005)})
    for f in load(source,'ne_50m_rivers_lake_centerlines'):
        p=f['properties'];water['rivers'].append({'n':p.get('name') or '','z':p.get('min_zoom',0),'l':lines(f['geometry'],0.005)})
    sizes['water']=write(target/'water.json',water)
    borders=[{'z':f['properties'].get('MIN_ZOOM',0),'l':lines(f['geometry'],0.005)} for f in load(source,'ne_50m_admin_1_states_provinces_lines')]
    sizes['borders']=write(target/'borders.json',borders)
    highways=[]
    for f in load(source,'ne_10m_roads'):
        p=f['properties'];kind=ROAD_KINDS.get(p['type'],3)
        if p['type'].startswith('Ferry') or kind>2:continue
        highways.append({'k':kind,'z':p.get('min_zoom',7),'n':p.get('label') or '','l':lines(f['geometry'],0.004)})
    sizes['highways']=write(target/'highways.json',highways)
    places=[[p['name'],round(p['latitude'],3),round(p['longitude'],3),p['scalerank'],p.get('min_zoom',9),p.get('pop_max',0),1 if p.get('adm0cap') else 0] for p in (f['properties'] for f in load(source,'ne_10m_populated_places_simple'))]
    sizes['places']=write(target/'places.json',places)
    return sizes

def near(box,centres,reach):
    """True when a bounding box comes within `reach` degrees of any centre (lat, lon)."""
    x0,y0,x1,y1=box
    return any(x0-reach<=lon<=x1+reach and y0-reach<=lat<=y1+reach for lat,lon in centres)

def build_detail(source,target,centres,reach=5.0):
    """Roads of every class and urban areas within `reach` degrees of the given centres."""
    roads=[];urban=[]
    for f in load(source,'ne_10m_roads'):
        p=f['properties']
        if p['type'].startswith('Ferry'):continue
        ls=lines(f['geometry'],0.0015)
        if not ls or not near(bbox(ls),centres,reach):continue
        roads.append({'k':ROAD_KINDS.get(p['type'],3),'z':p.get('min_zoom',7),'n':p.get('label') or p.get('name') or '','l':ls})
    for f in load(source,'ne_10m_urban_areas'):
        p=f['properties']
        if p.get('area_sqkm',0)<5:continue
        rs=rings(f['geometry'],0.002)
        if not rs or not near(bbox(rs),centres,reach):continue
        urban.append({'z':p.get('min_zoom',0),'p':rs})
    return write(target,{'roads':roads,'urban':urban,'centres':len(centres),'reach':reach})

def library_centres(database,imports):
    from .library import Library
    library=Library(database,imports)
    seen={};
    for p in library.places()['places']:seen[(round(p['lat'],1),round(p['lon'],1))]=1
    return list(seen)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--source',type=Path,default=Path.home()/'.cache/tmp/natural-earth',help='Folder holding the Natural Earth GeoJSON files (downloaded when missing)')
    parser.add_argument('--world',type=Path,help='Write the world-wide layers into this folder (web/basemap)')
    parser.add_argument('--detail',type=Path,help='Write the per-library detail file here')
    parser.add_argument('--database',type=Path);parser.add_argument('--imports',type=Path)
    parser.add_argument('--reach',type=float,default=5.0,help='Degrees around recorded places to keep at full detail')
    args=parser.parse_args(argv)
    if args.world:
        for name,size in build_world(args.source,args.world).items():print(f'{name}: {size//1024} KB')
    if args.detail:
        if not (args.database and args.imports):parser.error('--detail needs --database and --imports')
        centres=library_centres(args.database,args.imports)
        print(f'detail: {build_detail(args.source,args.detail,centres,args.reach)//1024} KB around {len(centres)} recorded areas')

if __name__=='__main__':main()
