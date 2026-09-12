"""Read-only assistant queries over the local Media Vault.

Use --server http://127.0.0.1:8770 to reuse the viewer's model and current snapshot.
Full output contains private names and coordinates; --count or --ids limits it.
"""
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlencode,urlsplit
from urllib.request import build_opener,HTTPRedirectHandler
from .server import Viewer


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Local query redirects are refused')


def remote(base,endpoint,params):
    url=urlsplit(base)
    if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost') or url.username or url.password or url.path not in ('','/') or url.query or url.fragment or not url.port or not 1024<=url.port<=65535:raise ValueError('Use an explicit loopback viewer URL and port')
    with build_opener(NoRedirect).open(base.rstrip('/')+'/api/'+endpoint+('?' +urlencode(params) if params else ''),timeout=90) as stream:return json.load(stream)


def resolve(rows,value):
    matched=[r for r in rows if r['id']==value or r.get('name','').casefold()==value.casefold()]
    if len(matched)!=1:raise ValueError('Name is missing or ambiguous; list the collection IDs first')
    return matched[0]


def execute(args,request):
    if args.list:
        result=request(args.list,{})
        return {'total':len(result[args.list])} if args.count else result
    if args.album:
        if args.query or args.year or args.after or args.before or args.person or args.place or args.trip or args.near or args.category or args.kind!='all' or args.mode!='metadata' or args.offset:raise ValueError('An album uses its saved selection; omit search filters')
        album=resolve(request('albums',{})['albums'],args.album)
        result=request('album/'+album['id'],{})
        if args.count:return {'total':result['total'],'missing':result['missing']}
        if args.ids:return {'items':[{'id':i['id'],'content_hash':i['content_hash']} for i in result['items']],'total':result['total'],'missing':result['missing']}
        return result
    filters={'q':args.query,'year':args.year,'after':args.after,'before':args.before,'kind':args.kind,'person':args.person,'place':args.place}
    if args.person and args.person!='untagged' and not re.fullmatch('[a-f0-9]{32}',args.person):filters['person']=resolve(request('people',{})['people'],args.person)['id']
    if args.place and not re.fullmatch(r'-?\d+:-?\d+',args.place):filters['place']=resolve(request('places',{})['places'],args.place)['id']
    if args.trip:
        if args.after or args.before or args.place:raise ValueError('A saved trip supplies its own date and place filters')
        trip=resolve(request('trips',{})['trips'],args.trip);filters.update({k:trip[k] for k in ('after','before','place')});filters['trip']=trip['id']
    if args.near:
        if args.mode!='metadata':raise ValueError('Radius queries currently use metadata mode')
        filters['near']=args.near
    if args.category:
        if args.mode!='descriptions':raise ValueError('Category requires descriptions mode')
        filters['category']=args.category
    if args.slideshow:
        if args.mode!='metadata':raise ValueError('Slideshows currently use metadata queries')
        endpoint='slideshow';filters['limit']=args.limit
    else:
        endpoint={'metadata':'items','visual':'visual','descriptions':'described','moments':'moments','video_images':'video-moments'}[args.mode];filters.update(offset=args.offset,limit=args.limit)
    result=request(endpoint,filters)
    if args.count:return {k:v for k,v in result.items() if k in ('total','indexed_contents','eligible_contents','processed_videos','truncated','indexed_frames','eligible_frames','sampled_videos')}
    if args.ids:return {'items':[{'id':i['id'],'content_hash':i.get('content_hash'),**({'start_seconds':i['moment']['start'],'end_seconds':i['moment']['end']} if i.get('moment') else {})} for i in result['items']],'total':result['total'],'next_offset':result.get('next_offset')}
    return result


def parser():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',type=Path,default=Path('.catalog/catalog.db'));p.add_argument('--imports',type=Path);p.add_argument('--server');p.add_argument('--query',default='');p.add_argument('--year',default='');p.add_argument('--after',default='');p.add_argument('--before',default='');p.add_argument('--near',default='');p.add_argument('--kind',default='all');p.add_argument('--person',default='');p.add_argument('--place',default='');p.add_argument('--trip',default='');p.add_argument('--album',default='');p.add_argument('--mode',choices=['metadata','visual','descriptions','moments','video_images'],default='metadata');p.add_argument('--category',default='');p.add_argument('--list',choices=['people','places','trips','albums']);p.add_argument('--slideshow',action='store_true');p.add_argument('--count',action='store_true');p.add_argument('--ids',action='store_true');p.add_argument('--offset',type=int,default=0);p.add_argument('--limit',type=int,default=80);return p


def main():
    p=parser();args=p.parse_args()
    try:
        if args.server:request=lambda endpoint,params:remote(args.server,endpoint,params)
        else:
            if args.mode in ('visual','video_images'):raise ValueError('Visual queries require --server to reuse its local model')
            if args.imports:
                from .library import Library
                viewer=Library(args.database,args.imports)
            else:viewer=Viewer(args.database)
            def request(endpoint,params):
                if endpoint.startswith('album/'):
                    if not hasattr(viewer,'album_items'):raise ValueError('Albums require --imports or --server')
                    return viewer.album_items(endpoint.removeprefix('album/'))
                data=dict(params)
                if 'q' in data:data['query']=data.pop('q')
                if not hasattr(viewer,'organization'):
                    for key in ('person','place'):
                        if data.pop(key,''):raise ValueError('Organization queries require --imports or --server')
                if endpoint=='moments':data.pop('kind',None)
                method={'items':'search','described':'described'}.get(endpoint,endpoint)
                if not hasattr(viewer,method):raise ValueError('This query requires --imports or --server')
                return getattr(viewer,method)(**data)
        print(json.dumps(execute(args,request),indent=2))
    except (ValueError,OSError) as exc:p.error(str(exc))


if __name__=='__main__':main()
