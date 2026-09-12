// Offline basemap: Natural Earth layers drawn on canvas tiles, plus place labels.
// Loaded on demand by the Map and Trips views. Needs Leaflet (window.L).
(function(){
const DARK={ocean:'#141c19',land:'#1f2823',border:'#4a5a51',state:'#34413a',water:'#1b2f34',river:'#2c4b54',urban:'#2a3530',hw:'#8a9a8f',hwCase:'#111715',road:'#5a685f',track:'#3f4a44',label:'#c9d3cc',halo:'#141c19',country:'#8d9b93'};
const LIGHT={ocean:'#d9e4e0',land:'#f4f2eb',border:'#a39e94',state:'#c9c4b9',water:'#c4d9e3',river:'#a8c6d3',urban:'#e7e3d8',hw:'#b3a58a',hwCase:'#fbfaf6',road:'#cfc9bc',track:'#e0dbd0',label:'#3b433e',halo:'#f4f2eb',country:'#7a847e'};
let data=null,loading=null;
function palette(){return document.documentElement.dataset.theme==='light'?LIGHT:DARK}
function bbox(coords){let x0=180,y0=90,x1=-180,y1=-90;for(const [x,y] of coords){if(x<x0)x0=x;if(x>x1)x1=x;if(y<y0)y0=y;if(y>y1)y1=y}return [x0,y0,x1,y1]}
const CELL=2;
function index(features){const cells=new Map();for(const f of features){const [x0,y0,x1,y1]=f.b;for(let cx=Math.floor(x0/CELL);cx<=Math.floor(x1/CELL);cx++)for(let cy=Math.floor(y0/CELL);cy<=Math.floor(y1/CELL);cy++){const k=cx+':'+cy;if(!cells.has(k))cells.set(k,[]);cells.get(k).push(f)}}return cells}
function query(cells,x0,y0,x1,y1){const out=new Set();for(let cx=Math.floor(x0/CELL);cx<=Math.floor(x1/CELL);cx++)for(let cy=Math.floor(y0/CELL);cy<=Math.floor(y1/CELL);cy++){const list=cells.get(cx+':'+cy);if(list)for(const f of list)if(!(f.b[2]<x0||f.b[0]>x1||f.b[3]<y0||f.b[1]>y1))out.add(f)}return out}
function prepare(raw){
 const polys=(rows,extra)=>rows.map(r=>({...extra(r),p:r.p,b:bbox(r.p.flat())}));
 const lines=(rows,extra)=>rows.map(r=>({...extra(r),l:r.l,b:bbox(r.l.flat())}));
 const land=polys(raw.land,r=>({n:r.n,r:r.r,l:r.l}));
 const roads=[...lines(raw.highways,r=>({k:r.k,z:r.z,n:r.n})),...lines(raw.detail.roads||[],r=>({k:r.k,z:r.z,n:r.n}))];
 return {land,landIndex:index(land),lakes:index(polys(raw.water.lakes,r=>({z:r.z}))),rivers:index(lines(raw.water.rivers,r=>({z:r.z}))),borders:index(lines(raw.borders,r=>({z:r.z}))),urban:index(polys(raw.detail.urban||[],r=>({z:r.z}))),roads:index(roads),places:raw.places,countries:land.filter(f=>f.l&&(f.l[0]||f.l[1]))}
}
function load(){
 if(data)return Promise.resolve(data);
 if(!loading)loading=Promise.all(['land','water','borders','highways','places','detail'].map(n=>fetch('/basemap/'+n+'.json').then(r=>{if(!r.ok)throw new Error('Basemap layer '+n+' missing');return r.json()}))).then(([land,water,borders,highways,places,detail])=>{data=prepare({land,water,borders,highways,places,detail});return data});
 return loading;
}
const TILE=256;
function projector(coords,size){const n=2**coords.z;const scale=size/TILE;return (lon,lat)=>{const x=((lon+180)/360*n-coords.x)*TILE*scale;const s=Math.sin(Math.max(-85.05,Math.min(85.05,lat))*Math.PI/180);const y=((0.5-Math.log((1+s)/(1-s))/(4*Math.PI))*n-coords.y)*TILE*scale;return [x,y]}}
function tileBounds(coords){const n=2**coords.z;const lon=x=>x/n*360-180;const lat=y=>{const t=Math.PI-2*Math.PI*y/n;return 180/Math.PI*Math.atan(0.5*(Math.exp(t)-Math.exp(-t)))};return [lon(coords.x),lat(coords.y+1),lon(coords.x+1),lat(coords.y)]}
function strokeAll(ctx,items,pick,proj,pad,box){for(const f of items){for(const line of f.l){ctx.beginPath();let first=true;for(const [lon,lat] of line){const [x,y]=proj(lon,lat);if(first){ctx.moveTo(x,y);first=false}else ctx.lineTo(x,y)}ctx.stroke()}}}
function fillAll(ctx,items,proj){ctx.beginPath();for(const f of items){for(const ring of f.p){let first=true;for(const [lon,lat] of ring){const [x,y]=proj(lon,lat);if(first){ctx.moveTo(x,y);first=false}else ctx.lineTo(x,y)}ctx.closePath()}}ctx.fill('evenodd')}
function draw(canvas,coords,size){
 const ctx=canvas.getContext('2d');const c=palette();const z=coords.z;const proj=projector(coords,size);const s=size/TILE;
 const [x0,y0,x1,y1]=tileBounds(coords);const pad=(x1-x0)*0.25;const qx0=x0-pad,qy0=y0-pad,qx1=x1+pad,qy1=y1+pad;
 ctx.fillStyle=c.ocean;ctx.fillRect(0,0,size,size);ctx.lineJoin='round';ctx.lineCap='round';
 const land=query(data.landIndex,qx0,qy0,qx1,qy1);ctx.fillStyle=c.land;fillAll(ctx,land,proj);
 if(z>=8){const urban=[...query(data.urban,qx0,qy0,qx1,qy1)].filter(f=>f.z<=z+1);ctx.fillStyle=c.urban;fillAll(ctx,urban,proj)}
 const lakes=[...query(data.lakes,qx0,qy0,qx1,qy1)].filter(f=>f.z<=z+2);ctx.fillStyle=c.water;fillAll(ctx,lakes,proj);
 if(z>=4){ctx.strokeStyle=c.river;ctx.lineWidth=Math.max(0.6,(z-3)*0.35)*s;strokeAll(ctx,[...query(data.rivers,qx0,qy0,qx1,qy1)].filter(f=>f.z<=z+1),null,proj)}
 if(z>=3){ctx.strokeStyle=c.state;ctx.lineWidth=0.9*s;ctx.setLineDash([4*s,3*s]);strokeAll(ctx,[...query(data.borders,qx0,qy0,qx1,qy1)].filter(f=>f.z<=z+1),null,proj);ctx.setLineDash([])}
 ctx.strokeStyle=c.border;ctx.lineWidth=(z<4?0.8:1.2)*s;for(const f of land){for(const ring of f.p){ctx.beginPath();let first=true;for(const [lon,lat] of ring){const [x,y]=proj(lon,lat);if(first){ctx.moveTo(x,y);first=false}else ctx.lineTo(x,y)}ctx.closePath();ctx.stroke()}}
 if(z>=5){const roads=[...query(data.roads,qx0,qy0,qx1,qy1)];const minor=roads.filter(f=>f.k>=3&&(z>=11||(z>=9&&f.k===3)));const major=roads.filter(f=>f.k<=2&&f.z<=z+1.5);
  if(minor.length){ctx.strokeStyle=c.track;ctx.lineWidth=0.8*s;strokeAll(ctx,minor.filter(f=>f.k===4),null,proj);ctx.strokeStyle=c.road;ctx.lineWidth=(z>=12?2:1.1)*s;strokeAll(ctx,minor.filter(f=>f.k===3),null,proj)}
  const w=(z<=6?0.9:z<=8?1.4:z<=10?2.2:3.2)*s;if(z>=9){ctx.strokeStyle=c.hwCase;ctx.lineWidth=w+1.6*s;strokeAll(ctx,major,null,proj)}
  ctx.strokeStyle=c.hw;ctx.lineWidth=w;strokeAll(ctx,major.filter(f=>f.k===2),null,proj);ctx.lineWidth=w*1.25;strokeAll(ctx,major.filter(f=>f.k===1),null,proj)}
}
const Layer=L.GridLayer.extend({createTile(coords,done){const canvas=document.createElement('canvas');const size=TILE*Math.min(2,window.devicePixelRatio||1);canvas.width=size;canvas.height=size;canvas.style.width=TILE+'px';canvas.style.height=TILE+'px';setTimeout(()=>{try{draw(canvas,coords,size)}catch(e){console.warn('basemap tile',e)}done(null,canvas)},0);return canvas}});
function labels(map){
 const group=L.layerGroup().addTo(map);
 const render=()=>{group.clearLayers();const z=map.getZoom();const b=map.getBounds();const placed=[];const fits=(pt)=>placed.every(q=>Math.abs(q.x-pt.x)>70||Math.abs(q.y-pt.y)>18);
  const add=(lat,lon,text,cls)=>{if(!b.contains([lat,lon]))return;const pt=map.latLngToContainerPoint([lat,lon]);if(!fits(pt))return;placed.push(pt);group.addLayer(L.marker([lat,lon],{interactive:false,keyboard:false,icon:L.divIcon({className:'bm-label '+cls,html:'<span>'+text.replace(/[<>&]/g,'')+'</span>',iconSize:[0,0]})}))};
  if(z<=6)for(const f of data.countries.filter(f=>f.r<=(z<=2?2:z<=3?3:z<=4?5:9)).sort((a,b)=>a.r-b.r))add(f.l[0],f.l[1],f.n,'bm-country');
  if(z>=3){const rows=data.places.filter(p=>p[4]<=z&&b.contains([p[1],p[2]])).sort((a,b)=>a[3]-b[3]||b[5]-a[5]).slice(0,80);for(const p of rows)add(p[1],p[2],p[0],p[6]?'bm-capital':p[3]<=3?'bm-city':'bm-town')}
 };
 map.on('zoomend moveend',render);render();
 return group;
}
window.VaultBasemap={load,layer:opts=>new Layer({tileSize:TILE,updateWhenZooming:false,keepBuffer:2,...opts}),labels,redraw(){}};
})();
