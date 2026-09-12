"""Conservative visit proposals from recorded dates and coordinate areas."""
from collections import defaultdict
from datetime import date
import hashlib


def proposals(items,place_key,labels):
    areas=defaultdict(list)
    for item in items:
        if not item.get('content_hash') or not item.get('day') or not item.get('location'):continue
        if not item.get('date_confirmed') and (item.get('date_status') in ('conflict','timezone_unknown','invalid') or (item.get('date') or {}).get('meaning')!='capture'):continue
        areas[place_key(item)].append(item)
    results=[]
    for key,photos in areas.items():
        groups=[]
        for item in sorted(photos,key=lambda i:(i['day'],i['id'])):
            day=date.fromisoformat(item['day'])
            if not groups or (day-date.fromisoformat(groups[-1][-1]['day'])).days>2 or (day-date.fromisoformat(groups[-1][0]['day'])).days>14:groups.append([])
            groups[-1].append(item)
        for group in groups:
            if len(group)<3:continue
            after,before=group[0]['day'],group[-1]['day']
            results.append({'id':hashlib.sha256((key+after+before).encode()).hexdigest(),'name':labels.get(key,'Recorded place')+' · '+after,'after':after,'before':before,'place':key,'count':len(group),'cover':group[0]['id']})
    return sorted(results,key=lambda g:(g['after'],g['id']),reverse=True)
