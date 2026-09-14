
(()=>{'use strict';
const $=id=>document.getElementById(id),root=document.documentElement;
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state={view:'photos',mode:'everything',person:'',trip:'',place:'',stack:'',bucket:'',collectionName:'',selecting:false,filter:'all',query:'',year:'',after:'',before:'',theme:'dark',tile:190};let firstOffset=0,loadingPage=false,timelineData=null,selection=new Set(),peopleCache=[],placeCache=[],pendingContents=[],createOnly=false,preferredPerson='',editingPerson='',pendingFaces=[],faceReview=null,currentFaceGroup=[],showIgnoredFaces=false,faceGroupLimit=100;let items=[],summary=null,nextOffset=null,requestId=0,selected=0,timer;
const size=n=>n>=1e9?(n/1e9).toFixed(1)+' GB':n>=1e6?(n/1e6).toFixed(1)+' MB':Math.round(n/1e3)+' KB';
const number=n=>Number(n).toLocaleString();
const dateLabel=day=>{if(!day)return 'Date not recorded';const parsed=new Date(day+'T12:00:00');return Number.isNaN(parsed.valueOf())?day:parsed.toLocaleDateString(undefined,{year:'numeric',month:'long',day:'numeric'})};
const duration=n=>Number.isFinite(n)?Math.floor(n/60)+':'+String(Math.floor(n%60)).padStart(2,'0'):'';
async function api(path,options={}){const response=await fetch(path,{cache:'no-store',...options});const data=await response.json();if(!response.ok)throw new Error(response.status===503?'The local search is busy or unavailable. Try again shortly.':data.error||'The local library could not be read.');return data}
function nav(){
 const browse=state.view==='photos';$('timeScopeNote').textContent=['moments','video_images'].includes(state.mode)?'Video moments matching your dates':'Photos matching your search and dates';$('makeHighlights').hidden=!browse||(state.mode==='moments'||state.mode==='video_images');$('descriptionCategory').hidden=state.mode!=='descriptions';
 $('playSlideshow').hidden=!browse||(state.mode==='descriptions'||(state.mode==='moments'||state.mode==='video_images'))||(state.mode==='visual'&&!!state.query.trim());
 $('filters').hidden=true;$('year').hidden=true;if($('timeline')){const hide=!browse||(['moments','video_images'].includes(state.mode));$('timeline').hidden=hide;document.body.classList.toggle('has-timeline',!hide&&!!timelineData?.years?.length)}
 $('organizeTools').hidden=!browse||(state.mode==='moments'||state.mode==='video_images')||!summary?.capabilities.organization;
 // The plain library needs no heading; a collection, search or review does. Idle tools fade until something is selected.
 $('heading').hidden=browse&&!state.collectionName&&!state.query.trim()&&!state.stack&&state.filter==='all'&&state.mode==='everything';
 $('organizeTools').classList.toggle('idle',!state.selecting&&!selection.size&&!state.person&&!state.place&&!state.trip&&!state.bucket);
 if(!$('organizeTools').hidden)$('organizeTools').hidden=[...$('organizeTools').children].every(c=>c.hidden||!c.textContent.trim());
 $('runVisual').hidden=false;
 document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===state.view);b.setAttribute('aria-current',b.dataset.view===state.view?'page':'false')});
 document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter===state.filter));
 renderTokens();selectionTools();
}
function selectionTools(){
 $('morePersonFaces').hidden=state.view!=='photos'||!state.person||state.person==='untagged';
 $('selectPhotos').textContent=state.selecting?'Done selecting':state.trip?'Select items':'Select photos';$('doneSelecting').hidden=!state.selecting;
 $('excludeTripItems').hidden=!state.trip||!selection.size;$('reviewTripExclusions').hidden=!state.trip;
 $('selectionCount').textContent=selection.size?`${selection.size} selected`:state.selecting?(state.trip?'Choose items to remove from this trip':'Choose photos or videos'):'';
 $('selectPage').hidden=!state.selecting;$('tagSelection').hidden=!selection.size||items.some(p=>selection.has(p.content_hash)&&!['photo','video'].includes(p.kind));
 $('removeSelection').hidden=!selection.size||!state.person||state.person==='untagged';$('setDateSelection').hidden=!selection.size;$('favoriteSelection').hidden=!selection.size||!summary?.capabilities.favorites;$('hideSelection').hidden=$('favoriteSelection').hidden;$('hideSelection').textContent=state.filter==='hidden'?'Show in timeline':'Hide';
 $('renamePerson').hidden=!state.person||state.person==='untagged';$('clearCollection').hidden=!state.person&&!state.place&&!state.trip&&!state.bucket;
 $('bucketMenu').hidden=!selection.size||!summary?.capabilities.buckets;$('bucketMenu').open=false;$('removeFromBucket').hidden=!state.bucket||!selection.size;$('bucketTools').hidden=!state.bucket;$('bucketTools').open=false;if(!$('bucketMenu').hidden)renderBucketMenu();$('renamePlace').hidden=!state.place;
 const me=peopleCache.find(p=>p.id===state.person);$('hidePerson').hidden=$('mergePerson').hidden=$('renamePerson').hidden;$('personMenu').hidden=$('renamePerson').hidden;$('personMenu').open=false;$('hidePerson').textContent=me?.hidden?'Show in suggestions':'Hide from suggestions';
}

const tileHtml=p=>`<button class="tile" data-photo="${p.id}" data-testid="photos.tile.open.${p.id}" ${p.moment?`data-moment="${p.moment.start}"`:""} data-selected="${selection.has(p.content_hash)}" aria-label="${state.selecting?'Select':'Open'} ${escape(p.name)}${p.moment?' at '+duration(p.moment.start):''}" ${state.selecting?`aria-pressed="${selection.has(p.content_hash)}"`:''}><img loading="lazy" decoding="async" src="${p.frame_id?'/frame-preview/'+p.frame_id:'/preview/'+p.id}" alt="${escape(p.name)}">${p.kind==='video'?'<span class="duration">▶ Video</span>':''}${p.favorite?'<span class="fav-badge" title="Favorite">♥</span>':''}${p.stack&&p.stack.top&&!state.stack?`<span class="stack-badge" role="button" tabindex="0" data-fan="${p.stack.id}" data-testid="photos.tile.fan.${p.id}" title="${openFan===p.stack.id?'Close the stack':`Fan out the ${p.stack.count} similar shots`}" aria-expanded="${openFan===p.stack.id}">⧉ ${p.stack.count}</span>`:''}${state.selecting&&p.content_hash?`<span class="select-mark">${selection.has(p.content_hash)?'✓':''}</span>`:''}<span class="tile-title">${p.moment?escape(duration(p.moment.start)+' · '+p.moment.text):escape(p.name)}</span></button>`;
// Perceived speed: time from the request to the API answer, and to the last visible preview. window.__vaultPerf holds the last reading.
let perfTicket=0,perfT0=0;function perfStart(t){perfTicket=t;perfT0=performance.now()}
function perfImages(){if(!perfT0)return;const t=perfTicket,api=Math.round(performance.now()-perfT0);const imgs=[...$('content').querySelectorAll('img')].filter(i=>{const r=i.getBoundingClientRect();return r.top<innerHeight*1.5});let left=imgs.length;const done=()=>{if(t!==perfTicket)return;window.__vaultPerf={api,visible:Math.round(performance.now()-perfT0),tiles:imgs.length,query:state.query,filter:state.filter,view:state.view};console.debug('vault perf',window.__vaultPerf)};if(!left){done();return}imgs.forEach(i=>{const one=()=>{if(--left===0)done()};if(i.complete)one();else{i.addEventListener('load',one,{once:true});i.addEventListener('error',one,{once:true})}})}
function renderTiles(){
 let html='';const visual=['visual','video_images','descriptions'].includes(state.mode)&&state.query.trim();
 for(const group of visual?['visual']:[...new Set(items.map(p=>p.day))]){
  const batch=visual?items:items.filter(p=>p.day===group);
  html+=`<div class="group-head" ${visual?'':`data-day="${escape(group||'')}"`}><h2>${visual?(state.mode==='descriptions'?'Search matches':'Closest image matches'):escape(dateLabel(group))}</h2><span>${batch.length} shown</span></div><div class="grid">`+batch.map(tileHtml).join('')+'</div>';
 }
 $('content').innerHTML=html||'<div class="empty"><h2>No photos match these filters</h2><p>Try removing a date or collection filter.</p><button class="primary" data-clear data-testid="photos.empty.clear">Clear filters</button></div>';
 $('content').querySelectorAll('[data-fan]').forEach(badge=>{const toggle=e=>{e.stopPropagation();e.preventDefault();fanOut(badge.dataset.fan)};badge.onclick=toggle;badge.onkeydown=e=>{if(e.key==='Enter'||e.key===' ')toggle(e)}});
 if(openFan)renderFan();
 $('content').querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>{img.closest('.tile').classList.add('failed');const note=document.createElement('span');note.className='preview-unavailable';note.textContent='Preview unavailable';img.closest('.tile').append(note)},{once:true}));
 $('loadMore').hidden=nextOffset===null;$('loadEarlier').hidden=firstOffset===0;selectionTools();perfImages();
}

const trail=[];let restoring=false,lastKey=null,lastSnapshot=null,ignorePop=false,pendingMapView=null,pendingTripMapView=null;
const VIEW_LABELS={photos:'Photos',people:'People',places:'Places',map:'Map',trips:'Trips',buckets:'Buckets',albums:'Albums',quality:'Archive quality',gaps:'Fill the gaps'};
function navKey(){return [state.view,state.mode,state.person,state.place,state.trip,state.bucket,state.stack,state.query,state.filter,state.year,state.after,state.before].join('|')}
function navLabel(){return state.view==='photos'?(state.stack?'Similar shots':state.collectionName||(state.query.trim()?'Search results':'Photos')):VIEW_LABELS[state.view]||'Back'}
function trackNavigation(){
 const key=navKey();
 if(lastSnapshot&&key!==lastKey&&!restoring){
  lastSnapshot.scroll=window.scrollY;
  if(lastSnapshot.state.view==='map'&&leafletMap)lastSnapshot.map={center:leafletMap.getCenter(),zoom:leafletMap.getZoom()};
  if(lastSnapshot.state.view==='trips'&&tripMap)lastSnapshot.tripMap={center:tripMap.getCenter(),zoom:tripMap.getZoom()};
  trail.push(lastSnapshot);if(trail.length>40)trail.shift();
  try{history.pushState({vault:trail.length},'')}catch{}
 }
 lastKey=key;lastSnapshot={state:{...state,selecting:false},scroll:0,map:null,tripMap:null,label:navLabel()};renderBack();
}
function renderBack(){const b=$('backButton');const to=trail[trail.length-1];b.hidden=!to;if(to)b.textContent='← Back to '+to.label}
function goBack(){
 const s=trail.pop();if(!s)return;restoring=true;Object.assign(state,s.state);selection.clear();
 $('query').value=state.query;$('year').value=state.year;$('after').value=state.after;$('before').value=state.before;$('searchMode').value=state.mode;
 pendingMapView=s.map;pendingTripMapView=s.tripMap;lastKey=navKey();lastSnapshot={state:{...state},scroll:0,map:null,tripMap:null,label:navLabel()};renderBack();
 load().then(()=>window.scrollTo(0,s.scroll)).finally(()=>{restoring=false});
}
$('backButton').onclick=()=>{if(history.state&&history.state.vault&&trail.length){ignorePop=true;history.back()}else goBack()};
window.addEventListener('popstate',()=>{if(ignorePop){ignorePop=false;goBack();return}if(trail.length)goBack()});
async function load(append=false,start=0){
 if(!append){openFan='';fanMembers=[]}
 const request=++requestId;if(!append)trackNavigation();nav();$('loadMore').hidden=true;$('loadEarlier').hidden=true;perfStart(request);
 if(state.view==='people'){await renderPeople(request);return}
 if(state.view==='places'){await renderPlaces(request);return}
 if(state.view==='albums'){await renderAlbums(request);return}
 if(state.view==='buckets'){await renderBuckets(request);return}
 if(state.view==='trips'){await renderTrips(request);return}
 if(state.view==='quality'){renderQuality();return}
 if(state.view==='gaps'){await renderGaps(request);return}
 if(state.view==='map'){await renderMap(request);return}
 if(state.mode==='video_images'&&!state.query.trim()){items=[];nextOffset=null;$('title').textContent='Images in videos';$('subtitle').textContent='Describe a moment to search sampled video images.';$('content').innerHTML='<div class="empty"><h2>Find a moment</h2><p>Try red car, a beach, or another visible scene.</p></div>';return}
 if(state.mode==='everything'&&state.query.trim()&&!state.person&&!state.place&&!state.trip){await renderEverything(request);return}
 const visual=state.mode==='visual'&&!!state.query.trim();
 $('title').textContent=state.stack?'Similar shots':state.collectionName|| (state.query?'Search results':'Your photos');
 $('subtitle').textContent=visual?'Finding image matches on this computer…':'Loading photos…';
 $('content').setAttribute('aria-busy','true');$('runVisual').disabled=true;$('runVisual').textContent=visual?'Searching…':'Search';
 loadingPage=true;
 try{
  const params=new URLSearchParams({category:$('descriptionCategory').value,q:state.query,kind:state.filter,year:state.year,after:state.after,before:state.before,person:state.person,place:state.place,trip:state.trip,stack:state.stack,bucket:state.bucket,offset:String(append==='earlier'?Math.max(0,firstOffset-80):append?nextOffset||0:start),limit:String(append==='earlier'?firstOffset-Math.max(0,firstOffset-80):80)});
  const data=await api((state.mode==='video_images'?'/api/video-moments?':state.mode==='moments'?'/api/moments?':state.mode==='descriptions'?'/api/described?':visual?'/api/visual?':'/api/items?')+params);
  if(request!==requestId)return;
  if(append==='earlier'){items=[...data.items,...items];firstOffset=Math.max(0,firstOffset-80)}else if(append){items=[...items,...data.items];nextOffset=data.next_offset}else{items=data.items;nextOffset=data.next_offset;firstOffset=start}
  $('subtitle').textContent=state.mode==='video_images'?`${items.length} of ${number(data.total)} closest moments · ${number(data.eligible_frames)} indexed samples in these filters. Up to 60 keyframe samples per video; brief appearances can be missed. Matches may be unrelated.`:state.mode==='moments'?`${number(data.total)} matching moments · ${number(data.processed_videos)} videos processed. Automatic transcripts can contain mistakes.`:state.mode==='descriptions'?`${number(data.total)} matches in ${number(data.indexed_contents)} analyzed files · AI tags may be wrong. Unanalyzed files are not searched.`:visual?`${items.length} of ${number(data.total)} closest matches · ${number(data.eligible_contents)} indexed photos in these filters. AI object/color tags appear first when available; other matches may be unrelated.`:`${number(data.total)} ${data.total===1?'file':'files'} · ${items.length} shown`;
  const grew=append==='earlier'?document.documentElement.scrollHeight:0;renderTiles();if(append==='earlier')window.scrollBy(0,document.documentElement.scrollHeight-grew);if(!append)loadTimeline(request,params);
 }catch(error){if(request!==requestId)return;$('subtitle').textContent=error.message;$('content').innerHTML='<div class="empty"><h2>Search could not finish</h2><p>Try again, or use Text &amp; file details.</p><button class="primary" data-retry data-testid="search.error.retry">Retry</button></div>'}
 finally{loadingPage=false;if(request===requestId){$('content').removeAttribute('aria-busy');$('runVisual').disabled=false;$('runVisual').textContent='Search'}}
}

function renderQuality(){loadWorkerProgress(requestId);$('title').textContent='Archive quality';$('subtitle').textContent='Measured from the current catalog. No files are removed.';if(!summary){$('content').innerHTML='<div class="empty">Catalog summary unavailable.</div>';return}const cards=[[summary.deduplicated?'Library items':'Files cataloged',`${number(summary.photos)} images · ${number(summary.videos)} ${summary.videos===1?'video':'videos'} · ${size(summary.bytes)}`,summary.files,'all'],['Missing a date','No parseable capture, modification, or container creation date was found.',summary.missing_date,'undated'],['Dates to review','Different source dates or unknown timezones need a choice. Equivalent UTC timestamps are grouped together.',summary.date_review_needed||0,'date_review'],['Missing a location','These files have no parsed GPS location. Unknown remains unknown.',summary.missing_location,'unknown'],[summary.deduplicated?'Items with verified copies':'Exact duplicate files',summary.deduplicated?'Verified copies share one library entry. All original source copies remain untouched.':'Byte-identical files found by SHA-256. This count includes both copies; nothing is deleted.',summary.duplicate_files,'duplicates'],['Metadata extraction errors','Inspect these separately from files with successfully read, empty metadata.',summary.metadata_errors,'errors']];$('content').innerHTML='<div class="quality">'+cards.map(([title,desc,count,filter])=>`<article><div><h2>${title}</h2><p>${escape(desc)}</p></div><strong>${number(count)}</strong><button class="outline" data-quality="${filter}" data-testid="quality.filter.${filter}">Browse</button></article>`).join('')+'</div>'+`<p class="local-note">Recorded dates: ${escape(summary.first_date||'unknown')} through ${escape(summary.last_date||'unknown')}. These are file metadata, not proof that the archive covers that entire period. ${summary.first_date?.startsWith('1970')?'Some 1970 dates may be placeholders.':''} Missing dates and locations can be filled from the archive's own evidence in <button class="link-button" data-view="gaps" data-testid="quality.gaps.open">Fill the gaps</button>.<br>${escape(summary.catalog_scope||'Google Photos ZIP exports are not included in this catalog yet. They need reconciliation, including sidecars and overlap.')}</p>`}
async function loadWorkerProgress(ticket){try{const p=await api('/api/progress');if(ticket!==requestId||state.view!=='quality')return;const rows=[];if(p.import_service?.state&&p.import_service.state!=='not_started'){const labels={scanning:'Scanning sources',verifying:'Verifying new files',connecting_metadata:'Connecting metadata',complete:'Last import completed',waiting_for_sources:'Waiting for the external drive',error:'Import needs attention',busy:'Another import is running'};rows.push(['Continuing imports',(labels[p.import_service.state]||'Import status unavailable')+(p.import_service.at?' · '+new Date(p.import_service.at).toLocaleString():''),'Originals read-only'])}if(p.imports.available){const r=p.imports;rows.push(['Source verification',`${number(r.verified_occurrences)} of ${number(r.occurrences)} source copies verified · ${size(r.verified_bytes||0)} of ${size(r.bytes||0)}. ${number(r.errors)} read errors recorded.`,number(r.verified_contents)+' distinct files'])}if(p.metadata.available)rows.push(['Connected metadata','Source-linked facts are connected to the library. Competing values stay separate.',number(p.metadata.contents)+' files']);if(p.vision.available)rows.push(['Visual search index',`${number(p.vision.errors)} decode failures recorded. ${summary?.capabilities.semantic_search?'Describe a photo in the main search box to find image matches.':'Local image matching is available through the query worker; this viewer has no model loaded.'}`,number(p.vision.contents)+' photos']);if(p.frames?.available)rows.push(['Video image samples',`${number(p.frames.frames)} retained samples · ${number(p.frames.errors)} sampling errors. Brief appearances may be missed.`,number(p.frames.contents)+' videos processed']);if(p.scenes?.available)rows.push(['Images in videos',`${number(p.scenes.errors)} indexing errors. Choose Images in videos to search sampled moments.`,number(p.scenes.contents)+' indexed samples']);if(p.transcripts?.available)rows.push(['Video transcripts',`${number(p.transcripts.segments)} timestamped segments · ${number(p.transcripts.errors)} errors. Search Spoken words in videos.`,number(p.transcripts.contents)+' processed']);if(p.descriptions?.available)rows.push(['AI descriptions',`${number(p.descriptions.errors)} processing errors. Choose AI tags & descriptions for object/color and category search.`,number(p.descriptions.contents)+' analyzed']);if(p.ocr?.available)rows.push(['Text recognition',`${number(p.ocr.with_text)} photos contain recognized English text · ${number(p.ocr.errors)} errors. Choose Text & file details to search it.`,number(p.ocr.contents)+' processed']);const html='<section id="workerCoverage"><div class="group-head"><h2>Library preparation</h2><button class="chip" id="refreshCoverage" data-testid="quality.coverage.refresh">Refresh progress</button></div><div class="quality">'+(rows.length?rows.map(([title,description,count])=>`<article><div><h2>${escape(title)}</h2><p>${escape(description)}</p></div><strong style="font-size:18px">${escape(count)}</strong></article>`).join(''):'<p class="local-note">Worker progress is not available for this catalog.</p>')+'</div></section>';const old=$('workerCoverage');if(old)old.outerHTML=html;else $('content').insertAdjacentHTML('beforeend',html);$('refreshCoverage').onclick=async()=>{try{summary=await api('/api/summary');$('videoImagesOption').disabled=!summary.capabilities.video_images;$('transcriptOption').disabled=!summary.capabilities.transcripts;$('descriptionOption').disabled=!summary.capabilities.descriptions;$('year').replaceChildren(new Option('All years',''),...summary.years.map(y=>new Option(y,y)));$('year').value=state.year;renderQuality()}catch{notify('Library refresh unavailable.')}}}catch{}}
function notify(message){$('toast').textContent=message;$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,4000)}
async function saveOrganization(op,data){
 const response=await fetch('/api/organize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({op,data})});
 const result=await response.json();if(!response.ok||!result.saved)throw new Error(result.error||'Could not save. Try again.');return result;
}
function openCollection(person='',place='',name=''){
 if(person&&person!=='untagged')preferredPerson=person;state.view='photos';state.person=person;state.place=place;state.trip='';state.bucket='';state.collectionName=name;state.query='';state.filter='all';state.year='';state.after='';state.before='';
 selection.clear();state.selecting=false;$('query').value='';$('year').value='';$('after').value='';$('before').value='';load();
}
let faceSensitivity=localStorage.getItem('faceSensitivity')||'balanced',showSingleFaces=false,showHiddenPeople=false,peopleSort=localStorage.getItem('peopleSort')||'count';
const sortPeople=rows=>[...rows].sort((a,b)=>peopleSort==='name'?a.name.localeCompare(b.name):peopleSort==='faces'?(b.confirmed_faces-a.confirmed_faces)||a.name.localeCompare(b.name):(b.count-a.count)||a.name.localeCompare(b.name));
async function renderPeople(ticket){
 $('title').textContent='People';$('subtitle').textContent='Loading people…';
 try{const data=await api('/api/people');if(ticket!==requestId)return;peopleCache=data.people;
 const hiddenCount=peopleCache.filter(p=>p.hidden).length;
 $('content').innerHTML=`<div class="people-tools"><label class="tool"><span>Sort</span><select id="peopleSort" data-testid="people.toolbar.sort"><option value="count">Most photos</option><option value="faces">Most confirmed faces</option><option value="name">Name</option></select></label>${hiddenCount?`<button class="tool quiet" id="toggleHiddenPeople" data-testid="people.toolbar.toggle-hidden">${showHiddenPeople?'Hide hidden people':`Show ${number(hiddenCount)} hidden`}</button>`:''}</div>`+'<div class="people">'+sortPeople(peopleCache.filter(p=>showHiddenPeople||!p.hidden)).map(p=>`<button class="person-card${p.hidden?' hidden-person':''}" data-person="${p.id}" data-testid="people.card.open.${p.id}">${p.face?`<img src="/face-preview/${p.face}" alt="">`:p.cover?`<img src="/preview/${p.cover}" alt="">`:`<div class="person-placeholder">${escape(p.name.slice(0,1))}</div>`}<strong>${escape(p.name)}</strong><small>${number(p.count)} ${p.count===1?'file':'files'}${p.confirmed_faces?' · '+number(p.confirmed_faces)+' faces':''}${p.hidden?' · hidden':''}</small></button>`).join('')+'<button class="person-card person-add" id="createPerson" data-testid="people.card.create"><div class="person-placeholder">+</div><strong>Add a person</strong><small>Or name a group below</small></button></div>';
 $('createPerson').onclick=()=>openPerson([],true);$('peopleSort').value=peopleSort;$('peopleSort').onchange=e=>{peopleSort=e.target.value;localStorage.setItem('peopleSort',peopleSort);load()};const toggleHidden=$('toggleHiddenPeople');if(toggleHidden)toggleHidden.onclick=()=>{showHiddenPeople=!showHiddenPeople;load()};
 $('content').querySelectorAll('[data-person]').forEach(b=>b.onclick=()=>{const p=peopleCache.find(p=>p.id===b.dataset.person);openCollection(p.id,'',p.name)});
 $('subtitle').textContent=`${peopleCache.length} named · ${number(data.untagged)} photos and videos without people tags. Your choices stay saved.`;
 await renderFaceGroups(ticket);
 }catch{$('content').innerHTML='<div class="empty">People could not be loaded. Keep the expanded local viewer running.</div>'}
}
async function renderFaceGroups(ticket){
 const section=document.createElement('section');section.id='suggestedFaces';section.innerHTML='<p class="local-note">Grouping faces…</p>';$('content').append(section);
 try{const data=await api('/api/faces?sensitivity='+faceSensitivity+(showIgnoredFaces?'&ignored=1':''));if(ticket!==requestId||state.view!=='people')return;faceReview=data;
 const groups=data.groups.filter(g=>showSingleFaces||showIgnoredFaces||g.faces.length>1);
 const showing=showIgnoredFaces?'ignored':showSingleFaces?'all':'groups';
 section.innerHTML=`<div class="group-head review-head"><div><h2>${showIgnoredFaces?'Ignored detections':'Unnamed faces'}</h2><span>${data.ready?`${number(data.faces)} faces found in ${number(data.processed_photos)} of ${number(data.candidate_photos)} photos and videos · ${number(data.confirmed)} already named`:'Waiting for the first face scan'}</span></div><span class="spacer"></span><label class="tool"><span>Show</span><select id="faceShow" data-testid="faces.toolbar.show"><option value="groups">Groups (${number(data.groups.length-data.single_groups)})</option><option value="all">Groups and single faces (${number(data.single_groups)})</option><option value="ignored">Ignored</option></select></label><label class="tool"><span>Grouping</span><select id="faceSensitivity" data-testid="faces.toolbar.sensitivity"><option value="tight">Tighter</option><option value="balanced">Balanced</option><option value="loose">Looser</option></select></label><button class="tool quiet" id="browseUntagged" data-testid="faces.toolbar.browse-untagged">Tag whole photos and videos</button></div>`
  +(data.ready?(groups.length?'<div class="face-groups">'+groups.slice(0,faceGroupLimit).map(g=>`<button class="face-group" data-face-group="${g.id}" data-testid="faces.group.open.${g.id}"><img class="cover" loading="lazy" src="/face-preview/${g.faces[0].face_id}" alt=""><div class="samples">${g.faces.slice(1,5).map(f=>`<img loading="lazy" src="/face-preview/${f.face_id}" alt="">`).join('')}</div><small>${number(g.faces.length)} ${g.faces.length===1?'face':'faces'}</small>${g.looks_like?`<span class="hint">Looks like ${escape(g.looks_like.name)}</span>`:''}</button>`).join('')+'</div>':'<div class="empty">Nothing left to review here.</div>'):'<p class="local-note">The first local face pass is running. Groups appear after its checkpoint is published.</p>');
 $('faceSensitivity').value=faceSensitivity;$('faceSensitivity').onchange=e=>{faceSensitivity=e.target.value;localStorage.setItem('faceSensitivity',faceSensitivity);faceGroupLimit=100;load()};
 $('faceShow').value=showing;$('faceShow').onchange=e=>{const v=e.target.value;showIgnoredFaces=v==='ignored';showSingleFaces=v==='all';load()};
 $('browseUntagged').onclick=()=>{openCollection('untagged','','Photos and videos without people tags');state.selecting=true;selectionTools()};
 if(groups.length>faceGroupLimit){const more=document.createElement('button');more.className='outline';more.dataset.testid='faces.groups.more';more.textContent=`More groups (${number(groups.length-faceGroupLimit)} left)`;more.style.margin='4px 0 30px';more.onclick=()=>{faceGroupLimit+=100;load()};section.append(more)}
 section.querySelectorAll('[data-face-group]').forEach(b=>b.onclick=()=>openFaceGroup(data.groups.find(g=>g.id===b.dataset.faceGroup)));
 }catch{section.innerHTML='<p class="local-note">Face suggestions are not available yet.</p>'}
}
function openFaceGroup(group,checked=true){
 currentFaceGroup=group.faces;const n=group.faces.length;
 $('faceHeading').textContent=showIgnoredFaces?'Ignored detections':n===1?'Who is this?':`Who is this? ${number(n)} faces`;
 $('faceHint').hidden=!group.looks_like||showIgnoredFaces;if(group.looks_like){$('faceHint').textContent='Yes, this is '+group.looks_like.name;$('faceHint').onclick=()=>assignFaces(group.looks_like.id)}
 $('faceNote').textContent=showIgnoredFaces?'Restore detections that are real faces you want to review again.':'Every face starts selected. Uncheck any face that is someone else; those stay unnamed for later.';
 $('facePerson').innerHTML='<option value="">Existing person…</option>'+peopleCache.map(p=>`<option value="${p.id}">${escape(p.name)}</option>`).join('');$('facePerson').parentElement.hidden=showIgnoredFaces||!peopleCache.length;
 $('faceName').value='';$('faceName').parentElement.hidden=showIgnoredFaces;$('nameFaces').hidden=showIgnoredFaces;
 $('ignoreFaces').textContent=showIgnoredFaces?'Restore selected':'Not a person / ignore';
 faceSelection=new Map(group.faces.map(f=>[f.face_id,checked]));faceRenderTicket++;renderFaceChoices(group.faces,faceRenderTicket);
 $('toggleAllFaces').textContent=checked?'Select none':'Select all';$('faceError').textContent='';$('faceDialog').showModal();
}
async function saveFaces(op,person=''){
 const faces=selectedFaces();if(!faces.length)throw new Error('Select at least one face.');
 for(let i=0;i<faces.length;i+=200){const chunk=faces.slice(i,i+200);await saveOrganization(op,op==='faces'?{person,faces:chunk}:{faces:chunk.map(f=>f.face_id)})}
 return faces.length;
}
async function assignFaces(person){try{const n=await saveFaces('faces',person);$('faceDialog').close();await refreshAfterSave();notify(`${n} ${n===1?'face':'faces'} confirmed`)}catch(e){$('faceError').textContent=e.message}}
$('morePersonFaces').onclick=async()=>{
 const person=state.person,button=$('morePersonFaces');button.disabled=true;
 try{const data=await api('/api/person-suggestions/'+person);if(state.person!==person)return;
 if(!data.reference_faces){notify('Confirm a face for this person in People first. Whole-photo tags do not identify a particular face.');return}
 if(!data.candidates.length){notify('No further likely faces right now.');return}
 preferredPerson=person;openFaceGroup({id:'suggest',faces:data.candidates,looks_like:{...data.person,similarity:0}},false);
 $('faceHeading').textContent='More possible faces of '+data.person.name;$('faceNote').textContent=`${data.candidates.length} of ${data.total} ${data.total===1?'candidate':'candidates'} from ${data.reference_faces} confirmed ${data.reference_faces===1?'face':'faces'}. Select only faces you recognize. Nothing is selected automatically.`;
 }catch(e){notify(e.message)}finally{button.disabled=false}
};
let faceSelection=new Map(),faceRenderTicket=0;
function renderFaceChoices(faces,ticket){
 const box=$('faceChoices');box.innerHTML='';const chunk=40;let index=0;
 const step=()=>{if(ticket!==faceRenderTicket)return;const slice=faces.slice(index,index+chunk);index+=chunk;
  box.insertAdjacentHTML('beforeend',slice.map((f,i)=>`<label><input type="checkbox" value="${f.face_id}" data-testid="faces-dialog.face.select.${f.face_id}" aria-label="Select face ${index-chunk+i+1}"${faceSelection.get(f.face_id)?' checked':''}><img loading="lazy" decoding="async" src="/face-preview/${f.face_id}" alt=""></label>`).join(''));
  if(index<faces.length)setTimeout(step,0)};
 step();
}
$('faceChoices').addEventListener('change',e=>{if(e.target.type==='checkbox')faceSelection.set(e.target.value,e.target.checked)});
function selectedFaces(){return currentFaceGroup.filter(f=>faceSelection.get(f.face_id)).map(f=>({face_id:f.face_id,content_hash:f.content_hash}))}
$('closeFaces').onclick=()=>$('faceDialog').close();$('faceName').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();$('nameFaces').click()}});$('faceName').addEventListener('input',()=>{if($('faceName').value.trim())$('facePerson').value=''});$('facePerson').onchange=()=>{if($('facePerson').value)$('faceName').value=''};$('personName').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();$('savePerson').click()}});
$('toggleAllFaces').onclick=()=>{const all=[...faceSelection.values()].every(Boolean);currentFaceGroup.forEach(f=>faceSelection.set(f.face_id,!all));$('faceChoices').querySelectorAll('input').forEach(b=>b.checked=!all);$('toggleAllFaces').textContent=all?'Select all':'Select none'};
$('facePerson').onchange=()=>{if($('facePerson').value)$('faceName').value=''};
$('nameFaces').onclick=async()=>{const name=$('faceName').value.trim(),existing=$('facePerson').value;
 try{let person=existing;if(!person){if(!name){$('faceError').textContent='Enter a name or choose a person.';return}const result=await saveOrganization('person',{person:'',name});person=result.data.person}await assignFaces(person)}catch(e){$('faceError').textContent=e.message}};
$('ignoreFaces').onclick=async()=>{try{const n=await saveFaces(showIgnoredFaces?'restore_faces':'ignore_faces');$('faceDialog').close();await refreshAfterSave();notify(showIgnoredFaces?`${n} restored`:`${n} ignored. History retained.`)}catch(e){$('faceError').textContent=e.message}};
let albumEditing='',albumRows=[],archivedAlbums=false;
let bucketsCache=null,archivedBuckets=false;
async function loadBuckets(force=false){if(!bucketsCache||force)bucketsCache=(await api('/api/buckets')).buckets;return bucketsCache}
function openBucket(id,name){state.view='photos';state.person='';state.place='';state.trip='';state.stack='';state.bucket=id;state.collectionName=name;state.query='';state.filter='all';state.year='';state.after='';state.before='';selection.clear();state.selecting=false;$('query').value='';$('year').value='';$('after').value='';$('before').value='';load()}
function askBucketName(title,current='',action='Create'){return new Promise(resolve=>{$('bucketHeading').textContent=title;$('bucketName').value=current;$('bucketError').textContent='';$('saveBucket').textContent=action;const d=$('bucketDialog');const done=v=>{d.close();resolve(v)};$('saveBucket').onclick=()=>{const v=$('bucketName').value.trim();if(!v){$('bucketError').textContent='Give the bucket a name.';return}done(v)};$('closeBucket').onclick=()=>done(null);d.oncancel=e=>{e.preventDefault();done(null)};$('bucketName').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('saveBucket').click()}};d.showModal();$('bucketName').focus();$('bucketName').select()})}
async function createBucket(){const name=await askBucketName('New bucket');if(!name)return null;try{const r=await saveOrganization('bucket',{bucket:'',name});await loadBuckets(true);notify('Bucket created');return {id:r.event?.data?.bucket||r.data?.bucket||(bucketsCache.find(b=>b.name===name)||{}).id,name}}catch(e){notify(e.message);return null}}
async function addToBucket(id,contents,name){try{await saveOrganization('bucket_add',{bucket:id,contents});await loadBuckets(true);notify(`Added ${contents.length===1?'1 item':contents.length+' items'} to ${name}`)}catch(e){notify(e.message)}}
async function renderBucketMenu(){const list=$('bucketMenuList');list.innerHTML='<button disabled data-testid="toolbar.bucket-menu.loading">Loading…</button>';try{const rows=await loadBuckets()}catch{list.innerHTML='<button disabled data-testid="toolbar.bucket-menu.unavailable">Buckets unavailable</button>';return}
 list.innerHTML=bucketsCache.map(b=>`<button data-bucket-add="${b.id}" data-testid="toolbar.bucket-menu.add.${b.id}">${escape(b.name)} <span class="muted">${number(b.count)}</span></button>`).join('')+'<button data-bucket-new data-testid="toolbar.bucket-menu.new">New bucket…</button>';
 list.querySelectorAll('[data-bucket-add]').forEach(b=>b.onclick=async()=>{$('bucketMenu').open=false;const row=bucketsCache.find(x=>x.id===b.dataset.bucketAdd);await addToBucket(row.id,[...selection],row.name);items.forEach(p=>{if(selection.has(p.content_hash)&&!p.buckets.includes(row.id))p.buckets.push(row.id)});selection.clear();state.selecting=false;renderTiles()});
 list.querySelector('[data-bucket-new]').onclick=async()=>{$('bucketMenu').open=false;const made=await createBucket();if(!made)return;await addToBucket(made.id,[...selection],made.name);selection.clear();state.selecting=false;renderTiles()};
}
$('removeFromBucket').onclick=async()=>{try{await saveOrganization('bucket_remove',{bucket:state.bucket,contents:[...selection]});await loadBuckets(true);notify('Removed from this bucket');selection.clear();state.selecting=false;load()}catch(e){notify(e.message)}};
$('renameBucket').onclick=async()=>{$('bucketTools').open=false;const name=await askBucketName('Rename bucket',state.collectionName,'Rename');if(!name)return;try{await saveOrganization('bucket',{bucket:state.bucket,name});await loadBuckets(true);state.collectionName=name;load()}catch(e){notify(e.message)}};
$('archiveBucket').onclick=async()=>{$('bucketTools').open=false;try{await saveOrganization('archive_bucket',{bucket:state.bucket});await loadBuckets(true);notify('Bucket archived; restore it from Buckets › Archived');state.bucket='';state.collectionName='';state.view='buckets';load()}catch(e){notify(e.message)}};
let exportTimer=null;
function exportSummary(st){const parts=[`${number(st.copied)} copied`];if(st.existing)parts.push(`${number(st.existing)} already there`);if(st.failed)parts.push(`${number(st.failed)} failed`);return `${parts.join(', ')} of ${number(st.total)} · ${(st.bytes/1e9).toFixed(2)} GB`}
async function exportProgress(){clearTimeout(exportTimer);const note=$('exportNote');if(!note)return;try{const st=await api('/api/export-status');if(!$('exportNote'))return;
 if(st.state==='idle'){note.hidden=true;return}note.hidden=false;
 if(st.state==='running'){note.innerHTML=`Exporting ${st.buckets} ${st.buckets===1?'bucket':'buckets'} to <code>${escape(st.destination)}</code>: ${exportSummary(st)}. <button class="chip" id="cancelExport" data-testid="export.progress.cancel">Stop</button>`;$('cancelExport').onclick=()=>fetch('/api/export-buckets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({destination:'',buckets:[],cancel:true})}).catch(()=>{});exportTimer=setTimeout(exportProgress,1000);return}
 note.innerHTML=`${st.state==='done'?'Export finished':st.state==='cancelled'?'Export stopped':'Export failed'} · <code>${escape(st.destination)}</code> · ${exportSummary(st)}.`+(st.errors?.length?`<details data-testid="export.progress.fold"><summary data-testid="export.progress.toggle">${st.errors.length} ${st.errors.length===1?'problem':'problems'}</summary><ul>${st.errors.map(e=>`<li>${escape(e)}</li>`).join('')}</ul></details>`:'');
 }catch{note.hidden=true}}
async function openExport(buckets){const d=$('exportDialog');$('exportError').textContent='';$('exportPath').value=localStorage.getItem('exportPath')||'';$('exportCount').textContent=`${buckets.length} ${buckets.length===1?'bucket':'buckets'}, ${number(buckets.reduce((n,b)=>n+b.count,0))} items. One folder per bucket, named after it; files already there are left alone.`;
 d.showModal();$('exportPath').focus();
 $('startExport').onclick=async()=>{const destination=$('exportPath').value.trim();if(!destination){$('exportError').textContent='Enter a folder path.';return}
  try{const r=await fetch('/api/export-buckets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({destination,buckets:[],create_parents:$('startExport').dataset.create==='1'})});const body=await r.json();if(!r.ok)throw new Error(body.error||'Export could not start');localStorage.setItem('exportPath',destination);d.close();notify('Export started');exportProgress()}catch(e){$('exportError').textContent=e.message;
   // A missing folder is the usual outcome of a fresh path: offer to make it rather than send them to a terminal.
   if(/does not exist/.test(e.message)){$('startExport').dataset.create='1';$('startExport').textContent='Create folders and export'}}};
 $('startExport').dataset.create='';$('startExport').textContent='Start export';$('exportPath').oninput=()=>{$('startExport').dataset.create='';$('startExport').textContent='Start export';$('exportError').textContent=''};
 $('closeExport').onclick=()=>d.close()}
async function renderBuckets(ticket){
 $('title').textContent=archivedBuckets?'Archived buckets':'Buckets';$('subtitle').textContent='Collections you manage by hand. Add photos and videos over time; nothing is inferred and originals stay untouched.';
 try{const data=await api('/api/buckets'+(archivedBuckets?'?archived=1':''));if(ticket!==requestId)return;if(!archivedBuckets)bucketsCache=data.buckets;
 $('content').innerHTML='<div class="collection-actions"><button class="primary" id="newBucket" data-testid="buckets.toolbar.new">New bucket</button><button class="outline" id="toggleArchivedBuckets" data-testid="buckets.toolbar.toggle-archived">'+(archivedBuckets?'Back to buckets':'Archived buckets')+'</button>'+(archivedBuckets||!data.buckets.length?'':'<button class="outline" id="exportBuckets" data-testid="buckets.toolbar.export">Export to folder…</button>')+'</div><p class="local-note" id="exportNote" hidden></p><div class="places">'+data.buckets.map(b=>`<article class="place-card"><button class="place-open" data-bucket-open="${b.id}" data-testid="buckets.card.open.${b.id}" aria-label="Open ${escape(b.name)}">${b.cover?`<img loading="lazy" src="/preview/${b.cover}" alt="">`:'<div class="place-empty">Empty</div>'}</button><strong>${escape(b.name)}</strong><small>${[b.photos?number(b.photos)+' photos':'',b.videos?number(b.videos)+' videos':''].filter(Boolean).join(' · ')||'Nothing in it yet'}${b.missing?' · '+number(b.missing)+' unavailable':''}</small><div class="collection-actions" style="padding:14px"><button class="chip" data-bucket-open="${b.id}" data-testid="buckets.card.browse.${b.id}">Open</button>${archivedBuckets?`<button class="outline" data-bucket-restore="${b.id}" data-testid="buckets.card.restore.${b.id}">Restore</button>`:`<button class="outline" data-bucket-play="${b.id}" data-testid="buckets.card.play.${b.id}">Slideshow</button>`}</div></article>`).join('')+'</div>'+(!data.buckets.length?`<p class="local-note">${archivedBuckets?'No archived buckets.':'No buckets yet. Create one, then select photos or videos anywhere and choose Add to bucket.'}</p>`:'');
 $('content').insertAdjacentHTML('beforeend','<p class="local-note">Highlights you saved with Make highlights live in <button class="chip" id="openAlbums" data-testid="buckets.toolbar.albums">Saved highlights</button>.</p>');$('openAlbums').onclick=()=>{state.view='albums';load()};
 $('newBucket').onclick=async()=>{const made=await createBucket();if(made)openBucket(made.id,made.name)};if($('exportBuckets'))$('exportBuckets').onclick=()=>openExport(data.buckets);exportProgress();$('toggleArchivedBuckets').onclick=()=>{archivedBuckets=!archivedBuckets;load()};
 $('content').querySelectorAll('[data-bucket-open]').forEach(b=>b.onclick=()=>{const row=data.buckets.find(x=>x.id===b.dataset.bucketOpen);openBucket(row.id,row.name)});
 $('content').querySelectorAll('[data-bucket-restore]').forEach(b=>b.onclick=async()=>{try{await saveOrganization('restore_bucket',{bucket:b.dataset.bucketRestore});await loadBuckets(true);load()}catch(e){notify(e.message)}});
 $('content').querySelectorAll('[data-bucket-play]').forEach(b=>b.onclick=async()=>{try{const d=await api('/api/slideshow?'+new URLSearchParams({bucket:b.dataset.bucketPlay,located:'0'}));if(!d.items.length){notify('No photos in this bucket yet; slideshows show photos only.');return}slides=d.items;slideIndex=0;slidePlaying=true;$('slideCoverage').textContent=data.buckets.find(x=>x.id===b.dataset.bucketPlay).name+' · photos in this bucket, oldest first.';$('slideshow').showModal();showSlide()}catch{notify('Bucket slideshow unavailable.')}});
 }catch{$('content').innerHTML='<div class="empty">Buckets could not be loaded.</div>'}
}
async function renderDetailBuckets(item){
 const box=$('detailBuckets');box.hidden=!summary?.capabilities.buckets||!item.content_hash;if(box.hidden)return;
 let rows=[];try{rows=await loadBuckets()}catch{}
 if(items[selected]!==item)return;
 const mine=rows.filter(b=>(item.buckets||[]).includes(b.id));
 box.innerHTML=(mine.length?`<span class="muted">Buckets</span>`:'')+mine.map(b=>`<span class="tool active bucket-chip"><button data-bucket-go="${b.id}" data-testid="inspector.bucket.go.${b.id}" title="Open this bucket">${escape(b.name)}</button><button data-bucket-drop="${b.id}" data-testid="inspector.bucket.drop.${b.id}" aria-label="Remove from ${escape(b.name)}" title="Remove from this bucket">×</button></span>`).join('')+`<details class="menu" id="detailBucketMenu" data-testid="inspector.bucket-menu.fold"><summary data-testid="inspector.bucket-menu.toggle" class="tool quiet">+ Add to bucket ▾</summary><div>${rows.filter(b=>!mine.includes(b)).map(b=>`<button data-bucket-put="${b.id}" data-testid="inspector.bucket-menu.put.${b.id}">${escape(b.name)}</button>`).join('')}<button data-bucket-make data-testid="inspector.bucket-menu.make">New bucket…</button></div></details>`;
 box.querySelectorAll('[data-bucket-go]').forEach(b=>b.onclick=()=>{$('inspector').close();const row=rows.find(x=>x.id===b.dataset.bucketGo);openBucket(row.id,row.name)});
 box.querySelectorAll('[data-bucket-drop]').forEach(b=>b.onclick=async()=>{try{await saveOrganization('bucket_remove',{bucket:b.dataset.bucketDrop,contents:[item.content_hash]});item.buckets=item.buckets.filter(x=>x!==b.dataset.bucketDrop);await loadBuckets(true);notify('Removed from the bucket');renderDetailBuckets(item)}catch(e){notify(e.message)}});
 box.querySelectorAll('[data-bucket-put]').forEach(b=>b.onclick=async()=>{const row=rows.find(x=>x.id===b.dataset.bucketPut);await addToBucket(row.id,[item.content_hash],row.name);item.buckets=[...(item.buckets||[]),row.id];renderDetailBuckets(item)});
 box.querySelector('[data-bucket-make]').onclick=async()=>{const made=await createBucket();if(!made)return;await addToBucket(made.id,[item.content_hash],made.name);item.buckets=[...(item.buckets||[]),made.id];renderDetailBuckets(item)};
}
async function renderAlbums(ticket){
 $('title').textContent=archivedAlbums?'Archived highlights':'Saved highlights';$('subtitle').textContent='Photo selections saved from Make highlights, in your chosen order. Buckets are the place for collections you grow by hand.';
 try{const data=await api('/api/albums'+(archivedAlbums?'?archived=1':''));if(ticket!==requestId)return;
 $('content').innerHTML='<div class="collection-actions"><button class="primary" id="newAlbum" data-testid="albums.toolbar.new">Choose photos for an album</button><button class="outline" id="toggleArchivedAlbums" data-testid="albums.toolbar.toggle-archived">'+(archivedAlbums?'Back to albums':'Archived albums')+'</button></div><div class="places">'+data.albums.map(a=>`<article class="place-card">${a.cover?`<img loading="lazy" src="/preview/${a.cover}" alt="">`:''}<strong>${escape(a.name)}</strong><small>${number(a.count)} photos${a.missing?' · '+number(a.missing)+' unavailable':''}</small><div class="collection-actions" style="padding:14px"><button class="chip" data-album-play="${a.id}" data-testid="albums.card.play.${a.id}">Slideshow</button><button class="outline" data-album-edit="${a.id}" data-testid="albums.card.edit.${a.id}">Edit</button><button class="outline" data-album-archive="${a.id}" data-testid="albums.card.archive.${a.id}">${archivedAlbums?'Restore':'Archive'}</button></div></article>`).join('')+'</div>'+(!data.albums.length?'<p class="local-note">No albums here yet. Open Photos, choose dates or search, then Make highlights to review a selection.</p>':'');
 $('newAlbum').onclick=()=>openCollection();$('toggleArchivedAlbums').onclick=()=>{archivedAlbums=!archivedAlbums;load()};
 $('content').querySelectorAll('[data-album-edit]').forEach(b=>b.onclick=async()=>{try{const d=await api('/api/album/'+b.dataset.albumEdit);if(d.missing){notify('Some album photos are unavailable. Restore their sources before editing to preserve the selection.');return}editAlbum(d.items,d.album.name,d.album.id,'Drag-free ordering: use Earlier or Later, or remove a photo. Saving retains the previous selection in history.')}catch{notify('Album unavailable.')}});
 $('content').querySelectorAll('[data-album-archive]').forEach(b=>b.onclick=async()=>{try{await saveOrganization(archivedAlbums?'restore_album':'archive_album',{album:b.dataset.albumArchive});await load()}catch(e){notify(e.message)}});
 $('content').querySelectorAll('[data-album-play]').forEach(b=>b.onclick=async()=>{try{const d=await api('/api/album/'+b.dataset.albumPlay);if(!d.items.length){notify('No album photos are available.');return}slides=d.items;slideIndex=0;slidePlaying=true;$('slideCoverage').textContent=d.album.name+' · Your saved order.'+(d.missing?' '+d.missing+' photos unavailable.':'');$('slideshow').showModal();showSlide()}catch{notify('Album slideshow unavailable.')}});
 }catch{$('content').innerHTML='<div class="empty">Albums could not be loaded.</div>'}
}
function editAlbum(rows,name='',id='',meaning=''){
 albumEditing=id;albumRows=[...rows];$('albumHeading').textContent=id?'Edit album':'Review highlights';$('albumName').value=name;$('albumMeaning').textContent=meaning;$('albumError').textContent='';renderAlbumChoices();$('albumDialog').showModal();$('albumName').focus();
}
function renderAlbumChoices(){
 $('albumChoices').innerHTML=albumRows.map((item,i)=>`<article><img loading="lazy" src="/preview/${item.id}" alt="Photo ${i+1}" style="width:100%;height:120px;object-fit:cover;border-radius:6px"><small>${i+1} · ${escape(dateLabel(item.day))}</small><div style="display:flex;gap:4px;flex-wrap:wrap"><button class="chip" data-album-up="${i}" data-testid="album-dialog.row.up.${i}" ${i===0?'disabled':''} aria-label="Move photo ${i+1} earlier">Earlier</button><button class="chip" data-album-down="${i}" data-testid="album-dialog.row.down.${i}" ${i===albumRows.length-1?'disabled':''} aria-label="Move photo ${i+1} later">Later</button><button class="outline" data-album-remove="${i}" data-testid="album-dialog.row.remove.${i}" aria-label="Remove photo ${i+1} from selection">Remove</button></div></article>`).join('');
 $('albumChoices').querySelectorAll('[data-album-up],[data-album-down],[data-album-remove]').forEach(b=>b.onclick=()=>{const remove=b.dataset.albumRemove;if(remove!==undefined)albumRows.splice(Number(remove),1);else{const i=Number(b.dataset.albumUp??b.dataset.albumDown),j=i+(b.dataset.albumUp!==undefined?-1:1);[albumRows[i],albumRows[j]]=[albumRows[j],albumRows[i]]}renderAlbumChoices()});
}
$('makeHighlights').onclick=async()=>{const b=$('makeHighlights');$('drawer').hidden=true;b.disabled=true;try{const params=new URLSearchParams({mode:state.mode,q:state.query,category:$('descriptionCategory').value,kind:state.filter,year:state.year,after:state.after,before:state.before,person:state.person,place:state.place,trip:state.trip,limit:'30'});const d=await api('/api/highlights?'+params);if(!d.items.length){notify('No photos match these highlights filters.');return}editAlbum(d.items,state.collectionName||state.query||'Highlights','',d.meaning+' '+d.coverage)}catch(e){notify(e.message)}finally{b.disabled=false}};
$('closeAlbum').onclick=()=>$('albumDialog').close();
$('saveAlbum').onclick=async()=>{const b=$('saveAlbum');b.disabled=true;try{await saveOrganization('album',{album:albumEditing,name:$('albumName').value,contents:albumRows.map(i=>i.content_hash)});$('albumDialog').close();state.view='albums';archivedAlbums=false;await load();notify('Album saved')}catch(e){$('albumError').textContent=e.message}finally{b.disabled=false}};
let tripEditing='',tripCache=[],tripProposalLimit=60,archivedTrips=false;
function renderDetailPath(sources){
 const box=$('detailPath');box.innerHTML=sources.length?`<div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.04em">Location on disk</div>`+sources.map(s=>`<div class="path-row"><code>${escape(s.path)}${s.member?` <span class="muted">→ ${escape(s.member)}</span>`:''}</code><button class="tool quiet" data-copy="${escape(s.path)}" data-testid="inspector.source.copy-path" title="Copy the full path">Copy</button></div>`).join(''):'';
 box.querySelectorAll('[data-copy]').forEach(b=>b.onclick=()=>copyText(b.dataset.copy));
}
async function copyText(text){try{await navigator.clipboard.writeText(text);notify('Path copied')}catch{const t=document.createElement('textarea');t.value=text;document.body.append(t);t.select();try{document.execCommand('copy');notify('Path copied')}catch{notify('Could not copy; select the path and copy it by hand')}t.remove()}}
function renderDetailFlags(item){
 const box=$('detailFlags');box.hidden=!summary?.capabilities.favorites||!item.content_hash;if(box.hidden)return;
 box.innerHTML=`<button class="tool${item.favorite?' active':''}" id="toggleFavorite" data-testid="inspector.actions.favorite">${item.favorite?'♥ Favorite':'♡ Favorite'}</button><button class="tool${item.hidden?' active':''}" id="toggleHidden" data-testid="inspector.actions.hide">${item.hidden?'Show in timeline':'Hide from timeline'}</button><span class="muted">${item.hidden?'Hidden. Find it under Show: Hidden photos.':'Both are reversible and stay on this machine.'}</span>`;
 $('toggleFavorite').onclick=async()=>{try{await saveOrganization(item.favorite?'unfavorite':'favorite',{contents:[item.content_hash]});item.favorite=!item.favorite;renderDetailFlags(item);renderTiles();notify(item.favorite?'Added to favorites':'Removed from favorites')}catch(e){notify(e.message)}};
 $('toggleHidden').onclick=async()=>{try{await saveOrganization(item.hidden?'unhide':'hide',{contents:[item.content_hash]});item.hidden=!item.hidden;renderDetailFlags(item);notify(item.hidden?'Hidden from the timeline; it reappears on the next reload under Show: Hidden photos':'Back in the timeline')}catch(e){notify(e.message)}};
}
function renderDetailStack(item){
 const box=$('detailStack');if(!box)return;const stack=item.stack;box.hidden=!stack;if(!stack)return;
 box.innerHTML=`<p class="local-note">One of ${stack.count} similar shots${stack.top?', shown for the stack in the grid':''}.</p><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="chip" id="openStack" data-testid="inspector.actions.open-stack">Show all ${stack.count}</button><button class="outline" id="separateStack" data-testid="inspector.actions.separate-stack">Separate</button></div>`;
 $('openStack').onclick=()=>{$('inspector').close();state.view='photos';state.stack=stack.id;state.collectionName='';state.filter='all';state.query='';$('query').value='';load()};
 $('separateStack').onclick=async()=>{$('separateStack').disabled=true;try{await saveOrganization('unstack',{stack:stack.id});notify('Kept separate');$('inspector').close();load()}catch(e){notify(e.message);$('separateStack').disabled=false}};
}

// Stacks are always collapsed in the grid. The badge fans one out in place: every member, the top marked,
// click a member to make it the top, Separate to keep them apart. Nothing is hidden for good either way.
let openFan='',fanMembers=[];
async function fanOut(id){
 if(openFan===id){openFan='';fanMembers=[];renderTiles();return}
 try{const data=await api(`/api/items?${new URLSearchParams({stack:id,limit:'200'})}`);openFan=id;fanMembers=data.items;renderTiles()}catch(e){notify(e.message)}
}
function renderFan(){
 const anchor=$('content').querySelector(`[data-fan="${openFan}"]`);if(!anchor||!fanMembers.length)return;
 const id=openFan,contents=fanMembers.map(i=>i.content_hash),top=(fanMembers.find(i=>i.stack&&i.stack.top)||fanMembers[0]).content_hash;
 const member=i=>`<button class="tile fan-member" data-fan-member="${i.content_hash}" data-testid="stacks.fan.member.${i.content_hash}" title="${i.content_hash===top?'The top of this stack':'Make this the top'}" aria-label="${escape(i.name)}${i.content_hash===top?' (top)':''}"><img loading="lazy" src="/preview/${i.id}" alt="${escape(i.name)}">${i.content_hash===top?'<span class="duration">Top</span>':''}<span class="tile-title">${escape(i.name)}${i.width?` · ${i.width}×${i.height}`:''}</span></button>`;
 const fan=document.createElement('div');fan.className='stack-fan';fan.dataset.stack=id;
 fan.innerHTML=`<div class="stack-head"><strong>${fanMembers.length} similar shots</strong><span class="muted">Click one to make it the top of the stack.</span><span style="flex:1"></span><button class="outline" data-fan-separate data-testid="stacks.fan.separate">Separate</button><button class="outline" data-fan-open data-testid="stacks.fan.open">Open</button><button class="outline" data-fan-close data-testid="stacks.fan.close">Close</button></div><div class="grid stack-grid">${fanMembers.map(member).join('')}</div>`;
 anchor.closest('.tile').insertAdjacentElement('afterend',fan);
 const act=async(b,op,payload,message)=>{b.disabled=true;try{await saveOrganization(op,payload);notify(message);openFan='';fanMembers=[];load()}catch(e){notify(e.message);b.disabled=false}};
 fan.querySelectorAll('[data-fan-member]').forEach(b=>b.onclick=e=>{e.stopPropagation();if(b.dataset.fanMember===top)return detail(fanMembers.find(i=>i.content_hash===top).id);act(b,'stack',{stack:id,contents,top:b.dataset.fanMember},'Top chosen')});
 fan.querySelector('[data-fan-separate]').onclick=e=>{e.stopPropagation();act(e.currentTarget,'unstack',{stack:id},'Kept separate')};
 fan.querySelector('[data-fan-open]').onclick=e=>{e.stopPropagation();openFan='';fanMembers=[];state.view='photos';state.stack=id;state.collectionName='';state.filter='all';load()};
 fan.querySelector('[data-fan-close]').onclick=e=>{e.stopPropagation();openFan='';fanMembers=[];renderTiles()};
}

const FILTER_NAMES={photo:'Photos only',video:'Videos only',favorites:'Favorites',hidden:'Hidden photos',duplicates:'Exact duplicates',stacks:'Similar stacks',unknown:'Missing location',undated:'Undated',date_review:'Dates needing review'};
function renderTokens(){
 const box=$('tokens');if(!box)return;const tokens=[];
 if(state.person)tokens.push({k:'Person',v:state.person==='untagged'?'Untagged':state.collectionName||'Person',clear:()=>{state.person='';state.collectionName=''}});
 if(state.place)tokens.push({k:'Place',v:state.collectionName&&!state.person?state.collectionName:state.place,clear:()=>{state.place='';if(!state.trip)state.collectionName=''}});
 if(state.trip)tokens.push({k:'Trip',v:state.collectionName||'Trip',clear:()=>{state.trip='';state.collectionName='';state.after='';state.before='';$('after').value='';$('before').value=''}});
 if(state.stack)tokens.push({k:'Stack',v:'One stack',clear:()=>{state.stack=''}});
 if(state.year)tokens.push({k:'Year',v:state.year,clear:()=>{state.year='';$('year').value=''}});
 if(state.after||state.before)tokens.push({k:'Dates',v:(state.after||'…')+' – '+(state.before||'…'),clear:()=>{state.after='';state.before='';$('after').value='';$('before').value=''}});
 if(state.filter&&state.filter!=='all')tokens.push({k:'Show',v:FILTER_NAMES[state.filter]||state.filter,clear:()=>{state.filter='all'}});
 if(state.mode!=='everything'&&state.query.trim())tokens.push({k:'Search in',v:$('searchMode').selectedOptions[0]?.textContent||state.mode,clear:()=>{state.mode='everything';$('searchMode').value='everything'}});
 box.hidden=state.view!=='photos'||!tokens.length;
 box.innerHTML=tokens.map((t,i)=>`<span class="token"><b>${t.k}</b> ${escape(String(t.v))}<button data-token="${i}" data-testid="nav.search.token-remove.${i}" aria-label="Remove ${t.k} filter">×</button></span>`).join('')+(tokens.length>1?'<button class="chip" data-clear-tokens data-testid="nav.search.clear-tokens">Clear all</button>':'');
 box.querySelectorAll('[data-token]').forEach(b=>b.onclick=()=>{tokens[+b.dataset.token].clear();load()});
 const all=box.querySelector('[data-clear-tokens]');if(all)all.onclick=()=>{tokens.forEach(t=>t.clear());load()};
}

let suggestTimer=null,suggestTicket=0,suggestRows=[],suggestIndex=-1;
function closeSuggest(){$('suggest').hidden=true;$('query').setAttribute('aria-expanded','false');suggestRows=[];suggestIndex=-1}
async function showSuggest(){
 const q=$('query').value.trim();const ticket=++suggestTicket;
 if(state.mode!=='everything'&&state.mode!=='metadata'){closeSuggest();return}
 try{const data=await api('/api/suggest?q='+encodeURIComponent(q)+'&limit=6');if(ticket!==suggestTicket||document.activeElement!==$('query'))return;
  const rows=[];const group=(title,items,render)=>{if(!items.length)return;rows.push({head:title});items.forEach(i=>rows.push({...i,html:render(i)}))};
  group('People',data.people,p=>`${p.face?`<img src="/face-preview/${p.face}" alt="">`:'<span class="sk">👤</span>'}<span>${escape(p.name)}${p.hidden?' <span class="sub">hidden</span>':''}</span><small>${number(p.count)} photos</small>`);
  group('Places',data.places,p=>`${p.cover?`<img src="/preview/${p.cover}" alt="">`:'<span class="sk">⌖</span>'}<span>${escape(p.name)}${p.region?` <span class="sub">${escape(p.region)}</span>`:''}</span><small>${number(p.count)}</small>`);
  group('Trips',data.trips,t=>`${t.cover?`<img src="/preview/${t.cover}" alt="">`:'<span class="sk">✈</span>'}<span>${escape(t.name)} <span class="sub">${escape(t.after)} – ${escape(t.before)}</span></span><small>${number(t.count)}</small>`);
  group('Albums',data.albums,a=>`<span class="sk">▤</span><span>${escape(a.name)}</span><small>${number(a.count)}</small>`);
  group('Buckets',data.buckets||[],b=>`${b.cover?`<img src="/preview/${b.cover}" alt="">`:'<span class="sk">▣</span>'}<span>${escape(b.name)} <span class="sub">bucket</span></span><small>${number(b.count)}</small>`);
  group('Dates',data.dates,d=>`<span class="sk">📅</span><span>${escape(d.kind==='year'?d.value:d.kind==='month'?dateLabel(d.value+'-01').replace(/^\S+ \d+, /,'').replace(/\d+, /,''):dateLabel(d.value))} <span class="sub">jump in the timeline</span></span><small>${number(d.count)}</small>`);
  group('Show only',data.filters,f=>`<span class="sk">◐</span><span>${escape(f.name)}</span>`);
  group('AI sees (may be wrong)',data.tags,t=>`<span class="sk">✦</span><span>${escape(t.name)}</span><small>${number(t.count)} photos</small>`);
  if(q)rows.push({kind:'search',html:`<span class="sk">⌕</span><span>Search everything for “${escape(q)}”</span><small>Enter</small>`});
  suggestRows=rows.filter(r=>!r.head);suggestIndex=-1;
  $('suggest').innerHTML=rows.length?rows.map(r=>r.head?`<div class="sg">${escape(r.head)}</div>`:`<button class="si ${r.kind}" data-si="${suggestRows.indexOf(r)}" data-testid="nav.suggest.item.${suggestRows.indexOf(r)}">${r.html}</button>`).join('')+`<div class="sn">${escape(data.meaning)}</div>`:'<div class="sn">Nothing matches yet. Press Enter to search file names, text in photos, descriptions and spoken words.</div>';
  $('suggest').hidden=false;$('query').setAttribute('aria-expanded','true');
  $('suggest').querySelectorAll('[data-si]').forEach(b=>b.onmousedown=e=>{e.preventDefault();pickSuggestion(suggestRows[+b.dataset.si])});
 }catch{closeSuggest()}
}
function pickSuggestion(row){
 if(!row)return;closeSuggest();
 const keep=()=>{state.view='photos';state.selecting=false;selection.clear()};
 if(row.kind==='person'){openCollection(row.id,'',row.name);return}
 if(row.kind==='place'){openCollection('',row.id,row.name);return}
 if(row.kind==='trip'){keep();state.person='';state.place=row.place||'';state.trip=row.saved?row.id:'';state.collectionName=row.name;state.query='';state.filter='all';state.year='';state.after=row.after;state.before=row.before;$('query').value='';$('after').value=row.after;$('before').value=row.before;load();return}
 if(row.kind==='album'){state.view='albums';state.query='';$('query').value='';load();return}
 if(row.kind==='bucket'){openBucket(row.id,row.name);return}
 if(row.kind==='date'){keep();state.query='';$('query').value='';if(row.kind==='date'&&row.value.length===4){state.year=row.value;state.after='';state.before=''}else{state.year='';const v=row.value;state.after=v.length===7?v+'-01':v;state.before=v.length===7?v+'-31':v;$('after').value=state.after;$('before').value=state.before}$('year').value=state.year;load();return}
 if(row.kind==='filter'){keep();state.filter=row.id;state.query='';$('query').value='';load();return}
 if(row.kind==='tag'){keep();state.mode='descriptions';$('searchMode').value='descriptions';state.query=row.name;$('query').value=row.name;load();return}
 if(row.kind==='search'){load();return}
}
$('query').addEventListener('focus',()=>{clearTimeout(suggestTimer);suggestTimer=setTimeout(showSuggest,80)});
$('query').addEventListener('blur',()=>setTimeout(closeSuggest,120));
$('query').addEventListener('keydown',e=>{
 if($('suggest').hidden)return;
 if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();suggestIndex=(suggestIndex+(e.key==='ArrowDown'?1:-1)+suggestRows.length)%suggestRows.length;$('suggest').querySelectorAll('.si').forEach((b,i)=>b.classList.toggle('hi',i===suggestIndex));$('suggest').querySelectorAll('.si')[suggestIndex]?.scrollIntoView({block:'nearest'})}
 else if(e.key==='Enter'&&suggestIndex>=0){e.preventDefault();e.stopImmediatePropagation();pickSuggestion(suggestRows[suggestIndex])}
 else if(e.key==='Escape'){closeSuggest()}
},true);

let leafletMap=null,tripMap=null;
function ensureLeaflet(){
 if(window.L)return Promise.resolve();
 return new Promise((resolve,reject)=>{const css=document.createElement('link');css.rel='stylesheet';css.href='/vendor/leaflet/leaflet.css';document.head.append(css);const js=document.createElement('script');js.src='/vendor/leaflet/leaflet.js';js.onload=resolve;js.onerror=()=>reject(new Error('The map library did not load.'));document.head.append(js)});
}
function ensureBasemap(){return ensureLeaflet().then(()=>window.VaultBasemap?null:new Promise((resolve,reject)=>{const js=document.createElement('script');js.src='/basemap.js';js.onload=resolve;js.onerror=()=>reject(new Error('The offline map did not load.'));document.head.append(js)})).then(()=>window.VaultBasemap.load())}
function makeMap(element,bounds,maxZoom,view){const L=window.L;const box=typeof element==='string'?document.getElementById(element):element;const map=L.map(box,{zoomControl:true,attributionControl:false,preferCanvas:true,fadeAnimation:false,worldCopyJump:true,minZoom:2,maxZoom:18,wheelPxPerZoomLevel:220,wheelDebounceTime:60});const mapKind=box.id==='tripMap'?'trip':'main';box.setAttribute('data-testid',`map.canvas.${mapKind}`);box.querySelectorAll('.leaflet-control-zoom a').forEach(a=>a.setAttribute('data-testid',`map.control.${a.classList.contains('leaflet-control-zoom-in')?'zoom-in':'zoom-out'}.${mapKind}`));map.on('popupopen',e=>{const c=e.popup.getElement()?.querySelector('.leaflet-popup-close-button');if(c)c.setAttribute('data-testid',`map.popup.close.${mapKind}`)});
 let fitted=false;const fit=()=>{map.invalidateSize({animate:false});if(view)map.setView(view.center,view.zoom,{animate:false});else if(bounds&&bounds.length)map.fitBounds(bounds,{padding:[30,30],maxZoom,animate:false});else map.setView([20,0],2);const size=map.getSize();fitted=size.x>0&&size.y>0};
 fit();if(!fitted){let tries=0;const retry=()=>{if(!box.isConnected||fitted)return;fit();if(!fitted&&tries++<20)setTimeout(retry,50)};setTimeout(retry,30)}
 if(window.ResizeObserver){const ro=new ResizeObserver(()=>{if(!box.isConnected)return;if(fitted)map.invalidateSize({animate:false});else fit()});ro.observe(box);map.on('unload',()=>ro.disconnect())}
 window.__vaultMap=map;window.VaultBasemap.layer().addTo(map);window.VaultBasemap.labels(map);L.control.attribution({prefix:false}).addAttribution('Natural Earth').addTo(map);return map}
function clusterPlaces(map,places){const z=map.getZoom();const pts=places.map(p=>({p,pt:map.project([p.lat,p.lon],z)})).sort((a,b)=>b.p.count-a.p.count);const groups=[];for(const item of pts){const g=groups.find(g=>g.pt.distanceTo(item.pt)<44);if(g){g.members.push(item.p);g.count+=item.p.count}else groups.push({pt:item.pt,members:[item.p],count:item.p.count})}return groups.map(g=>{let lat=0,lon=0;for(const m of g.members){lat+=m.lat*m.count;lon+=m.lon*m.count}return {...g,lat:lat/g.count,lon:lon/g.count,name:g.members[0].name||g.members[0].id}})}
function mapNote(){return '<div class="map-note"><span>Offline map from Natural Earth, stored with the app. Nothing about your photos leaves this machine.</span></div>'}
async function renderMap(ticket){
 $('title').textContent='Map';$('subtitle').textContent='Where your photos were taken, from recorded coordinates. Dots group nearby places; zoom in to split them.';
 try{const [placesData,tripsData]=await Promise.all([api('/api/places'),api('/api/trips')]);if(ticket!==requestId||state.view!=='map')return;
  const trips=[...tripsData.trips.map(t=>({...t,saved:true})),...tripsData.proposals.map(t=>({...t,saved:false}))];
  const tripsByPlace={};trips.forEach(t=>{if(t.place)(tripsByPlace[t.place]=tripsByPlace[t.place]||[]).push(t)});
  const merged={};placesData.places.forEach(p=>{const key=p.geographic?.id?'g:'+p.geographic.id:p.id;const m=merged[key]=merged[key]||{...p,ids:[],count:0,lat:0,lon:0,cells:0,trips:[]};m.ids.push(p.id);m.lat+=p.lat*p.count;m.lon+=p.lon*p.count;m.count+=p.count;m.cells++;m.trips.push(...(tripsByPlace[p.id]||[]));if(!m.name&&p.name)m.name=p.name;if(p.first&&(!m.first||p.first<m.first))m.first=p.first;if(p.last&&(!m.last||p.last>m.last))m.last=p.last});
  const places=Object.values(merged).map(m=>({...m,id:m.ids.join(','),lat:m.lat/m.count,lon:m.lon/m.count})).sort((a,b)=>b.count-a.count);places.forEach(p=>{tripsByPlace[p.id]=p.trips});
  const byRegion={};places.forEach(p=>{const g=p.geographic||{};const key=[g.country,g.region].filter(Boolean).join(' · ')||'Unnamed area';(byRegion[key]=byRegion[key]||[]).push(p)});
  const list=Object.entries(byRegion).sort((a,b)=>b[1].reduce((n,p)=>n+p.count,0)-a[1].reduce((n,p)=>n+p.count,0)).map(([region,rows])=>`<h3>${escape(region)}</h3>`+rows.map(p=>`<button class="place-row" data-map-place="${p.id}" data-testid="map.list.place.${p.id}">${p.cover?`<img loading="lazy" src="/preview/${p.cover}" alt="">`:''}<span>${escape(p.name||p.id)}${p.cells>1?` <span class="sub muted">· ${p.cells} areas</span>`:''}${(()=>{const saved=(tripsByPlace[p.id]||[]).filter(t=>t.saved).length,proposed=(tripsByPlace[p.id]||[]).length-saved;return saved?` <span class="sub muted">· ${saved} ${saved===1?'trip':'trips'}</span>`:proposed?` <span class="sub muted">· ${proposed} proposed</span>`:''})()}</span><small>${number(p.count)}</small></button>`).join('')).join('');
  $('content').innerHTML=`<div class="map-layout"><div class="map-canvas" id="mapCanvas"></div><div class="map-list">${places.length?list:'<p class="local-note">No recorded coordinates yet.</p>'}<p class="local-note" style="margin:14px 8px">${escape(placesData.meaning)} ${number(placesData.missing)} files have no location.</p></div></div>`;
  $('content').querySelectorAll('[data-map-place]').forEach(b=>b.onclick=()=>{const p=places.find(p=>p.id===b.dataset.mapPlace);openCollection('',p.id,p.name||p.id)});
  const canvas=$('mapCanvas');if(leafletMap){try{leafletMap.remove()}catch(e){}leafletMap=null}
  if(!places.length){canvas.innerHTML='<div class="empty"><h2>No coordinates yet</h2></div>';return}
  canvas.innerHTML='<div id="leaflet" style="position:absolute;inset:0"></div>'+mapNote();
  try{await ensureBasemap()}catch(e){canvas.innerHTML=`<div class="empty">${escape(e.message)}</div>`;return}
  if(ticket!==requestId||!$('leaflet'))return;
  leafletMap=makeMap('leaflet',places.map(p=>[p.lat,p.lon]),12,pendingMapView);pendingMapView=null;const L=window.L;const dots=L.layerGroup().addTo(leafletMap);
  const drawDots=()=>{dots.clearLayers();for(const g of clusterPlaces(leafletMap,places)){const r=Math.max(7,Math.min(28,6+Math.sqrt(g.count)*1.4));const m=L.circleMarker([g.lat,g.lon],{radius:r,color:'#7fd3a0',weight:1.5,fillColor:'#2f8f5b',fillOpacity:.6}).addTo(dots);
    if(g.members.length>1||g.count>=10)L.marker([g.lat,g.lon],{interactive:false,keyboard:false,icon:L.divIcon({className:'map-count',html:`<span>${g.count>=1000?Math.round(g.count/1000)+'k':g.count}</span>`,iconSize:[0,0]})}).addTo(dots);
    const ids=g.members.flatMap(x=>x.id.split(','));const tr=g.members.flatMap(x=>tripsByPlace[x.id]||[]);const label=g.members.length===1?g.name:`${g.members.length} places`;
    m.bindPopup(`<strong>${escape(label)}</strong><br>${number(g.count)} files${g.first?` · ${escape(g.members[0].first||'')} – ${escape(g.members[0].last||'')}`:''}${g.members.length>1?'<div class="popup-list">'+g.members.slice(0,6).map(x=>`<button class="chip" data-popup-place="${x.id}" data-testid="map.popup.place.${x.id}" data-popup-name="${escape(x.name||x.id)}">${escape(x.name||x.id)} · ${number(x.count)}</button>`).join('')+'</div>':''}${tr.length?`<div class="muted">${tr.slice(0,4).map(x=>escape(x.name)).join('<br>')}</div>`:''}<div class="popup-list"><button class="chip" data-popup-place="${ids.join(',')}" data-testid="map.popup.all" data-popup-name="${escape(label)}">Browse${g.members.length>1?' all':''}</button>${g.members.length>1?'<button class="chip" data-popup-zoom="1" data-testid="map.popup.zoom">Zoom in</button>':''}</div>`,{autoPanPaddingTopLeft:[70,30],autoPanPaddingBottomRight:[30,70]});
    m.on('popupopen',e=>{const el=e.popup.getElement();el.querySelectorAll('[data-popup-place]').forEach(b=>b.onclick=()=>openCollection('',b.dataset.popupPlace,b.dataset.popupName));const zb=el.querySelector('[data-popup-zoom]');if(zb)zb.onclick=()=>leafletMap.fitBounds(g.members.map(x=>[x.lat,x.lon]),{padding:[40,40],maxZoom:14})})}};
  leafletMap.on('zoomend',drawDots);drawDots();
 }catch(e){$('content').innerHTML=`<div class="empty">The map could not be loaded. ${escape(e.message||'')}</div>`}
}
function openTrip(t,data){state.view='photos';state.collectionName=t.name;state.person='';state.place=t.place;state.trip=data.trips.some(x=>x.id===t.id)?t.id:'';state.query='';state.filter='all';state.year='';state.after=t.after;state.before=t.before;state.selecting=false;selection.clear();$('query').value='';$('year').value='';$('after').value=t.after;$('before').value=t.before;load()}
async function renderTripMap(data,all,ticket){
 const box=$('tripMap');if(!box)return;
 try{const paths=(await api('/api/trip-paths')).paths;await ensureBasemap();if(ticket!==requestId||!$('tripMap'))return;
  const rows=all.filter(t=>paths[t.id]&&paths[t.id].points.length);if(!rows.length){box.innerHTML='<div class="empty"><h2>No trip has recorded coordinates yet</h2></div>';return}
  box.innerHTML='<div id="tripLeaflet" style="position:absolute;inset:0"></div><div class="map-note"><span>Saved trips in green, suggested visits in grey. A dot marks where you set out, the line follows your photos in time order, dashed legs join the nearest located photos before and after, and the arrow marks the end. Click a trip for details.</span></div>';
  if(tripMap){try{tripMap.remove()}catch(e){}tripMap=null}const bounds=rows.flatMap(t=>[...paths[t.id].points,...[paths[t.id].from,paths[t.id].back].filter(Boolean)]);tripMap=makeMap('tripLeaflet',bounds,11,pendingTripMapView);pendingTripMapView=null;window.__vaultTripMap=tripMap;const L=window.L;const saved=new Set(data.trips.map(t=>t.id));
  rows.sort((a,b)=>(saved.has(a.id)?1:0)-(saved.has(b.id)?1:0));
  for(const t of rows){const path=paths[t.id];const pts=path.points;const isSaved=saved.has(t.id);const color=isSaved?'#7fd3a0':'#8a938e';let layer;
   // The journey: a dashed leg from the last located photo before the trip, the trip itself, then a dashed leg to the first located photo after it.
   const legs={color,weight:isSaved?2:1.2,opacity:isSaved?.8:.5,dashArray:'6 6',interactive:false};const seq=[...(path.from?[path.from]:[]),...pts,...(path.back?[path.back]:[])];
   if(path.from)L.polyline([path.from,pts[0]],legs).addTo(tripMap);if(path.back)L.polyline([pts[pts.length-1],path.back],legs).addTo(tripMap);
   if(pts.length>1)layer=L.polyline(pts,{color,weight:isSaved?3:1.6,opacity:isSaved?.95:.6,lineJoin:'round'}).addTo(tripMap);
   else layer=L.circleMarker(pts[0],{radius:isSaved?7:5,color,weight:1.5,fillColor:color,fillOpacity:.6}).addTo(tripMap);
   if(seq.length>1){const a=seq[seq.length-2],b=seq[seq.length-1];const p1=tripMap.project(a,12),p2=tripMap.project(b,12);const angle=Math.atan2(p2.y-p1.y,p2.x-p1.x)*180/Math.PI;L.marker(b,{interactive:false,keyboard:false,icon:L.divIcon({className:'trip-arrow'+(isSaved?' saved':''),html:`<span style="transform:rotate(${angle}deg)">➤</span>`,iconSize:[0,0]})}).addTo(tripMap);L.circleMarker(seq[0],{radius:4,color,weight:2,fillColor:color,fillOpacity:1,interactive:false}).addTo(tripMap)}
   const journey=[path.from?'set out from elsewhere':'',path.back?'returned elsewhere after':''].filter(Boolean).join(', ');
   layer.bindPopup(`<strong>${escape(t.name)}</strong><br>${escape(t.after)} – ${escape(t.before)} · ${number(t.count)} files · ${number(path.located)} with coordinates${journey?`<br><span class="muted">${journey}</span>`:''}<div class="popup-list"><button class="chip" data-trip-open="${t.id}" data-testid="trips.popup.open.${t.id}">Browse</button><button class="chip" data-trip-edit="${t.id}" data-testid="trips.popup.edit.${t.id}">${isSaved?'Edit trip':'Save as trip'}</button>${isSaved?'':tripInto(t,data)}</div>`,{autoPanPaddingTopLeft:[70,30],autoPanPaddingBottomRight:[30,70]});
   layer.on('popupopen',e=>{const el=e.popup.getElement();el.querySelector('[data-trip-open]').onclick=()=>openTrip(t,data);el.querySelector('[data-trip-edit]').onclick=()=>editTrip(t,!isSaved);const into=el.querySelector('[data-trip-into]');if(into)into.onchange=()=>mergeIntoTrip(t,data.trips.find(x=>x.id===into.value))})}
 }catch(e){box.innerHTML=`<div class="empty">${escape(e.message||'Trip map unavailable')}</div>`}
}
// Fold a suggested visit into a saved trip: the dates stretch to cover both and the recorded areas are joined, so the suggestion stops being offered.
function tripInto(t,data){if(!data.trips.length)return '';const near=[...data.trips].sort((a,b)=>Math.abs(Date.parse(a.after)-Date.parse(t.after))-Math.abs(Date.parse(b.after)-Date.parse(t.after)));return `<select class="trip-into" data-trip-into="${t.id}" data-testid="trips.card.into.${t.id}" aria-label="Add ${escape(t.name)} to a saved trip"><option value="">Add to trip…</option>${near.map(s=>`<option value="${s.id}">${escape(s.name)}</option>`).join('')}</select>`}
async function mergeIntoTrip(t,saved){if(!t||!saved)return;const place=saved.place?[...new Set([...saved.place.split(','),...(t.place?t.place.split(','):[])])].join(','):'';
 try{await saveOrganization('trip',{trip:saved.id,name:saved.name,after:t.after<saved.after?t.after:saved.after,before:t.before>saved.before?t.before:saved.before,place});await load();notify(`Added to ${saved.name}`)}catch(e){notify(e.message)}}
async function renderTrips(ticket){
 $('title').textContent=archivedTrips?'Archived trips':'Trips';$('subtitle').textContent=archivedTrips?'Restore a trip with its dates and review decisions intact.':'Your trips and suggested visits from recorded places and dates.';
 try{const data=await api('/api/trips'+(archivedTrips?'?archived=1':''));if(ticket!==requestId)return;tripCache=data.trips;
 const cards=(rows,suggested)=>rows.map(t=>`<article class="place-card">${t.cover?`<img loading="lazy" src="/preview/${t.cover}" alt="">`:''}<strong>${escape(t.name)}</strong><small>${escape(t.after)} – ${escape(t.before)} · ${number(t.count)} files</small><div class="card-actions"><button class="chip" data-trip-open="${t.id}" data-testid="trips.card.open.${t.id}">Browse</button><button class="outline" data-trip-edit="${t.id}" data-testid="trips.card.edit.${t.id}" data-suggested="${suggested}">${suggested?'Save as trip':'Edit trip'}</button>${suggested?tripInto(t,data):`<button class="outline" data-trip-archive="${t.id}" data-testid="trips.card.archive.${t.id}">${archivedTrips?'Restore':'Archive'}</button>`}</div></article>`).join('');
 $('content').innerHTML=(archivedTrips?'':'<div class="map-canvas trip-map" id="tripMap"><div class="empty">Loading map…</div></div>')+'<div class="collection-actions"><button class="primary" id="newTrip" data-testid="trips.toolbar.new">New trip</button><button class="outline" id="toggleArchivedTrips" data-testid="trips.toolbar.toggle-archived">'+(archivedTrips?'Back to trips':'Archived trips')+'</button></div><div class="places">'+cards(data.trips,false)+'</div>'+(archivedTrips?'':'<div class="group-head"><h2>Suggested visits</h2><span>'+number(data.proposals.length)+' suggestions</span></div><p class="local-note">'+escape(data.meaning)+'</p><div class="places">'+cards(data.proposals.slice(0,tripProposalLimit),true)+'</div>'+(data.proposals.length>tripProposalLimit?'<button class="outline" id="moreTrips" data-testid="trips.list.more" style="margin:20px 0">More suggestions</button>':''));
 $('newTrip').onclick=()=>editTrip();$('toggleArchivedTrips').onclick=()=>{archivedTrips=!archivedTrips;load()};
 $('content').querySelectorAll('[data-trip-archive]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await saveOrganization(archivedTrips?'restore_trip':'archive_trip',{trip:b.dataset.tripArchive});await load()}catch(e){notify(e.message);b.disabled=false}});if($('moreTrips'))$('moreTrips').onclick=()=>{tripProposalLimit+=60;renderTrips(requestId)};
 const all=[...data.trips,...data.proposals];
 $('content').querySelectorAll('[data-trip-open]').forEach(b=>b.onclick=()=>openTrip(all.find(t=>t.id===b.dataset.tripOpen),data));
 $('content').querySelectorAll('[data-trip-edit]').forEach(b=>b.onclick=()=>editTrip(all.find(t=>t.id===b.dataset.tripEdit),b.dataset.suggested==='true'));
 $('content').querySelectorAll('[data-trip-into]').forEach(s=>s.onchange=()=>mergeIntoTrip(all.find(t=>t.id===s.dataset.tripInto),data.trips.find(x=>x.id===s.value)));
 if(!archivedTrips)renderTripMap(data,all,ticket);
 }catch{$('content').innerHTML='<div class="empty">Trips could not be loaded. Try again.</div>'}
}
async function editTrip(trip=null,suggested=false){
 try{const places=(await api('/api/places')).places;tripEditing=trip&&!suggested?trip.id:'';$('tripHeading').textContent=tripEditing?'Edit trip':'Save a trip';$('tripName').value=trip?.name||'';$('tripAfter').value=trip?.after||'';$('tripBefore').value=trip?.before||'';$('tripPlace').replaceChildren(new Option('All places',''),...places.map(p=>new Option(p.name||'Recorded area '+p.id,p.id)));if(trip?.place?.includes(','))$('tripPlace').add(new Option(trip.place.split(',').map(k=>places.find(p=>p.id===k)?.name||'Recorded area '+k).join(' + ')+' (keep all)',trip.place),1);$('tripPlace').value=trip?.place||'';$('tripError').textContent='';$('tripDialog').showModal();$('tripName').focus()}catch{notify('Could not open the trip editor.')}
}
$('closeTrip').onclick=()=>$('tripDialog').close();
$('saveTrip').onclick=async()=>{const b=$('saveTrip');b.disabled=true;try{await saveOrganization('trip',{trip:tripEditing,name:$('tripName').value,after:$('tripAfter').value,before:$('tripBefore').value,place:$('tripPlace').value});$('tripDialog').close();await load();notify('Trip saved')}catch(e){$('tripError').textContent=e.message}finally{b.disabled=false}};
async function renderPlaces(ticket){
 $('title').textContent='Places';$('subtitle').textContent='Explore recorded locations and give familiar places a name.';
 try{const data=await api('/api/places');if(ticket!==requestId)return;placeCache=data.places;
 $('content').innerHTML='<p class="local-note">'+escape(data.meaning)+' '+number(data.missing)+' files have no recorded location. '+escape(data.attribution||'')+'</p><div class="places">'+placeCache.map((p,i)=>`<button class="place-card" data-place="${escape(p.id)}" data-testid="places.card.open.${escape(p.id)}"><img loading="lazy" src="/preview/${p.cover}" alt=""><strong>${escape(p.name||'Unnamed area '+(i+1))}</strong><small>${number(p.count)} files · ${p.first?escape(p.first.slice(0,4))+(p.last?.slice(0,4)!==p.first.slice(0,4)?'–'+escape(p.last.slice(0,4)):''):'Date unknown'}<br>${p.geographic?escape(p.geographic.region+', '+p.geographic.country)+'<br>'+number(p.geographic.distance_km)+' km from nearest settlement':p.lat.toFixed(2)+'°, '+p.lon.toFixed(2)+'°'}</small></button>`).join('')+'</div>';
 $('content').querySelectorAll('[data-place]').forEach(b=>b.onclick=()=>{const p=placeCache.find(p=>p.id===b.dataset.place);openCollection('',p.id,p.name||'Unnamed place')});
 }catch{$('content').innerHTML='<div class="empty">Places could not be loaded. Try again.</div>'}
}
let mergingPerson='';
// Owner actions never bounce the owner out of what they are looking at (docs/taste.md, 2026-09-05).
// With the viewer open, refresh the item in place and let the grid catch up when the viewer closes.
async function refreshAfterSave(){
 if($('inspector').open&&items[selected]){const item=items[selected];try{const fresh=await api('/api/item/'+item.id);if(items[selected]===item)Object.assign(item,fresh)}catch{}if(items[selected]===item){detailTags();renderDetailBuckets(item)}reviewChanged=true;return}
 await load();
}
async function openPerson(contents,onlyCreate=false,rename='',faces=[],mergeFrom=''){
 try{const data=await api('/api/people');peopleCache=data.people;pendingContents=contents;pendingFaces=faces;createOnly=onlyCreate;editingPerson=rename;mergingPerson=mergeFrom;
 $('personDialogTitle').textContent=mergeFrom?'Merge this person into…':faces.length?`Name ${faces.length} selected ${faces.length===1?'face':'faces'}`:rename?'Rename person':onlyCreate?'Add a person':`Tag ${contents.length} ${contents.length===1?'photo':'photos'}`;
 $('personDialogHelp').textContent=mergeFrom?`Every photo and confirmed face of ${escape(peopleCache.find(p=>p.id===mergeFrom)?.name||'this person')} moves to the person you choose. The merged name is retired.`:faces.length?'Only the selected faces will be confirmed. Other faces stay unreviewed.':onlyCreate?'Create their name, then choose photos to add.':'Choose an existing person or enter a new name. This confirms they appear in each selected photo.';
 $('personChoice').innerHTML=(mergeFrom?'<option value="">Choose a person</option>':'<option value="">New person</option>')+peopleCache.filter(p=>p.id!==mergeFrom).map(p=>`<option value="${p.id}">${escape(p.name)}</option>`).join('');
 $('personChoice').value=onlyCreate||mergeFrom?'':preferredPerson;$('personChoice').parentElement.hidden=onlyCreate||!!rename;$('personName').parentElement.hidden=!!mergeFrom||(!rename&&!onlyCreate&&!!$('personChoice').value);$('personName').value=rename?peopleCache.find(p=>p.id===rename).name:'';$('personError').textContent='';$('personDialog').showModal();$('personName').focus();
 }catch{notify('Could not load people. Try again.')}
}
function detailTags(){
 const item=items[selected];$('detailPeople').hidden=!summary?.capabilities.organization||!['photo','video'].includes(item.kind)||!item.content_hash;
 $('personTags').className='person-tags';$('personTags').innerHTML=(item.people||[]).map(p=>`<button data-remove-person="${p.id}" data-testid="inspector.people.remove.${p.id}" title="Remove this person tag">${escape(p.name)} ×</button>`).join('');
 $('personTags').querySelectorAll('button').forEach(b=>b.onclick=async()=>{try{await saveOrganization('remove',{person:b.dataset.removePerson,contents:[item.content_hash]});item.people=item.people.filter(p=>p.id!==b.dataset.removePerson);detailTags();notify('Person tag removed')}catch(e){notify(e.message)}});
 loadPhotoFaces(item);renderDetailStack(item);renderDetailFlags(item);
}
async function loadPhotoFaces(item){
 const box=$('photoFaces');box.innerHTML='';if($('detailPeople').hidden||!summary?.capabilities.faces)return;
 try{const data=await api('/api/photo-faces/'+item.id);if(items[selected]!==item)return;
  if(!data.faces.length){box.innerHTML='<p class="muted">No faces detected in this file.</p>';return}
  box.innerHTML=data.faces.map(f=>`<div class="photo-face ${f.person?'named':f.ignored?'ignored':''}"><img src="/face-preview/${f.face_id}" alt="">${f.timestamp!=null?`<small class="face-time" title="Where in the video this face was seen">at ${Math.floor(f.timestamp/60)}:${String(Math.floor(f.timestamp%60)).padStart(2,'0')}</small>`:''}<span>${f.person?escape(f.person.name):f.ignored?'Not a face':'Unnamed'}</span><span class="photo-face-actions">${f.person?`${f.cover?'<span title="Cover face">★</span>':`<button data-cover="${f.face_id}" data-testid="inspector.face.cover.${f.face_id}" data-person="${f.person.id}" title="Use as this person's cover">☆</button>`}<button data-unname="${f.person.id}" data-testid="inspector.face.unname.${f.face_id}" title="Remove ${escape(f.person.name)} from this photo">×</button>`:f.ignored?`<button data-restore="${f.face_id}" data-testid="inspector.face.restore.${f.face_id}" title="Review this face again">Restore</button>`:`<button data-name="${f.face_id}" data-testid="inspector.face.name.${f.face_id}">Name</button><button data-ignore="${f.face_id}" data-testid="inspector.face.ignore.${f.face_id}" title="Not a face">×</button>`}</span></div>`).join('');
  const act=async(op,data,message)=>{try{await saveOrganization(op,data);notify(message);item.people=(await api('/api/photo-faces/'+item.id)).faces.filter(f=>f.person).map(f=>f.person).filter((p,i,a)=>a.findIndex(x=>x.id===p.id)===i);detailTags()}catch(e){notify(e.message)}};
  box.querySelectorAll('[data-name]').forEach(b=>b.onclick=()=>openPerson([],false,'',[{face_id:b.dataset.name,content_hash:item.content_hash}]));
  box.querySelectorAll('[data-ignore]').forEach(b=>b.onclick=()=>act('ignore_faces',{faces:[b.dataset.ignore]},'Face ignored'));
  box.querySelectorAll('[data-restore]').forEach(b=>b.onclick=()=>act('restore_faces',{faces:[b.dataset.restore]},'Face restored for review'));
  box.querySelectorAll('[data-cover]').forEach(b=>b.onclick=()=>act('cover_face',{person:b.dataset.person,face_id:b.dataset.cover},'Cover face set'));
  box.querySelectorAll('[data-unname]').forEach(b=>b.onclick=()=>act('remove',{person:b.dataset.unname,contents:[item.content_hash]},'Person removed from this photo'));
 }catch(e){box.innerHTML=`<p class="muted">${escape(e.message)}</p>`}
}
$('personChoice').onchange=()=>$('personName').parentElement.hidden=!!$('personChoice').value;
$('closePerson').onclick=()=>$('personDialog').close();
$('savePerson').onclick=async()=>{const b=$('savePerson');b.disabled=true;$('personError').textContent='';try{
 let person=createOnly?'':$('personChoice').value;
 if(mergingPerson){if(!person)throw new Error('Choose the person to keep.');await saveOrganization('merge_person',{person:mergingPerson,into:person});$('personDialog').close();peopleCache=(await api('/api/people')).people;const kept=peopleCache.find(p=>p.id===person);notify('People merged');openCollection(person,'',kept?kept.name:'');return}
 if(editingPerson){const result=await saveOrganization('person',{person:editingPerson,name:$('personName').value});state.collectionName=result.data.name;$('personDialog').close();await load();notify('Person renamed');return}
 if(!person){const name=$('personName').value.trim();if(!name)throw new Error('Enter a name.');const result=await saveOrganization('person',{person:'',name});person=result.data.person;const option=document.createElement('option');option.value=person;option.textContent=name;$('personChoice').append(option);$('personChoice').value=person}
 if(pendingFaces.length)await saveOrganization('faces',{person,faces:pendingFaces});
 if(pendingContents.length)await saveOrganization('add',{person,contents:pendingContents});
 $('personDialog').close();if($('faceDialog').open)$('faceDialog').close();selection.clear();notify('Saved');
 preferredPerson=person;if(createOnly){const p=(await api('/api/people')).people.find(p=>p.id===person);openCollection('','','Choose photos for '+p.name);state.selecting=true;selectionTools();$('personChoice').value=person}
 else await refreshAfterSave();
 }catch(e){$('personError').textContent=e.message}finally{b.disabled=false}};
$('renamePerson').onclick=()=>openPerson([],false,state.person);
$('hidePerson').onclick=async()=>{try{let me=peopleCache.find(p=>p.id===state.person);if(!me){peopleCache=(await api('/api/people')).people;me=peopleCache.find(p=>p.id===state.person)}await saveOrganization(me?.hidden?'show_person':'hide_person',{person:state.person});peopleCache=(await api('/api/people')).people;selectionTools();notify(me?.hidden?'Shown in suggestions again':'Hidden from suggestions. Their photos and name stay.')}catch(e){notify(e.message)}};
$('mergePerson').onclick=()=>openPerson([],false,'',[],state.person);
$('tagPhoto').onclick=()=>openPerson([items[selected].content_hash]);
$('selectPhotos').onclick=()=>{$('drawer').hidden=true;state.selecting=!state.selecting;if(!state.selecting)selection.clear();renderTiles()};$('doneSelecting').onclick=()=>{state.selecting=false;selection.clear();renderTiles()};
$('selectPage').onclick=()=>{for(const p of items)if((state.trip||['photo','video'].includes(p.kind))&&p.content_hash&&selection.size<200)selection.add(p.content_hash);renderTiles()};
$('tagSelection').onclick=()=>openPerson([...selection]);
$('setDateSelection').onclick=()=>{$('bulkDateError').textContent='';$('bulkDateNote').textContent=`${selection.size} selected. Sets the day for every selected item at noon; source dates are retained and each item can be reset from its details.`;$('dateDialog').showModal()};
$('closeDateDialog').onclick=()=>$('dateDialog').close();
$('saveBulkDate').onclick=async()=>{const date=$('bulkDate').value;if(!date){$('bulkDateError').textContent='Choose a date';return}$('saveBulkDate').disabled=true;try{const contents=[...selection];for(let i=0;i<contents.length;i+=200)await saveOrganization('set_date',{contents:contents.slice(i,i+200),date});$('dateDialog').close();selection.clear();summary=await api('/api/summary');await load();notify('Date set')}catch(e){$('bulkDateError').textContent=e.message}finally{$('saveBulkDate').disabled=false}};
$('favoriteSelection').onclick=async()=>{try{await saveOrganization('favorite',{contents:[...selection]});selection.clear();await load();notify('Added to favorites')}catch(e){notify(e.message)}};
$('hideSelection').onclick=async()=>{try{await saveOrganization(state.filter==='hidden'?'unhide':'hide',{contents:[...selection]});selection.clear();await load();notify(state.filter==='hidden'?'Back in the timeline':'Hidden from the timeline')}catch(e){notify(e.message)}};
$('removeSelection').onclick=async()=>{try{await saveOrganization('remove',{person:state.person,contents:[...selection]});selection.clear();await load();notify('Person tags removed')}catch(e){notify(e.message)}};

$('excludeTripItems').onclick=async()=>{const b=$('excludeTripItems');b.disabled=true;try{await saveOrganization('exclude_trip',{trip:state.trip,contents:[...selection]});selection.clear();await load();notify('Removed from this trip. Restore them in Removed items.')}catch(e){notify(e.message)}finally{b.disabled=false}};
async function reviewTripExclusions(){
 try{const d=await api('/api/trip-exclusions/'+state.trip);$('tripExclusionsNote').textContent=`${d.total} removed items. ${d.missing} currently unavailable. Showing up to 200; restoring items reveals more. Restored items reappear when they match the trip dates and place.`;
 $('tripExclusionsChoices').innerHTML=d.items.map((p,i)=>`<article><img loading="lazy" src="/preview/${p.id}" alt="Removed item ${i+1}" style="width:100%;height:120px;object-fit:cover;border-radius:6px"><button class="chip" data-trip-restore="${p.content_hash}" data-testid="trip-exclusions.row.restore.${p.content_hash}" aria-label="Restore item ${i+1} to trip">Restore</button></article>`).join('')||'<p>No available removed items.</p>';
 $('tripExclusionsChoices').querySelectorAll('[data-trip-restore]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await saveOrganization('restore_trip_items',{trip:state.trip,contents:[b.dataset.tripRestore]});await reviewTripExclusions();await load()}catch(e){notify(e.message);b.disabled=false}});
 if(!$('tripExclusionsDialog').open)$('tripExclusionsDialog').showModal();
 }catch(e){notify(e.message)}
}
$('reviewTripExclusions').onclick=reviewTripExclusions;
$('closeTripExclusions').onclick=()=>$('tripExclusionsDialog').close();
$('clearCollection').onclick=()=>openCollection();
$('renamePlace').onclick=()=>{$('placeName').value=placeCache.find(p=>p.id===state.place)?.name||'';$('placeError').textContent='';$('placeDialog').showModal();$('placeName').focus()};
$('closePlace').onclick=()=>$('placeDialog').close();
$('savePlace').onclick=async()=>{const b=$('savePlace');b.disabled=true;try{const result=await saveOrganization('place',{place:state.place,trip:state.trip,name:$('placeName').value});state.collectionName=result.data.name;const p=placeCache.find(p=>p.id===state.place);if(p)p.name=result.data.name;$('placeDialog').close();await load();notify('Place name saved')}catch(e){$('placeError').textContent=e.message}finally{b.disabled=false}};
let videoPoll=null,videoTicket=0,videoSeek=0;
const videoAutoTried=new Set();
function stopVideo(){videoTicket++;videoSeek=0;clearTimeout(videoPoll);$('detailVideo').pause();$('detailVideo').removeAttribute('src');$('detailVideo').load();$('detailVideo').hidden=true;$('detailPhoto').hidden=false;$('videoTools').hidden=true}
async function videoStatus(photo,start=false){
 const ticket=videoTicket;$('videoTools').hidden=false;$('prepareVideo').disabled=true;
 try{const result=await api('/api/video/'+photo.id,start?{method:'POST'}:undefined);if(ticket!==videoTicket)return;
 $('prepareVideo').hidden=result.state==='ready';$('prepareVideo').disabled=result.state==='preparing';
 if(!start&&result.state!=='ready'&&result.state!=='preparing'&&!result.error&&!videoAutoTried.has(photo.id)){videoAutoTried.add(photo.id);return videoStatus(photo,true)}
 if(result.state==='ready'){$('detailVideo').onloadedmetadata=()=>{$('detailVideo').currentTime=Math.min(videoSeek,Math.max(0,$('detailVideo').duration-.1));$('detailVideo').play().catch(()=>{})};$('detailVideo').src='/video/'+photo.id;$('detailVideo').hidden=false;$('detailPhoto').hidden=true;$('videoStatus').textContent=result.direct?'Playing the original file directly':'Local playback copy · original preserved';return}
 $('videoStatus').textContent=result.message||(result.state==='preparing'?'Preparing a local playback copy; it starts on its own when ready.':'Playback needs a local copy. Long or unsupported videos may need further processing.');
 if(result.state==='preparing')videoPoll=setTimeout(()=>videoStatus(photo),2000);
 }catch{if(ticket===videoTicket){$('videoStatus').textContent='Playback preparation is unavailable.';$('prepareVideo').disabled=false}}
}
$('inspector').addEventListener('close',stopVideo);
$('detailVideo').onerror=()=>{$('videoStatus').textContent='This browser could not play the local copy.'};
function renderDateReview(photo,data){
 if(!data?.choices.length)return;$('dateReview').hidden=false;
 const messages={conflict:'Source timestamps describe different moments.',timezone_unknown:'A source has no timezone. These dates cannot be compared reliably yet.',equivalent:'These source dates agree after normalization.',single:'One capture-date source is available.',invalid:'A source date could not be parsed.'};
 $('dateStatus').textContent=data.selected?(data.selected.source==='owner'?'You set this date. Source values are retained.':'You chose a date for this file. All source values are retained.'):data.inferred?'No recorded capture date. This date is read from the file name; confirm it or choose another.':data.status==='missing'?'No recorded capture date. Choose a file-name date or set one.':messages[data.status]||'Review the source dates.';
 $('dateError').textContent='';$('dateChoice').replaceChildren();
 for(const c of data.choices){const option=document.createElement('option');option.value=c.id;option.disabled=!c.normalized;option.textContent=`${c.date.value} · ${c.inferred?'read from '+c.date.field+' '+c.name:c.date.field||c.source.extractor}${c.normalized?.timezone_known?'':' · timezone unknown'}`;$('dateChoice').append(option)}
 if(data.selected&&data.choices.some(c=>c.id===data.selected.id))$('dateChoice').value=data.selected.id;
 $('saveDate').disabled=!data.choices.some(c=>c.normalized);$('resetDate').hidden=!data.selected;
 const save=async(reset)=>{const b=reset?$('resetDate'):$('saveDate');b.disabled=true;try{await saveOrganization(reset?'reset_date':'date',reset?{content_hash:photo.content_hash}:{content_hash:photo.content_hash,choice:$('dateChoice').value});summary=await api('/api/summary');$('year').replaceChildren(new Option('All years',''),...summary.years.map(y=>new Option(y,y)));$('year').value=state.year;$('inspector').close();await load();notify(reset?'Source date restored':'Date choice saved')}catch(e){$('dateError').textContent=e.message}finally{b.disabled=false}};
 $('saveDate').onclick=()=>save(false);$('resetDate').onclick=()=>save(true);
}
let reviewContext=null,reviewChanged=false,reviewBusy=false;
function renderSearchReview(p){
 const available=!!p.content_hash&&!!state.query.trim()&&['visual','descriptions','video_images'].includes(state.mode);
 $('searchReview').hidden=!available;reviewContext=available?{mode:state.mode,query:state.query,item:p.frame_id||p.content_hash,id:p.id,start:p.moment?.start??null}:null;
 if(!available)return;
 $('reviewQuery').textContent='“'+state.query+'”';
 $('reviewStatus').textContent=p.search_verdict==='match'?'You marked this as a match.':p.search_verdict==='mismatch'?'You marked this as a mismatch.':'Unreviewed. Your choices reorder this query; they do not change image metadata.';
 $('reviewMatch').setAttribute('aria-pressed',String(p.search_verdict==='match'));$('reviewMismatch').setAttribute('aria-pressed',String(p.search_verdict==='mismatch'));$('reviewReset').hidden=!p.search_verdict;
 for(const id of ['reviewMatch','reviewMismatch','reviewReset'])$(id).disabled=reviewBusy;
}
async function saveSearchReview(verdict){
 if(!reviewContext||reviewBusy)return;const context={...reviewContext};reviewBusy=true;renderSearchReview(items[selected]);
 try{await saveOrganization('search_review',{mode:context.mode,query:context.query,item:context.item,verdict});const p=items.find(i=>i.id===context.id&&(i.moment?.start??null)===context.start);if(p)p.search_verdict=verdict==='reset'?null:verdict;reviewChanged=true;notify('Search review saved. Ordering updates when you close this photo.')}catch(e){notify(e.message)}finally{reviewBusy=false;if(items[selected])renderSearchReview(items[selected])}
}
$('reviewMatch').onclick=()=>saveSearchReview('match');$('reviewMismatch').onclick=()=>saveSearchReview('mismatch');$('reviewReset').onclick=()=>saveSearchReview('reset');
$('inspector').addEventListener('close',()=>{if(reviewChanged){reviewChanged=false;load()}});
function detail(id,start=null){const index=items.findIndex(p=>p.id===id&&(start===null||p.moment?.start===start));if(index<0)return;selected=index;stopVideo();videoSeek=start||0;const p=items[index];renderSearchReview(p);detailTags();renderDetailBuckets(p);if(p.kind==='video'&&p.content_hash&&summary?.capabilities.video_playback){$('prepareVideo').hidden=false;$('prepareVideo').onclick=()=>videoStatus(p,true);videoStatus(p)};$('detailPhoto').src=p.frame_id?'/frame-preview/'+p.frame_id:'/preview/'+p.id;$('detailPhoto').alt=p.name;$('detailTitle').textContent=p.name;$('detailTranscript').hidden=true;$('detailAI').hidden=true;$('detailOCR').hidden=true;$('dateReview').hidden=true;let metadataPromise;const readMetadata=()=>metadataPromise||(metadataPromise=api('/api/metadata/'+id));$('detailPath').innerHTML='';readMetadata().then(data=>{if(items[selected]?.id!==id)return;renderDetailPath(data.sources||[]);renderDateReview(p,data.dates);if(data.transcript){$('detailTranscript').hidden=false;$('transcriptSegments').innerHTML=data.transcript.segments.length?data.transcript.segments.map(s=>`<p><button class="chip" data-seek="${s.start_seconds}" data-testid="inspector.transcript.seek.${s.start_seconds}">Go to ${duration(s.start_seconds)}</button> ${escape(s.text)}</p>`).join(''):escape(data.transcript.status==='no_audio'?'No audio track found.':data.transcript.status==='no_speech'?'No speech detected.':'Transcription unavailable.');$('transcriptSegments').querySelectorAll('[data-seek]').forEach(b=>b.onclick=()=>{videoSeek=Number(b.dataset.seek);if(!$('detailVideo').hidden){$('detailVideo').currentTime=videoSeek}else videoStatus(p,true)})}if(data.ai?.description){$('detailAI').hidden=false;$('aiCaption').textContent=data.ai.description.caption;$('aiObjects').textContent=data.ai.description.category+' · '+data.ai.description.objects.map(o=>o.color+' '+o.name).join(', ')}if(data.ocr?.text){$('ocrText').textContent=data.ocr.text;$('detailOCR').hidden=false}}).catch(()=>{});$('allMetadata').open=false;$('rawMetadata').textContent='Open to read the available source metadata.';$('allMetadata').ontoggle=async()=>{if(!$('allMetadata').open)return;$('rawMetadata').textContent='Reading metadata…';try{const data=await readMetadata();if(items[selected]?.id===id)$('rawMetadata').textContent=JSON.stringify(data,null,2)}catch{if(items[selected]?.id===id)$('rawMetadata').textContent='Metadata is unavailable.'}};const date=p.date,location=p.location;$('facts').innerHTML=`<dt>Date</dt><dd>${escape(date?date.value:'Not recorded')}<span class="source">${escape(date?date.meaning+' · '+date.source:'No parsed timestamp')}${date&&!date.timezone_known?' · timezone unknown':''}</span></dd><dt>Location</dt><dd>${location?escape(location.lat.toFixed(6)+', '+location.lon.toFixed(6)):'Not recorded'}<span class="source">${escape(location?location.source:'No inferred location')}${p.location_confirmed?' · <button class="link-button" id="resetLocation" data-testid="inspector.location.reset" style="padding:0;font-size:10px">Reset location</button>':''}</span></dd><dt>Camera</dt><dd>${escape(p.camera||'Not recorded')}</dd>${p.keywords&&p.keywords.length?`<dt>Keywords</dt><dd>${p.keywords.map(k=>`<span class="chip" style="display:inline-flex;margin:0 6px 6px 0">${escape(k)}</span>`).join('')}<span class="source">Written into the file by an editing tool</span></dd>`:''}${p.caption||p.title?`<dt>Caption</dt><dd>${p.title?`<strong>${escape(p.title)}</strong>${p.caption?'<br>':''}`:''}${p.caption?escape(p.caption):''}${p.rating?`<span class="source">Rated ${'★'.repeat(p.rating)} in the editing tool</span>`:''}</dd>`:''}<dt>File</dt><dd>${escape(p.extension)} · ${size(p.size)}${p.duration?' · '+duration(p.duration):''}${p.duplicate?'<span class="source">Exact duplicate found in this archive</span>':''}${p.metadata_error?'<span class="source">Metadata extraction reported an error</span>':''}</dd></dd>`;if($('resetLocation'))$('resetLocation').onclick=async()=>{const b=$('resetLocation');b.disabled=true;try{await saveOrganization('reset_location',{content_hash:p.content_hash});const row=items[selected];if(row){row.location=null;row.location_confirmed=false}notify('Location reset; the recorded value is back in charge.');detail(p.id,start);if(state.view==='gaps'){const request=++requestId;renderGaps(request,true)}}catch(e){notify(e.message);b.disabled=false}};$('previous').disabled=index===0;$('next').disabled=index===items.length-1;if(!$('inspector').open)$('inspector').showModal()}
function appearance(){root.dataset.theme=state.theme;root.style.setProperty('--tile',state.tile+'px');document.querySelectorAll('[data-setting="theme"] button').forEach(b=>b.classList.toggle('active',b.dataset.value===state.theme));$('density').value=state.tile}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.view||b.dataset.quality){trail.length=0;lastSnapshot=null;state.mode='everything';$('searchMode').value=state.mode;$('runVisual').hidden=false;state.person='';state.place='';state.trip='';state.stack='';state.bucket='';state.collectionName='';state.selecting=false;selection.clear();$('query').placeholder=EVERYTHING_HINT}if(b.dataset.view){state.filter='all';state.year='';state.after='';state.before='';$('year').value='';$('after').value='';$('before').value='';state.view=b.dataset.view;state.query='';$('query').value='';load()}if(b.dataset.filter){state.filter=b.dataset.filter;load()}if(b.dataset.photo){const p=items.find(p=>p.id===b.dataset.photo);if(state.selecting){if(p.content_hash){if(selection.has(p.content_hash))selection.delete(p.content_hash);else if(selection.size<200)selection.add(p.content_hash);renderTiles()}}else detail(b.dataset.photo,b.dataset.moment===undefined?null:Number(b.dataset.moment))};if(b.dataset.quality){state.view='photos';state.filter=b.dataset.quality;state.query='';state.year='';$('query').value='';$('year').value='';load()}if(b.hasAttribute('data-clear')){$('descriptionCategory').value='';state.person='';state.place='';state.trip='';state.bucket='';state.collectionName='';selection.clear();state.after='';state.before='';$('after').value='';$('before').value='';state.query='';state.filter='all';state.year='';$('query').value='';$('year').value='';load()}if(b.hasAttribute('data-retry'))load();if(b.dataset.value){state.theme=b.dataset.value;appearance()}});

let slides=[],slideIndex=0,slideTimer=null,slidePlaying=false;
function slideSchedule(){clearTimeout(slideTimer);if(slidePlaying&&$('slideshow').open)slideTimer=setTimeout(()=>{if(slideIndex+1<slides.length){slideIndex++;showSlide()}else{slidePlaying=false;$('slidePause').textContent='Play again'}},Number($('slideSpeed').value)*1000)}
function showSlide(){clearTimeout(slideTimer);const p=slides[slideIndex];if(!p)return;$('slideTitle').textContent=dateLabel(p.day);$('slideImage').alt=p.name;$('slideImage').onload=slideSchedule;$('slideImage').onerror=()=>{$('slideCount').textContent=`${slideIndex+1} / ${slides.length} · Preview unavailable`;slideSchedule()};$('slideImage').src='/preview/'+p.id;$('slideCount').textContent=`${slideIndex+1} / ${slides.length}`;$('slidePrevious').disabled=slideIndex===0;$('slideNext').disabled=slideIndex+1===slides.length;$('slidePause').textContent=slidePlaying?'Pause':'Play';if($('slideImage').complete&&$('slideImage').naturalWidth)slideSchedule()}
$('after').onchange=()=>{state.after=$('after').value;load()};$('before').onchange=()=>{state.before=$('before').value;load()};
$('playSlideshow').onclick=async()=>{const button=$('playSlideshow');$('drawer').hidden=true;button.disabled=true;try{const params=new URLSearchParams({q:state.query,year:state.year,after:state.after,before:state.before,kind:state.filter,located:'0',person:state.person,place:state.place,trip:state.trip,bucket:state.bucket});const data=await api('/api/slideshow?'+params);if(!data.items.length){$('toast').textContent='No photos match these slideshow filters.';$('toast').hidden=false;return}slides=data.items;slideIndex=0;slidePlaying=true;$('slideCoverage').textContent=`${data.truncated?'First '+slides.length+' of '+data.total+' matching photos. ':''}Oldest first. ${data.coverage}`;$('slideshow').showModal();showSlide()}catch{$('toast').textContent='Slideshow unavailable. Check the date range and local viewer.';$('toast').hidden=false}finally{button.disabled=false}};
$('closeSlideshow').onclick=()=>$('slideshow').close();$('slideshow').addEventListener('close',()=>{clearTimeout(slideTimer);slidePlaying=false;$('playSlideshow').focus()});
$('slidePrevious').onclick=()=>{if(slideIndex>0){slideIndex--;showSlide()}};$('slideNext').onclick=()=>{if(slideIndex+1<slides.length){slideIndex++;showSlide()}};$('slidePause').onclick=()=>{slidePlaying=!slidePlaying;if(slidePlaying&&slideIndex===slides.length-1)slideIndex=0;showSlide()};$('slideSpeed').onchange=slideSchedule;
document.addEventListener('keydown',e=>{if(!$('slideshow').open)return;if(e.key==='ArrowLeft'){$('slidePrevious').click();e.preventDefault()}if(e.key==='ArrowRight'){$('slideNext').click();e.preventDefault()}});
$('query').addEventListener('input',()=>{clearTimeout(timer);clearTimeout(suggestTimer);suggestTimer=setTimeout(showSuggest,120);state.query=$('query').value;state.view='photos';nav();if(state.mode==='visual'||state.mode==='video_images'){requestId++;$('runVisual').disabled=false;$('runVisual').textContent='Search';items=[];nextOffset=null;$('loadMore').hidden=true;$('content').removeAttribute('aria-busy');$('content').innerHTML='<div class="empty"><h2>Visual matches</h2><p>Select Search to find matches for your description.</p></div>';$('subtitle').textContent='Select Search to update the results.'}else timer=setTimeout(()=>load(),200)});$('runVisual').onclick=()=>load();$('query').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();closeSuggest();load()}});$('descriptionCategory').onchange=()=>load();$('searchMode').onchange=()=>{state.mode=$('searchMode').value;state.view='photos';if(['moments','video_images'].includes(state.mode)){state.selecting=false;selection.clear();}$('runVisual').hidden=false;clearTimeout(timer);$('query').placeholder=state.mode==='visual'?'Describe an image, such as red car…':state.mode==='video_images'?'Describe a video moment…':state.mode==='moments'?'Search spoken words in videos…':state.mode==='descriptions'?'Search AI tags, such as red car…':state.mode==='everything'?EVERYTHING_HINT:'Search detected text, people, places, filenames…';load()};$('year').onchange=()=>{state.year=$('year').value;load()};$('loadMore').onclick=()=>load(true);$('settings').onclick=()=>{$('drawer').hidden=false;$('closeSettings').focus()};$('closeSettings').onclick=()=>{$('drawer').hidden=true;$('settings').focus()};$('density').oninput=()=>{state.tile=Number($('density').value);appearance()};$('reset').onclick=()=>{state.theme='dark';state.tile=190;appearance()};$('closeInspector').onclick=()=>$('inspector').close();$('previous').onclick=()=>{if(selected>0)detail(items[selected-1].id,items[selected-1].moment?.start??null)};$('next').onclick=()=>{if(selected+1<items.length)detail(items[selected+1].id,items[selected+1].moment?.start??null)};$('inspector').addEventListener('click',e=>{if(e.target===$('inspector'))$('inspector').close()});document.addEventListener('keydown',e=>{if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('query').focus()}if(e.key==='Escape')$('drawer').hidden=true;if($('inspector').open){if(e.key==='ArrowRight')$('next').click();if(e.key==='ArrowLeft')$('previous').click()}});async function start(){try{summary=await api('/api/summary');$('videoImagesOption').disabled=!summary.capabilities.video_images;$('transcriptOption').disabled=!summary.capabilities.transcripts;$('descriptionOption').disabled=!summary.capabilities.descriptions;$('visualOption').disabled=!summary.capabilities.semantic_search;state.mode='everything';$('searchMode').value=state.mode;$('query').placeholder=EVERYTHING_HINT;for(const year of summary.years){const option=document.createElement('option');option.value=year;option.textContent=year;$('year').append(option)}if(!summary.source_available){$('toast').textContent='The external drive is unavailable. Cached previews may still work.';$('toast').hidden=false}}catch{}appearance();load()}start();
// Timeline scrubber: year and month buckets from /api/timeline, each a jump into search pagination. The list keeps loading in both directions as you scroll.
const monthLabel=m=>new Date(m+'-15T12:00:00').toLocaleDateString(undefined,{month:'short'});
async function loadTimeline(request,params){
 const browse=state.view==='photos'&&!(['visual','video_images','descriptions','moments'].includes(state.mode)&&state.query.trim());
 if(!browse){timelineData=null;$('timeline').hidden=true;return}
 const q=new URLSearchParams(params);for(const k of ['offset','limit','category'])q.delete(k);
 try{const data=await api('/api/timeline?'+q);if(request!==requestId)return;timelineData=data;renderTimeline()}catch(e){$('timeline').hidden=true}
}
// Fill the gaps: proposals grouped by the evidence rule behind them. Accepting a group appends ordinary set_date / set_location events; nothing is written until then.
const GAP_KINDS={date:{label:'Dates',filled:'dated',thing:'a date'},location:{label:'Places',filled:'located',thing:'a location'}};
const gapState={kind:'date',group:'',skip:new Set(),data:null,busy:false};
async function renderGaps(request,keepScroll=false){
 $('title').textContent='Fill the gaps';$('subtitle').textContent='Dates and places worked out from the archive’s own evidence. Each group names its rule; nothing is written until you accept.';
 if(!keepScroll)$('content').innerHTML='<p class="local-note">Working out proposals…</p>';
 let data;
 try{data=await api('/api/gaps?'+new URLSearchParams({kind:gapState.kind,group:gapState.group,offset:'0',limit:'80'}))}
 catch(error){if(request!==requestId)return;$('content').innerHTML=`<div class="empty"><h2>Proposals are not available</h2><p>${escape(error.message)}</p><button class="primary" data-retry data-testid="search.error.retry">Retry</button></div>`;return}
 if(request!==requestId)return;
 gapState.data=data;if(data.group&&data.group.id!==gapState.group){gapState.group=data.group.id;gapState.skip.clear()}
 items=data.group?data.group.items:[];nextOffset=null;drawGaps();
}
function gapCaption(row){
 const p=row.proposal;if(!p)return row.day?dateLabel(row.day):'No date recorded';
 return gapState.kind==='date'?dateLabel(p.date):p.place_name||`${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`;
}
function gapTile(row){
 const skipped=gapState.skip.has(row.content_hash);
 return `<figure class="gap-tile" data-skipped="${skipped}" data-content="${row.content_hash}"><button class="tile" data-photo="${row.id}" data-testid="gaps.tile.open.${row.id}" aria-label="Open ${escape(row.name)}"><img loading="lazy" decoding="async" src="/preview/${row.id}" alt="${escape(row.name)}">${row.kind==='video'?'<span class="duration">▶ Video</span>':''}</button>${gapState.data.group.action==='accept'?`<button class="gap-skip" data-gap-skip="${row.content_hash}" data-testid="gaps.item.skip.${row.id}" aria-pressed="${skipped}" title="${skipped?'Include this file again':'Leave this file out of the batch'}">✕</button>`:''}<figcaption><b title="${escape(row.evidence)}">${escape(gapCaption(row))}</b><span title="${escape(row.evidence)}">${escape(row.evidence)}</span></figcaption></figure>`;
}
function drawGaps(){
 const data=gapState.data,c=data.coverage,kind=GAP_KINDS[gapState.kind];
 const pct=c.total?Math.round(100*c.filled/c.total):0,ownerPct=c.total?100*c.owner/c.total:0;
 const tabs=`<div class="segmented" role="tablist" aria-label="What to fill">${Object.entries(GAP_KINDS).map(([k,v])=>`<button role="tab" data-gap-kind="${k}" data-testid="gaps.kind.${k}" class="${k===gapState.kind?'active':''}" aria-selected="${k===gapState.kind}">${v.label}${k===gapState.kind?` <small>${number(c.missing)} missing</small>`:''}</button>`).join('')}</div>`;
 const cover=`<div class="gap-cover">${tabs}<div class="gap-bar" title="${pct}% ${kind.filled}"><i style="width:${Math.max(0,pct-ownerPct)}%"></i><i class="owner" style="width:${ownerPct}%" title="${number(c.owner)} set by you"></i></div><span><strong>${number(c.filled)}</strong> of ${number(c.total)} ${kind.filled} · <strong>${number(c.proposed)}</strong> proposed · ${number(c.no_evidence)} without evidence${c.undated?` · ${number(c.undated)} need a date first`:''}${c.not_expected?` · ${number(c.not_expected)} not expected`:''}</span></div>`;
 const strengthOrder=['strong','medium','weak','none'];
 const sections=strengthOrder.map(strength=>{const rows=data.groups.filter(g=>g.strength===strength);if(!rows.length)return '';const label={strong:'Strong evidence',medium:'Good evidence',weak:'Weak evidence, check first',none:'Nothing to recover'}[strength];return `<h3>${label}</h3>`+rows.map(g=>`<button class="gap-group ${g.id===gapState.group?'active':''}" data-gap-group="${escape(g.id)}" data-testid="gaps.group.open.${escape(g.rule)}" aria-current="${g.id===gapState.group}"><i class="gap-dot ${g.strength}"></i><b>${escape(g.title)}</b><small>${number(g.count)}</small></button>`).join('')}).join('');
 const g=data.group;let panel;
 if(!g)panel=`<div class="empty"><h2>Nothing left to propose</h2><p>Every file with usable evidence has ${kind.thing}. Files without evidence stay honestly unknown.</p></div>`;
 else{
  const remaining=g.count-[...gapState.skip].length;
  const verb=g.action==='accept'?`Accept ${number(remaining)}`:g.action==='dismiss'?`Mark ${number(g.count)} as not expected`:`Restore ${number(g.count)}`;
  const note={strong:'Strong evidence',medium:'Good evidence',weak:'Weak evidence',none:g.action==='restore'?'Set aside by you':'No proposal'}[g.strength];
  panel=`<h2>${escape(g.title)}<small>${note}</small></h2><p class="meaning">${escape(g.meaning)}</p><div class="gap-actions"><button class="primary" data-gap-action="${g.action}" data-testid="gaps.group.${g.action}" ${remaining<=0||gapState.busy?'disabled':''}>${verb}</button>${g.action==='accept'?`<button class="outline" data-gap-action="dismiss" data-testid="gaps.group.dismiss" ${gapState.busy?'disabled':''}>Not expected</button>`:''}${gapState.skip.size?`<span class="muted">${number(gapState.skip.size)} left out · <button class="link-button" data-gap-unskip data-testid="gaps.group.unskip">include all</button></span>`:g.action==='accept'?'<span class="muted">Hover a file and press ✕ to leave it out. Open a file to check it; you come back here.</span>':''}</div><div class="grid">${g.items.map(gapTile).join('')}</div>${g.next_offset!==null?`<div class="gap-more"><button class="outline" data-gap-more="${g.next_offset}" data-testid="gaps.group.more">Show more of this group</button></div>`:''}`;
 }
 $('content').innerHTML=cover+`<div class="gaps"><nav class="gap-groups" aria-label="Evidence groups">${sections||'<p class="local-note">No groups yet.</p>'}</nav><section class="gap-panel">${panel}</section></div>`;
 $('content').querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>{img.closest('.tile').classList.add('failed')},{once:true}));
}
async function gapAct(action){
 const g=gapState.data?.group;if(!g||gapState.busy)return;gapState.busy=true;drawGaps();
 try{
  const result=await api('/api/gaps',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:gapState.kind,group:g.id,action,skip:[...gapState.skip]})});
  const kind=GAP_KINDS[gapState.kind];
  notify(action==='accept'?`${number(result.items)} ${result.items===1?'file':'files'} ${kind.filled} from ${g.title}.`:action==='dismiss'?`${number(result.items)} marked as not expected.`:`${number(result.items)} restored.`);
  gapState.skip.clear();try{summary=await api('/api/summary')}catch{}
 }catch(error){notify(error.message)}
 finally{gapState.busy=false;const request=++requestId;await renderGaps(request,true)}
}
document.addEventListener('click',async e=>{
 const b=e.target.closest('button');if(!b||state.view!=='gaps')return;
 if(b.dataset.gapKind){gapState.kind=b.dataset.gapKind;gapState.group='';gapState.skip.clear();load();return}
 if(b.dataset.gapGroup!==undefined){gapState.group=b.dataset.gapGroup;gapState.skip.clear();const request=++requestId;await renderGaps(request,true);return}
 if(b.dataset.gapSkip){const d=b.dataset.gapSkip;if(gapState.skip.has(d))gapState.skip.delete(d);else gapState.skip.add(d);drawGaps();return}
 if(b.hasAttribute('data-gap-unskip')){gapState.skip.clear();drawGaps();return}
 if(b.dataset.gapAction){gapAct(b.dataset.gapAction);return}
 if(b.dataset.gapMore){const g=gapState.data.group;b.disabled=true;try{const more=await api('/api/gaps?'+new URLSearchParams({kind:gapState.kind,group:g.id,offset:b.dataset.gapMore,limit:'80'}));if(more.group&&more.group.id===g.id){g.items=[...g.items,...more.group.items];g.next_offset=more.group.next_offset;items=g.items;drawGaps()}}catch(error){notify(error.message);b.disabled=false}}
});
function renderTimeline(){
 const t=timelineData;if(!t||!t.years.length){$('timeline').hidden=true;document.body.classList.remove('has-timeline');return}
 $('timeline').hidden=false;document.body.classList.add('has-timeline');
 $('timeline').innerHTML=t.years.map(y=>`<div class="year" data-year="${y.year}"><button data-jump="${y.offset}" data-testid="timeline.year.jump.${y.year}" title="${number(y.count)} in ${y.year}">${y.year}</button><div class="months">${y.months.map(m=>`<button data-jump="${m.offset}" data-month="${m.month}" data-testid="timeline.month.jump.${m.month}" title="${number(m.count)} in ${monthLabel(m.month)} ${y.year}">${monthLabel(m.month)}</button>`).join('')}</div></div>`).join('')+(t.undated.count?`<div class="year" data-year="undated"><button data-jump="${t.undated.offset}" data-testid="timeline.undated.jump" title="${number(t.undated.count)} without a date">Undated</button></div>`:'');
 markTimeline();
}
function markTimeline(){
 if($('timeline').hidden)return;
 const heads=[...$('content').querySelectorAll('.group-head[data-day]')];
 const current=heads.find(h=>h.getBoundingClientRect().bottom>80)||heads[heads.length-1];
 const day=current?current.dataset.day:'';const year=day?day.slice(0,4):(current?'undated':'');
 $('timeline').querySelectorAll('.year').forEach(y=>y.classList.toggle('active',y.dataset.year===year));
 $('timeline').querySelectorAll('[data-month]').forEach(b=>b.classList.toggle('active',!!day&&b.dataset.month===day.slice(0,7)));
}
$('timeline').onclick=e=>{const b=e.target.closest('[data-jump]');if(!b)return;window.scrollTo({top:0});load(false,Number(b.dataset.jump))};
let scrollTick=false;addEventListener('scroll',()=>{if(scrollTick)return;scrollTick=true;requestAnimationFrame(()=>{scrollTick=false;markTimeline()})},{passive:true});
const pager=new IntersectionObserver(entries=>{for(const e of entries){if(!e.isIntersecting||loadingPage||state.view!=='photos')continue;if(e.target.id==='loadMore'&&nextOffset!==null)load(true);else if(e.target.id==='loadEarlier'&&firstOffset>0)load('earlier')}},{rootMargin:'700px 0px'});
pager.observe($('loadMore'));pager.observe($('loadEarlier'));
window.__vaultPaging=()=>({loadingPage,nextOffset,firstOffset,view:state.view,items:items.length});
// Everything search: one box, results grouped by kind. Names and recorded facts first; model output labelled; image similarity offered as a separate, slower step.
const EVERYTHING_HINT='Search people, places, dates, text in photos, file names…';
const dateHitLabel=h=>h.kind==='year'?h.value:h.kind==='month'?new Date(h.value+'-15T12:00:00').toLocaleDateString(undefined,{month:'long',year:'numeric'}):dateLabel(h.value);
async function renderEverything(request){
 $('title').textContent='Search results';$('subtitle').textContent='Searching everything…';$('timeline').hidden=true;$('loadMore').hidden=true;$('loadEarlier').hidden=true;
 $('content').setAttribute('aria-busy','true');
 try{
  const data=await api('/api/search?'+new URLSearchParams({q:state.query,year:state.year,after:state.after,before:state.before,limit:'12'}));if(request!==requestId)return;
  items=[...data.files.items,...data.text.items,...data.descriptions.items,...data.moments.items];nextOffset=null;firstOffset=0;
  const section=(title,total,body,more='',meaning='')=>total?`<section class="search-section"><div class="group-head"><h2>${title}</h2><span>${number(total)}</span>${meaning?`<span class="muted">${escape(meaning)}</span>`:''}<span class="spacer"></span>${more?`<button class="chip" data-testid="search.section.more.${title.toLowerCase().replace(/[^a-z]+/g,'-')}" ${more}>See all</button>`:''}</div>${body}</section>`:'';
  const card=(t,attrs,sub)=>`<button class="place-card" data-testid="search.card.open.${t.id}" ${attrs}>${t.cover?`<img loading="lazy" src="/preview/${t.cover}" alt="">`:''}<strong>${escape(t.name||'Unnamed place')}</strong><small>${sub}</small></button>`;
  const count=['people','places','trips','albums','dates','files','text','descriptions','moments'].reduce((n,k)=>n+data[k].total,0);
  $('subtitle').textContent=count?`${number(count)} results for “${data.query}” across your library`:`Nothing matched “${data.query}” in names, dates, file details, recognized text, descriptions or spoken words.`;
  let html='';
  html+=section('Dates',data.dates.total,'<div class="date-hits">'+data.dates.items.map(h=>`<button class="chip" data-jump-date="${h.offset}" data-testid="search.date.jump.${h.offset}">${escape(dateHitLabel(h))} · ${number(h.count)} files</button>`).join('')+'</div>');
  html+=section('People',data.people.total,'<div class="people search-people">'+data.people.items.map(p=>`<button class="person-card" data-person="${p.id}" data-testid="search.person.open.${p.id}">${p.face?`<img src="/face-preview/${p.face}" alt="">`:p.cover?`<img src="/preview/${p.cover}" alt="">`:`<div class="person-placeholder">${escape(p.name.slice(0,1))}</div>`}<strong>${escape(p.name)}</strong><small>${number(p.count)} ${p.count===1?'file':'files'}</small></button>`).join('')+'</div>');
  html+=section('Places',data.places.total,'<div class="places">'+data.places.items.map(p=>card(p,`data-place="${p.id}"`,`${number(p.count)} files${p.geographic?' · '+escape([p.geographic.region,p.geographic.country].filter(Boolean).join(', ')):''}`)).join('')+'</div>');
  html+=section('Trips',data.trips.total,'<div class="places">'+data.trips.items.map(t=>card(t,`data-trip-jump="${t.id}"`,`${escape(t.after)} – ${escape(t.before)} · ${number(t.count)} files`)).join('')+'</div>');
  html+=section('Albums',data.albums.total,'<div class="places">'+data.albums.items.map(a=>card(a,`data-album-jump="${a.id}"`,`${number(a.count)} photos`)).join('')+'</div>');
  html+=section('Files and details',data.files.total,'<div class="grid">'+data.files.items.map(tileHtml).join('')+'</div>','data-see-mode="metadata"',data.files.meaning);
  html+=section('Text read from photos',data.text.total,'<div class="grid">'+data.text.items.map(tileHtml).join('')+'</div>','data-see-mode="metadata"',data.text.meaning);
  html+=section('AI descriptions',data.descriptions.total,'<div class="grid">'+data.descriptions.items.map(tileHtml).join('')+'</div>','data-see-mode="descriptions"',data.descriptions.meaning);
  html+=section('Spoken words in videos',data.moments.total,'<div class="grid">'+data.moments.items.map(tileHtml).join('')+'</div>','data-see-mode="moments"',data.moments.meaning);
  const slow=[];if(summary?.capabilities.semantic_search)slow.push('<button class="chip" data-see-mode="visual" data-testid="search.mode.visual">Image search</button>');if(summary?.capabilities.video_images)slow.push('<button class="chip" data-see-mode="video_images" data-testid="search.mode.video-images">Images in videos</button>');
  if(slow.length)html+=`<section class="search-section"><div class="group-head"><h2>Look inside the pictures</h2><span class="muted">Model similarity for “${escape(data.query)}”. Slower, and not yet verified against examples.</span></div><div class="date-hits">${slow.join('')}</div></section>`;
  $('content').innerHTML=html||'<div class="empty"><h2>No results</h2><p>Try a name, a place, a month such as “June 2019”, or a word that appears in a photo.</p></div>';
  $('content').querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>img.closest('.tile,.person-card,.place-card')?.classList.add('failed'),{once:true}));
  $('content').querySelectorAll('[data-person]').forEach(b=>b.onclick=()=>{const p=data.people.items.find(p=>p.id===b.dataset.person);openCollection(p.id,'',p.name)});
  $('content').querySelectorAll('[data-place]').forEach(b=>b.onclick=()=>{const p=data.places.items.find(p=>p.id===b.dataset.place);openCollection('',p.id,p.name||'Unnamed place')});
  $('content').querySelectorAll('[data-trip-jump]').forEach(b=>b.onclick=()=>{const t=data.trips.items.find(t=>t.id===b.dataset.tripJump);state.view='photos';state.collectionName=t.name;state.person='';state.place=t.place||'';state.trip=t.id;state.query='';state.filter='all';state.year='';state.after=t.after;state.before=t.before;state.selecting=false;selection.clear();$('query').value='';$('after').value=t.after;$('before').value=t.before;load()});
  $('content').querySelectorAll('[data-album-jump]').forEach(b=>b.onclick=()=>{state.view='albums';state.query='';$('query').value='';load()});
  $('content').querySelectorAll('[data-jump-date]').forEach(b=>b.onclick=()=>{state.query='';$('query').value='';window.scrollTo({top:0});load(false,Number(b.dataset.jumpDate))});
  $('content').querySelectorAll('[data-see-mode]').forEach(b=>b.onclick=()=>{state.mode=b.dataset.seeMode;$('searchMode').value=state.mode;load()});
  selectionTools();perfImages();
 }catch(error){if(request!==requestId)return;$('subtitle').textContent=error.message;$('content').innerHTML='<div class="empty"><h2>Search could not finish</h2><p>Try again.</p><button class="primary" data-retry data-testid="search.error.retry">Retry</button></div>'}
 finally{if(request===requestId)$('content').removeAttribute('aria-busy')}
}
})();
