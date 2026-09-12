"""Evidence-backed proposals for files that are missing a date or a location.

Nothing here writes. Every proposal names the rule that produced it and the
evidence behind it, so the owner accepts a whole group knowing what it rests
on. Accepting appends ordinary ``set_date`` and ``set_location`` events to the
corrections log; the catalog itself is never touched, and a rebuilt index
replays the same answers.

Rules, strongest first. Dates: a file sits in a numbered sequence between two
dated files taken within a month of each other; a file has one dated sequence
neighbour, or two that disagree; the file's modification day, when that day is
not a mass copy. Locations: every located photo from the same day sits in one
0.1 degree area; the nearest located photo in time that day is within three
hours; the day falls inside a saved trip. Screenshots, generated images and
downloaded assets get no proposal at all: the owner can mark them as not expected, which takes them
out of the missing count without inventing a fact.
"""
import collections
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath
import re

SCREENSHOT_NAME=re.compile(r'screen ?shot|screen ?cap|screen ?record|screenshot|^snip|clipboard',re.I)
GENERATED_FOLDER=re.compile(r'Gemini Apps|AI Studio|My Activity',re.I)
SEQUENCE=re.compile(r'^(.*?)(\d{3,})(?!.*\d{3,})')
ASSET_EXTENSIONS={'.png','.bmp','.gif','.webp','.svg','.ico'}
HASH_NAME=re.compile(r'^[a-f0-9]{16,}(-\(\d+\))?$',re.I)
MASS_COPY_DAY=100
SEQUENCE_WINDOW_DAYS=31
NEARBY_SECONDS=3*3600

STRENGTH_ORDER={'strong':0,'medium':1,'weak':2,'none':3}
STRENGTH_NOTE={'strong':'Strong evidence','medium':'Good evidence','weak':'Weak evidence','none':'No proposal'}


def folder_of(library,item):
    rows=library.sources_by_id.get(item['id'],[])
    if not rows:return ''
    row=rows[0];path=row.get('member') or row['source']
    return str(PurePosixPath(path).parent)


def sequence_key(name):
    match=SEQUENCE.match(PurePosixPath(name).stem)
    return (match.group(1).casefold(),int(match.group(2))) if match else None


def not_a_capture(library,item):
    return bool(SCREENSHOT_NAME.search(item['name']) or GENERATED_FOLDER.search(folder_of(library,item)))


def asset_like(item):
    return item['extension'] in ASSET_EXTENSIONS or bool(HASH_NAME.match(PurePosixPath(item['name']).stem))


def span(seconds):
    minutes=seconds//60
    return f'{minutes} minutes' if minutes<120 else f'{minutes//60} hours'


def seconds_of_day(item):
    value=(item.get('date') or {}).get('value')
    if not value or len(value)<=10:return None
    try:moment=datetime.fromisoformat(value)
    except ValueError:return None
    return moment.hour*3600+moment.minute*60+moment.second


def modified_day(library,item):
    record=library.paths.get(item['id'])
    if not record:return None
    return date.fromtimestamp(record[2]/1e9).isoformat()


def public(item,proposal,evidence,strength):
    keys=('id','content_hash','name','kind','extension','day','date','location','size','duration')
    return {**{k:item.get(k) for k in keys},'proposal':proposal,'evidence':evidence,'strength':strength}


def calibrate_modified(library):
    """How often the modification day equals the recorded capture day, measured on files that carry one."""
    same=week=total=0
    for item in library.items:
        if not item['day'] or item.get('date_inferred') or item.get('date_confirmed'):continue
        modified=modified_day(library,item)
        if not modified:continue
        total+=1;gap=abs((date.fromisoformat(modified)-date.fromisoformat(item['day'])).days)
        same+=gap==0;week+=gap<=7
    return {'total':total,'same_day':same,'within_week':week}


def visible(library):
    hidden=library.organization.read()['hidden']
    return [i for i in library.items if i['content_hash'] not in hidden]


def date_groups(library,items):
    groups=collections.OrderedDict()
    def add(rule,title,meaning,strength,item,proposal,evidence,action='accept'):
        group=groups.setdefault(rule,{'id':rule,'rule':rule,'title':title,'meaning':meaning,'strength':strength,'action':action,'items':[]})
        group['items'].append(public(item,proposal,evidence,strength))
    undated=[i for i in items if not i['day'] and not i.get('date_dismissed')]
    placed=set()
    for item in undated:
        if not_a_capture(library,item):
            add('not-a-capture','Screenshots and generated images','Named like a screenshot, or exported from a chat or image tool. They never carried a capture date, so there is nothing to recover. Marking them as not expected takes them out of the missing count; it invents no date.','none',item,None,'Named '+item['name'],action='dismiss')
            placed.add(item['id'])
    by_folder=collections.defaultdict(list)
    for item in items:by_folder[folder_of(library,item)].append(item)
    for rows in by_folder.values():
        sequences=collections.defaultdict(dict)
        for item in rows:
            key=sequence_key(item['name'])
            if key:sequences[key[0]][key[1]]=item
        for numbers in sequences.values():
            order=sorted(numbers)
            for position,number in enumerate(order):
                item=numbers[number]
                if item['day'] or item['id'] in placed or item.get('date_dismissed'):continue
                before=next((numbers[order[j]] for j in range(position-1,-1,-1) if numbers[order[j]]['day']),None)
                after=next((numbers[order[j]] for j in range(position+1,len(order)) if numbers[order[j]]['day']),None)
                if before is None and after is None:continue
                if before and after:
                    span=abs((date.fromisoformat(after['day'])-date.fromisoformat(before['day'])).days)
                    if span<=SEQUENCE_WINDOW_DAYS:
                        add('sequence','Between dated files in the same sequence',f'The file number sits between two files from the same camera or export that carry dates at most {SEQUENCE_WINDOW_DAYS} days apart. The earlier neighbour\'s day is proposed.','strong',item,{'date':before['day']},f"Between {before['name']} ({before['day']}) and {after['name']} ({after['day']})")
                        placed.add(item['id']);continue
                nearest=min((n for n in (before,after) if n),key=lambda n:abs(sequence_key(n['name'])[1]-number))
                other=after if nearest is before else before
                add('sequence-loose','Next to one dated file in the same sequence','Only one side of the numbered sequence carries a date, or the two sides disagree by more than a month. The nearest dated neighbour\'s day is proposed; check a few before accepting the group.','weak',item,{'date':nearest['day']},f"Nearest dated neighbour {nearest['name']} ({nearest['day']})"+(f"; the other side is {other['name']} ({other['day']})" if other else ''))
                placed.add(item['id'])
    for item in undated:
        if item['id'] in placed or not asset_like(item):continue
        add('asset','Downloads, artwork and app assets','Image formats cameras do not produce, or names that are only a hash, with no dated neighbours. Game assets, wallpapers, generated art and saved downloads land here. They were never taken, so marking them as not expected is the honest answer; restore any that turn out to be scans or exports of real photos.','none',item,None,'Named '+item['name'],action='dismiss')
        placed.add(item['id'])
    calibration=calibrate_modified(library)
    day_counts=collections.Counter(modified_day(library,i) for i in undated)
    for item in undated:
        if item['id'] in placed:continue
        modified=modified_day(library,item)
        if not modified or day_counts[modified]>=MASS_COPY_DAY:continue
        share=calibration['same_day']*100//calibration['total'] if calibration['total'] else 0
        week=calibration['within_week']*100//calibration['total'] if calibration['total'] else 0
        add('modified','File modification day',f'The day the file was last written. Days shared by {MASS_COPY_DAY} or more files are skipped as bulk copies. On this archive\'s files that do carry a capture date, the modification day matches it {share}% of the time and lands within a week {week}% of the time, so treat these as a guess to check, not a fact.','weak',item,{'date':modified},f"Last modified {modified}; {day_counts[modified]-1} other undated files share that day")
        placed.add(item['id'])
    unexplained=[i for i in undated if i['id'] not in placed]
    return list(groups.values()),unexplained


def location_groups(library,items):
    state=library.organization.read();names=state['places']
    def place_name(key):
        if key is None:return 'an unknown area'
        if names.get(key):return names[key]
        geographic=library.geographic_names.get(key)
        return 'Near '+geographic['name'] if geographic else 'area '+key
    def phrase(key):
        name=place_name(key)
        return name[0].lower()+name[1:] if name.startswith('Near ') else 'in '+name
    def slug(key):
        # Neighbouring 0.1 degree squares around one town share a review group; each item still carries its own coordinates.
        return re.sub(r'[^a-z0-9]+','-',place_name(key).casefold()).strip('-') or key
    groups=collections.OrderedDict()
    def add(rule,group_id,title,meaning,strength,item,proposal,evidence,action='accept'):
        group=groups.setdefault(group_id,{'id':group_id,'rule':rule,'title':title,'meaning':meaning,'strength':strength,'action':action,'items':[]})
        group['items'].append(public(item,proposal,evidence,strength))
    unlocated=[i for i in items if not i['location'] and not i.get('location_dismissed')]
    located_by_day=collections.defaultdict(list)
    for item in items:
        if item['location'] and item['day']:located_by_day[item['day']].append(item)
    trips=[t for t in state['trips'].values() if t['id'] not in state['archived_trips']]
    place_points={p['id']:(p['lat'],p['lon']) for p in library.places()['places']}
    placed=set();undated=[];ambiguous=[]
    for item in unlocated:
        if not_a_capture(library,item):
            add('not-a-capture','not-a-capture','Screenshots and generated images','Named like a screenshot, or exported from a chat or image tool. They were never taken anywhere, so there is no location to recover. Marking them as not expected takes them out of the missing count.','none',item,None,'Named '+item['name'],action='dismiss')
            placed.add(item['id']);continue
        if not item['day']:undated.append(item);continue
        peers=located_by_day.get(item['day'],[])
        when=seconds_of_day(item)
        def gap(peer):
            other=seconds_of_day(peer)
            return None if when is None or other is None else abs(other-when)
        if peers:
            keys={library.place_key(p) for p in peers}
            timed=[p for p in peers if gap(p) is not None]
            nearest=min(timed,key=gap) if timed else None
            if len(keys)==1:
                key=next(iter(keys));anchor=nearest or peers[0]
                minutes=f'; the closest in time is {span(gap(nearest))} away' if nearest else ''
                add('same-day','same-day:'+slug(key),place_name(key),f'Every located photo or video from the same day sits in one 0.1 degree area, about 11 km across. The coordinates of the closest located file that day are proposed.','strong',item,{'lat':round(anchor['location']['lat'],6),'lon':round(anchor['location']['lon'],6),'place':key,'place_name':place_name(key)},f"{len(peers)} located {'file' if len(peers)==1 else 'files'} that day, all {phrase(key)}{minutes}")
                placed.add(item['id']);continue
            if nearest is not None and gap(nearest)<=NEARBY_SECONDS:
                key=library.place_key(nearest);others=sorted({place_name(k) for k in keys if k!=key})
                add('nearby-time','nearby-time:'+slug(key),place_name(key)+' (moved that day)',f'Located photos from the same day sit in more than one area, so the nearest located file within {NEARBY_SECONDS//3600} hours is used. Check the odd one out before accepting.','medium',item,{'lat':round(nearest['location']['lat'],6),'lon':round(nearest['location']['lon'],6),'place':key,'place_name':place_name(key)},f"Closest located file {span(gap(nearest))} away is {phrase(key)}; that day also has photos {', '.join(others)}")
                placed.add(item['id']);continue
            ambiguous.append(item);continue
        trip=next((t for t in trips if t['after']<=item['day']<=t['before']),None)
        if trip and trip.get('place'):
            points=[place_points[k] for k in trip['place'].split(',') if k in place_points]
            if points:
                lat=sum(p[0] for p in points)/len(points);lon=sum(p[1] for p in points)/len(points)
                add('trip','trip:'+trip['id'],'During '+trip['name'],'The day falls inside a saved trip and nothing else that day carries a location. The centre of the trip\'s areas is proposed, which is rough: accept only for trips that stayed in one place.','weak',item,{'lat':round(lat,6),'lon':round(lon,6),'place':trip['place'].split(',')[0],'place_name':trip['name']},f"Taken {item['day']}, inside {trip['name']} ({trip['after']} to {trip['before']})")
                placed.add(item['id']);continue
        ambiguous.append(item)
    return list(groups.values()),undated,ambiguous


def proposals(library,kind):
    items=visible(library)
    dismissed=[i for i in items if not i['day'] and i.get('date_dismissed')] if kind=='date' else [i for i in items if not i['location'] and i.get('location_dismissed')]
    if kind=='date':
        groups,unexplained=date_groups(library,items);undated=[];ambiguous=[]
        filled=sum(bool(i['day']) for i in items)
    else:
        groups,undated,ambiguous=location_groups(library,items)
        filled=sum(bool(i['location']) for i in items)
    if dismissed:
        groups.append({'id':'not-expected','rule':'not-expected','title':'Marked as not expected','meaning':'Files you took out of the missing count. Restoring one puts it back so it can receive a proposal again.','strength':'none','action':'restore','items':[public(i,None,'Marked as not expected',"none") for i in dismissed]})
    # Strongest evidence first, biggest groups first within a strength, so the view opens on the group most worth accepting.
    groups.sort(key=lambda g:(STRENGTH_ORDER[g['strength']],g['action']!='accept',-len(g['items'])))
    proposed=sum(len(g['items']) for g in groups if g['action']=='accept')
    missing=len(items)-filled-len(dismissed)
    coverage={'total':len(items),'filled':filled,'missing':missing,'not_expected':len(dismissed),'proposed':proposed,
              'no_evidence':len(unexplained) if kind=='date' else len(ambiguous),'undated':len(undated),
              'owner':sum(bool(i.get('date_confirmed' if kind=='date' else 'location_confirmed')) for i in items)}
    for group in groups:group['count']=len(group['items'])
    return {'kind':kind,'coverage':coverage,'groups':groups}


def page(data,group,offset,limit):
    groups=data['groups']
    chosen=next((g for g in groups if g['id']==group),None) or (groups[0] if groups else None)
    offset=max(0,int(offset));limit=max(1,min(int(limit),200))
    listing=[{k:g[k] for k in ('id','rule','title','strength','action','count')} for g in groups]
    if chosen is None:
        return {**data,'groups':listing,'group':None}
    rows=chosen['items'][offset:offset+limit]
    return {**data,'groups':listing,'group':{**chosen,'items':rows,'offset':offset,'next_offset':offset+limit if offset+limit<len(chosen['items']) else None}}


def events_for(kind,action,rows):
    """Turn accepted rows into log events, batched by identical value and capped at the log's 200 items per event."""
    def chunks(digests):
        digests=sorted(set(digests))
        for start in range(0,len(digests),200):yield digests[start:start+200]
    if action in ('dismiss','restore'):
        for batch in chunks(r['content_hash'] for r in rows):
            yield ('dismiss_gap' if action=='dismiss' else 'restore_gap'),{'gap':kind,'contents':batch}
        return
    by_value=collections.defaultdict(list)
    for row in rows:
        proposal=row['proposal']
        if proposal is None:raise ValueError('These files have no proposal to accept')
        key=proposal['date'] if kind=='date' else (proposal['lat'],proposal['lon'])
        by_value[key].append(row['content_hash'])
    for key,digests in by_value.items():
        for batch in chunks(digests):
            if kind=='date':yield 'set_date',{'contents':batch,'date':key}
            else:yield 'set_location',{'contents':batch,'lat':key[0],'lon':key[1]}
