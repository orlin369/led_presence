import {clone, outline, mountingPoint, polygonError, pointInside,
  buildLayouts, barYawAt, normalizedAngle} from './editor-geometry.js';

const el = id => document.getElementById(id);
const plan = el('editor-plan'), fields = el('editor-fields');
const status = el('editor-status');
const NS = 'http://www.w3.org/2000/svg';
let base, draft, selected = 'room', mode = 'select', layouts = [];
let undo = [], redo = [], busy = false, loaded = false;
let zoom = 1, pan = [0,0], bounds = [0,0,1,1], transform;
let drag, gesture, previewTimer, previewVersion = 0, width = 800, height = 600;
const pointers = new Map(), locks = new Set();
const equal = (a,b) => JSON.stringify(a) === JSON.stringify(b);
const dirty = () => loaded && !equal(base,draft);
const step = () => Math.max(1,Math.min(10000,Number(el('editor-grid').value)||100));
const locked = () => locks.has(selected) || (selected.startsWith('corner:') && locks.has('room')) ||
  (selected.startsWith('wall:') && locks.has('room'));
const roomEdit = () => mode === 'room';
const message = value => {status.textContent=value;};

function selection() {
  const [kind,id,barId] = selected.split(':');
  const group = ['controller','bar'].includes(kind) ? draft.lighting.controllers.find(g=>g.id===id) : null;
  return {kind,id,group,bar:group?.bars.find(b=>b.id===barId),
    sensor:kind==='sensor'?draft.sensors.find(s=>s.id===id):null,
    index:Number(id)};
}
function selectionExists(key) {
  if (key === 'room') return true;
  const [kind,id,barId]=key.split(':');
  if (['corner','wall'].includes(kind)) return Number(id)<outline(draft.room).length;
  if (kind==='sensor') return draft.sensors.some(s=>s.id===id);
  const group=draft.lighting.controllers.find(g=>g.id===id);
  return !!group && (kind==='controller' || group.bars.some(b=>b.id===barId));
}
function choose(key) {
  selected=selectionExists(key)?key:'room';
  properties();draw();
}
function updateControls() {
  if (!loaded) return;
  el('editor-save').disabled=busy||!dirty()||!!polygonError(draft.room);
  el('editor-discard').disabled=busy;
  el('editor-undo').disabled=busy||!undo.length;
  el('editor-redo').disabled=busy||!redo.length;
  el('editor-controls').inert=busy;
  for(const name of ['select','room','pan']) el('editor-'+name).setAttribute('aria-pressed',String(mode===name));
  el('editor-presets').hidden=!roomEdit();
  const error=polygonError(draft.room);
  const outside=[...draft.sensors.map(s=>mountingPoint(s.mounting)),...layouts.flatMap(g=>[g.position,...g.bars.flatMap(b=>[b.start,b.end])])]
    .filter(p=>!pointInside(p,outline(draft.room))).length;
  el('editor-validation').textContent=error?`Invalid outline: ${error} Undo or move the marked corners before saving.`:
    outside?`${outside} device / bar points are outside the floor outline. Their positions are preserved; check your installation.`:
    'Valid outline. Width/depth define the coordinate envelope; the origin never moves.';
  el('editor-validation').classList.toggle('editor-error',!!error);
}
function edited(before, note = 'Unsaved geometry changes.') {
  if (!equal(before,draft)) {
    undo.push(before);if(undo.length>100)undo.shift();redo=[];
    message(note);preview();
  }
  if(!selectionExists(selected))selected='room';
  properties();draw();
}
function change(action) {
  if (!loaded||busy||locked()) return;
  const before=clone(draft);action();edited(before);
}
function restoreHistory(from,to) {
  if(!from.length||busy)return;
  cancelDrag();to.push(clone(draft));draft=from.pop();
  if(!selectionExists(selected))selected='room';
  message(dirty()?'Unsaved geometry changes.':'Back to saved geometry.');
  properties();draw();preview();
}
function addSvg(tag, attrs, text, parent=plan) {
  const node=document.createElementNS(NS,tag);
  for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));
  if(text!==undefined)node.textContent=text;
  parent.append(node);return node;
}
function world(point) {
  return [(point[0]-transform.cx)*transform.scale+width/2+pan[0],
    height/2+pan[1]-(point[1]-transform.cy)*transform.scale];
}
function inverse(point) {
  return [(point[0]-width/2-pan[0])/transform.scale+transform.cx,
    (height/2+pan[1]-point[1])/transform.scale+transform.cy];
}
function local(event) {
  const rect=plan.getBoundingClientRect();return [event.clientX-rect.left,event.clientY-rect.top];
}
function fit() {
  if(!loaded)return;
  layouts=buildLayouts(draft);
  const points=[[0,0],[draft.room.width_mm,draft.room.depth_mm],...outline(draft.room),
    ...draft.sensors.map(s=>mountingPoint(s.mounting)),
    ...layouts.flatMap(g=>[g.position,...g.bars.flatMap(b=>[b.start,b.end])])];
  bounds=[Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1])),Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))];
  zoom=1;pan=[0,0];draw();
}
function line(a,b,color,size=1,key,extra={}) {
  const p=world(a),q=world(b);
  return addSvg('line',{x1:p[0],y1:p[1],x2:q[0],y2:q[1],stroke:color,'stroke-width':size,
    'stroke-linecap':'round',...(key?{'data-key':key}:{}),...extra});
}
function label(point,text,color='#b3c2d5',dy=-16) {
  const p=world(point);addSvg('text',{x:p[0],y:p[1]+dy,fill:color,'font-size':12,
    'text-anchor':'middle','pointer-events':'none',class:'editor-label'},text);
}
function handle(point,key,color,r=7,action) {
  const p=world(point);
  addSvg('circle',{cx:p[0],cy:p[1],r:22,fill:'transparent','data-key':key,...(action?{'data-action':action}:{})});
  addSvg('circle',{cx:p[0],cy:p[1],r,fill:color,stroke:key===selected?'#f3f8fd':'#080f1b','stroke-width':2,'pointer-events':'none'});
}
function draw() {
  if(!loaded)return;
  layouts=buildLayouts(draft);plan.replaceChildren();
  const rect=plan.getBoundingClientRect();width=Math.max(240,rect.width);height=rect.height||600;
  plan.setAttribute('viewBox',`0 0 ${width} ${height}`);
  transform={cx:(bounds[0]+bounds[2])/2,cy:(bounds[1]+bounds[3])/2,
    scale:Math.min((width-100)/Math.max(1,bounds[2]-bounds[0]),(height-100)/Math.max(1,bounds[3]-bounds[1]))*zoom};
  const points=outline(draft.room),error=polygonError(draft.room);
  const path=points.map(p=>world(p).join(',')).join(' ');
  addSvg('polygon',{points:path,fill:'#122a4f',stroke:error?'#ff927a':'#4495d1','stroke-width':2});
  const defs=addSvg('defs',{}),clip=addSvg('clipPath',{id:'editor-floor-clip'},undefined,defs);
  addSvg('polygon',{points:path},undefined,clip);
  // Cap visible grid lines independently of room size and zoom.
  const a=inverse([0,height]),b=inverse([width,0]);
  const grid=Math.max(step(),Math.ceil((b[0]-a[0])/120/step())*step(),Math.ceil((b[1]-a[1])/120/step())*step());
  for(let x=Math.max(0,Math.ceil(a[0]/grid)*grid);x<=Math.min(draft.room.width_mm,b[0]);x+=grid)
    line([x,0],[x,draft.room.depth_mm],'#2c405c',1,null,{'clip-path':'url(#editor-floor-clip)','pointer-events':'none'});
  for(let y=Math.max(0,Math.ceil(a[1]/grid)*grid);y<=Math.min(draft.room.depth_mm,b[1]);y+=grid)
    line([0,y],[draft.room.width_mm,y],'#2c405c',1,null,{'clip-path':'url(#editor-floor-clip)','pointer-events':'none'});
  label([0,0],'Origin · X →  Y ↑','#aae1fa',28);
  for(let i=0;i<points.length;i++) {
    const next=points[(i+1)%points.length],key='wall:'+i,mid=points[i].map((v,j)=>(v+next[j])/2);
    if(roomEdit())line(points[i],next,'transparent',26,key);
    line(points[i],next,error?'#ff927a':selected===key?'#aae1fa':'#4495d1',selected===key?4:2,null,{'pointer-events':'none'});
    if(roomEdit()||selected===key) label(mid,`${i+1} · ${Math.round(Math.hypot(next[0]-points[i][0],next[1]-points[i][1]))} mm`);
  }
  for(const sensor of draft.sensors) {
    const key='sensor:'+sensor.id,p=mountingPoint(sensor.mounting),angle=sensor.mounting.yaw*Math.PI/180;
    const reach=40/transform.scale;
    const tip=[p[0]+Math.sin(angle)*reach,p[1]+Math.cos(angle)*reach];
    line(p,tip,'#aae1fa',2);
    handle(p,key,sensor.enabled?'#4495d1':'#a0a0a0',selected===key?10:7);
    label(p,sensor.name);
    if(selected===key&&!locked())handle(tip,key,'#aae1fa',6,'rotate');
  }
  for(const group of layouts) {
    for(const bar of group.bars) {
      const key=`bar:${group.id}:${bar.id}`;
      line(bar.start,bar.end,'transparent',24,key);
      line(bar.start,bar.end,selected===key?'#aae1fa':'#4495d1',6,null,{'pointer-events':'none'});
      if(selected===key){label(bar.end,bar.id);handle(bar.end,key,'#aae1fa',7,'rotate');}
    }
    const key='controller:'+group.id,p=group.position;
    handle(p,key,group.enabled?'#aae1fa':'#a0a0a0',selected===key?10:7);label(p,group.name);
    if(selected===key&&!locked()) {
      const angle=group.controller.mounting.yaw*Math.PI/180,reach=40/transform.scale;
      const tip=[p[0]-Math.sin(angle)*reach,p[1]-Math.cos(angle)*reach];
      line(p,tip,'#aae1fa',2);handle(tip,key,'#aae1fa',6,'rotate');
    }
  }
  if(roomEdit())for(let i=0;i<points.length;i++){
    handle(points[i],'corner:'+i,error?'#ff927a':'#aae1fa',selected==='corner:'+i?10:7);
    label(points[i],String(i+1),'#f3f8fd',-18);
  }
  if(drag?.action==='move'&&el('editor-snap').checked) {
    const s=selection(),m=s.sensor?.mounting||s.group?.controller.mounting;
    const anchor=s.kind==='corner'?outline(draft.room)[s.index]:m?[m.left_wall,m.front_wall]:null;
    if(anchor){const q=world(anchor);
      addSvg('line',{x1:0,y1:q[1],x2:width,y2:q[1],stroke:'#aae1fa','stroke-dasharray':'4 6',opacity:.5,'pointer-events':'none'});
      addSvg('line',{x1:q[0],y1:0,x2:q[0],y2:height,stroke:'#aae1fa','stroke-dasharray':'4 6',opacity:.5,'pointer-events':'none'});
      label(anchor,`${Math.round(anchor[0])} / ${Math.round(anchor[1])} mm`,'#f3f8fd',32);
    }
  }
  updateControls();
}
function option(value,text) {
  const node=document.createElement('option');node.value=value;node.textContent=text;el('editor-object').append(node);
}
function field(title,value,setter,{min=-1000000,max=1000000,step='any'}={}) {
  const label=document.createElement('label'),input=document.createElement('input');
  label.textContent=title;input.type='number';input.value=value;input.min=min;input.max=max;input.step=step;input.required=true;
  input.addEventListener('change',()=>{
    if(!input.reportValidity()||busy)return;
    const number=Number(input.value);if(!Number.isFinite(number))return;
    change(()=>setter(number));
  });
  label.append(input);fields.append(label);
}
function textField(title,value,setter) {
  const label=document.createElement('label'),input=document.createElement('input');label.textContent=title;
  input.value=value;input.required=true;
  input.addEventListener('change',()=>{if(input.reportValidity())change(()=>setter(input.value));});
  label.append(input);fields.append(label);
}
function properties() {
  if(!loaded)return;
  el('editor-object').replaceChildren();option('room','Room');
  for(const [i] of outline(draft.room).entries()) {option('corner:'+i,`Corner ${i+1}`);option('wall:'+i,`Wall ${i+1}`);}
  for(const sensor of draft.sensors)option('sensor:'+sensor.id,'Sensor · '+sensor.name);
  for(const group of draft.lighting.controllers){option('controller:'+group.id,'Controller · '+group.name);for(const bar of group.bars)option(`bar:${group.id}:${bar.id}`,`${group.name} / ${bar.id}`);}
  el('editor-object').value=selected;el('editor-object').disabled=busy;
  fields.replaceChildren();const s=selection(),blocked=locked();
  fields.disabled=blocked||busy||(['corner','wall'].includes(s.kind)&&!roomEdit());
  el('editor-lock').checked=locks.has(selected);el('editor-lock').disabled=busy;
  el('editor-selection-note').textContent=blocked?'Selection is locked. Unlock it to edit.':
    ['corner','wall'].includes(s.kind)&&!roomEdit()?'Choose Edit room to change corners and walls.':
    s.kind==='bar'?'Drag the endpoint to rotate its joint. Bar length comes from the shared product; descendants follow.':
    ['sensor','controller'].includes(s.kind)?'Drag to move. Drag the pale rotation handle to change yaw. X/Y are offsets from the fixed room origin.':'';
  if(s.kind==='room') {
    textField('Installation name',draft.installation_name,v=>{draft.installation_name=v;});
    for(const [key,title] of [['width_mm','Width envelope · mm'],['depth_mm','Depth envelope · mm']])field(title,draft.room[key],v=>{
      const axis=key==='width_mm'?0:1,old=draft.room[key];
      if(draft.room.outline_mm)draft.room.outline_mm=draft.room.outline_mm.map(p=>p.map((n,i)=>i===axis?n*v/old:n));
      draft.room[key]=v;
    },{min:1});
    field('Ceiling height · mm',draft.room.height_mm,v=>{draft.room.height_mm=v;},{min:1});
  } else if(s.kind==='corner') {
    for(const [axis,title] of ['X · mm','Y · mm'].entries())field(title,outline(draft.room)[s.index][axis],v=>{
      materialize();draft.room.outline_mm[s.index][axis]=v;
    },{min:0,max:axis===0?draft.room.width_mm:draft.room.depth_mm});
  } else if(s.kind==='wall') {
    const a=outline(draft.room)[s.index],b=outline(draft.room)[(s.index+1)%outline(draft.room).length];
    const length=Math.hypot(b[0]-a[0],b[1]-a[1]),angle=Math.atan2(b[1]-a[1],b[0]-a[0])*180/Math.PI;
    field('Length · mm (moves end corner)',Math.round(length*100)/100,v=>moveWallEnd(s.index,v,angle),{min:1});
    field('Angle · degrees from +X',Math.round(angle*100)/100,v=>moveWallEnd(s.index,length,v),{min:-360,max:360});
  } else if(s.kind==='sensor'||s.kind==='controller') {
    const object=s.sensor||s.group,m=s.sensor?.mounting||s.group.controller.mounting;
    textField('Name',object.name,v=>{object.name=v;});
    for(const [key,title] of [['left_wall','X · mm'],['front_wall','Y · mm'],['height','Height · mm'],['yaw','Yaw · degrees'],['pitch','Pitch · degrees'],['roll','Roll · degrees']])field(title,m[key],v=>{m[key]=v;},{min:key==='height'&&s.sensor?1:-1000000});
  } else if(s.bar) {
    for(const key of ['yaw','pitch','roll'])field(key[0].toUpperCase()+key.slice(1)+' · degrees',s.bar[key],v=>{s.bar[key]=v;},{min:-360,max:360});
    const label=document.createElement('label'),select=document.createElement('select');label.textContent='Connected to';
    for(const parent of ['controller',...s.group.bars.filter(b=>b.id!==s.bar.id&&!descendant(s.group,b.id,s.bar.id)).map(b=>b.id)]){
      const op=document.createElement('option');op.value=parent;op.textContent=parent;select.append(op);
    }
    select.value=s.bar.connected_to;select.addEventListener('change',()=>change(()=>{s.bar.connected_to=select.value;}));label.append(select);fields.append(label);
    const p=document.createElement('p');p.className='hint';p.textContent=`Electrical order: ${s.bar.wire_order} · ${s.bar.reversed?'reversed pixels':'forward pixels'}. Wiring and product dimensions can be edited in Geometry → Lights.`;fields.append(p);
  }
  el('editor-split').disabled=blocked||!roomEdit()||s.kind!=='wall'||outline(draft.room).length>=64;
  el('editor-remove').disabled=blocked||!(s.kind==='corner'&&roomEdit()&&outline(draft.room).length>3||s.bar&&s.group.bars.length>1&&!s.group.bars.some(b=>b.connected_to===s.bar.id));
  el('editor-add-bar').disabled=blocked||!s.group||s.group.bars.length>=64;
  updateControls();
}
function descendant(group,id,ancestor) {
  let current=group.bars.find(b=>b.id===id);
  while(current){if(current.connected_to===ancestor)return true;current=group.bars.find(b=>b.id===current.connected_to);}
  return false;
}
function materialize() {if(!draft.room.outline_mm)draft.room.outline_mm=clone(outline(draft.room));}
function moveWallEnd(index,length,angle) {
  materialize();const a=draft.room.outline_mm[index],r=angle*Math.PI/180;
  draft.room.outline_mm[(index+1)%draft.room.outline_mm.length]=[a[0]+Math.cos(r)*length,a[1]+Math.sin(r)*length].map(v=>Math.round(v*100)/100);
}
function snapPoint(point,event) {
  if(!el('editor-snap').checked||event.shiftKey)return point.map(v=>Math.round(v));
  const points=outline(draft.room).filter((_,i)=>selected!=='corner:'+i);
  const result=point.map(v=>Math.round(v/step())*step());
  // Align to nearby corner/device axes only within a visible 10px guide range.
  points.push(...draft.sensors.filter(s=>selected!=='sensor:'+s.id).map(s=>mountingPoint(s.mounting)),
    ...draft.lighting.controllers.filter(g=>selected!=='controller:'+g.id).map(g=>mountingPoint(g.controller.mounting)));
  for(let i=0;i<2;i++){
    const near=points.map(p=>p[i]).filter(v=>Math.abs(v-point[i])*transform.scale<10).sort((a,b)=>Math.abs(a-point[i])-Math.abs(b-point[i]));
    if(near.length)result[i]=near[0];
  }
  return result;
}
function moveSelection(point,event={}) {
  if(locked())return;
  const s=selection(),p=snapPoint(point,event);
  if(s.kind==='corner'&&roomEdit()) {materialize();draft.room.outline_mm[s.index]=p;}
  else if(s.kind==='sensor'||s.kind==='controller'){
    const m=s.sensor?.mounting||s.group.controller.mounting;m.left_wall=p[0];m.front_wall=p[1];
  }
}
function rotateSelection(point,event) {
  if(locked())return;
  const s=selection();let yaw;
  if(s.bar) {
    const layout=layouts.find(g=>g.id===s.group.id).bars.find(b=>b.id===s.bar.id);
    const length=draft.lighting.products.find(p=>p.id===s.group.product_id).length_mm;
    yaw=barYawAt(layout,point,length);
  } else {
    const m=s.sensor?.mounting||s.group?.controller.mounting;if(!m)return;
    const dx=point[0]-m.left_wall,dy=point[1]-m.front_wall;
    yaw=Math.atan2(s.sensor?dx:-dx,s.sensor?dy:-dy)*180/Math.PI;
  }
  if(el('editor-snap').checked&&!event.shiftKey)yaw=Math.round(yaw/15)*15;
  (s.bar||s.sensor?.mounting||s.group.controller.mounting).yaw=normalizedAngle(Math.round(yaw));
}
function cancelDrag() {
  if(drag?.before)draft=drag.before;
  drag=null;gesture=null;
}
function pointerDown(event) {
  if(!loaded||busy||event.button>0)return;
  event.preventDefault();plan.focus();plan.setPointerCapture(event.pointerId);
  pointers.set(event.pointerId,local(event));
  if(pointers.size===2) {
    cancelDrag();const [a,b]=[...pointers.values()];
    gesture={distance:Math.max(1,Math.hypot(a[0]-b[0],a[1]-b[1])),mid:[(a[0]+b[0])/2,(a[1]+b[1])/2],zoom,pan:[...pan]};
    draw();return;
  }
  if(pointers.size>2)return;
  const target=event.target.closest('[data-key]'),key=target?.dataset.key;
  const already=key===selected;
  if(key&&mode!=='pan')choose(key);
  const canMove=key&&mode!=='pan'&&!locked()&&
    (selected.startsWith('corner:')&&roomEdit()||mode==='select'&&/^(sensor|controller|bar):/.test(selected));
  const point=local(event),p=inverse(point),s=selection();
  const m=s.sensor?.mounting||s.group?.controller.mounting;
  const anchor=s.kind==='corner'?outline(draft.room)[s.index]:m?[m.left_wall,m.front_wall]:p;
  drag={id:event.pointerId,start:point,pan:[...pan],before:clone(draft),offset:[anchor[0]-p[0],anchor[1]-p[1]],
    action:canMove&&(event.pointerType!=='touch'||already)?target.dataset.action==='rotate'?'rotate':s.kind==='bar'?'none':'move':key&&mode!=='pan'?'none':'pan'};
}
function pointerMove(event) {
  if(!pointers.has(event.pointerId)||busy)return;
  event.preventDefault();pointers.set(event.pointerId,local(event));
  if(gesture&&pointers.size===2) {
    const [a,b]=[...pointers.values()],mid=[(a[0]+b[0])/2,(a[1]+b[1])/2];
    zoom=Math.max(0.25,Math.min(16,gesture.zoom*Math.hypot(a[0]-b[0],a[1]-b[1])/gesture.distance));
    const factor=zoom/gesture.zoom;
    pan=[0,1].map(i=>mid[i]-[width,height][i]/2-(gesture.mid[i]-[width,height][i]/2-gesture.pan[i])*factor);
    draw();return;
  }
  if(!drag||drag.id!==event.pointerId)return;
  const p=local(event);
  if(drag.action==='pan')pan=pan.map((_,i)=>drag.pan[i]+p[i]-drag.start[i]);
  if(drag.action==='move')moveSelection(inverse(p).map((v,i)=>v+drag.offset[i]),event);
  if(drag.action==='rotate')rotateSelection(inverse(p),event);
  draw();
}
function pointerEnd(event) {
  if(!pointers.has(event.pointerId))return;
  pointers.delete(event.pointerId);
  if(drag?.id===event.pointerId){const before=drag.before;drag=null;edited(before);}
  if(gesture){gesture=null;drag=null;}
  if(plan.hasPointerCapture(event.pointerId))plan.releasePointerCapture(event.pointerId);
}
function magnify(factor,point=[width/2,height/2]) {
  const old=zoom;zoom=Math.max(0.25,Math.min(16,zoom*factor));
  pan=pan.map((v,i)=>point[i]-[width,height][i]/2-(point[i]-[width,height][i]/2-v)*zoom/old);draw();
}
function preview() {
  clearTimeout(previewTimer);const version=++previewVersion;
  el('scene-status').textContent=polygonError(draft.room)?'Invalid outline · showing last valid draft':'Updating draft…';
  if(polygonError(draft.room))return;
  previewTimer=setTimeout(async()=>{
    try {
      const response=await fetch('/api/geometry/draft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:draft}),signal:AbortSignal.timeout(8000)});
      const result=await response.json();if(version!==previewVersion)return;
      if(!response.ok)throw new Error(result.error);
      window.dispatchEvent(new CustomEvent('geometry-preview',{detail:result}));
    } catch(error){if(version===previewVersion)el('scene-status').textContent=`Preview unavailable: ${error.message}`;}
  },150);
}
async function load() {
  if(busy)return;
  busy=true;updateControls();
  try {
    const response=await fetch('/api/config',{cache:'no-store',signal:AbortSignal.timeout(8000)});
    if(!response.ok)throw new Error('Could not read installation');
    base=await response.json();draft=clone(base);loaded=true;selected='room';undo=[];redo=[];locks.clear();
    message('Geometry loaded. Edits stay in this browser until saved.');fit();properties();preview();
  } catch(error){message(error.message);}
  finally{busy=false;el('editor-controls').inert=false;if(loaded){properties();draw();}}
}
async function save() {
  if(!loaded||busy||!dirty()||polygonError(draft.room))return;
  busy=true;updateControls();message('Saving geometry…');
  try {
    const response=await fetch('/api/geometry/draft',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:draft,base_config:base}),signal:AbortSignal.timeout(15000)});
    const result=await response.json();if(!response.ok)throw new Error(result.error);
    base=clone(result.config);draft=clone(base);undo=[];redo=[];
    message('Geometry saved. Dashboard and 3D now use these positions.');
    window.dispatchEvent(new CustomEvent('geometry-preview',{detail:result}));
  } catch(error){message(`Not saved: ${error.message}`);}
  finally{busy=false;properties();draw();}
}
function setMode(value){if(busy)return;mode=value;properties();draw();}
function removeSelected() {
  if(el('editor-remove').disabled)return;
  change(()=>{
    const s=selection();
    if(s.kind==='corner'){materialize();draft.room.outline_mm.splice(s.index,1);selected='room';}
    else if(s.bar){s.group.bars=s.group.bars.filter(b=>b.id!==s.bar.id).sort((a,b)=>a.wire_order-b.wire_order);s.group.bars.forEach((b,i)=>{b.wire_order=i+1;});selected='controller:'+s.group.id;}
  });
}
plan.addEventListener('pointerdown',pointerDown);
plan.addEventListener('pointermove',pointerMove);
plan.addEventListener('pointerup',pointerEnd);
plan.addEventListener('pointercancel',event=>{pointers.delete(event.pointerId);cancelDrag();properties();draw();});
plan.addEventListener('lostpointercapture',event=>{if(pointers.has(event.pointerId)){pointers.delete(event.pointerId);cancelDrag();properties();draw();}});
plan.addEventListener('wheel',event=>{if(!loaded||busy)return;event.preventDefault();magnify(Math.exp(-event.deltaY*0.001),local(event));},{passive:false});
plan.addEventListener('keydown',event=>{
  if(!loaded||busy)return;
  const move={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,1],ArrowDown:[0,-1]}[event.key];
  if(move){event.preventDefault();const s=selection(),m=s.sensor?.mounting||s.group?.controller.mounting;
    if(mode==='pan'){pan[0]+=move[0]*20;pan[1]-=move[1]*20;draw();}
    else if(s.kind==='corner'&&roomEdit()||s.kind==='sensor'||s.kind==='controller'){
      const point=s.kind==='corner'?outline(draft.room)[s.index]:[m.left_wall,m.front_wall];
      change(()=>moveSelection(point.map((v,i)=>v+move[i]*(event.shiftKey?1:step())),{shiftKey:true}));
    }
  } else if(['+','=','-','Home','Delete','Escape'].includes(event.key)){
    event.preventDefault();if(event.key==='Home')fit();else if(event.key==='Delete')removeSelected();
    else if(event.key==='Escape'){cancelDrag();choose('room');}else magnify(event.key==='-'?0.8:1.25);
  }
});
window.addEventListener('keydown',event=>{
  if(!loaded||busy||event.target.matches('input,textarea,select')||!(event.ctrlKey||event.metaKey))return;
  if(event.key.toLowerCase()==='z'){event.preventDefault();if(event.shiftKey)restoreHistory(redo,undo);else restoreHistory(undo,redo);}
  if(event.key.toLowerCase()==='y'){event.preventDefault();restoreHistory(redo,undo);}
});
el('editor-object').addEventListener('change',event=>choose(event.target.value));
el('editor-lock').addEventListener('change',event=>{if(event.target.checked)locks.add(selected);else locks.delete(selected);properties();draw();});
el('editor-properties').addEventListener('submit',event=>event.preventDefault());
for(const name of ['select','room','pan'])el('editor-'+name).addEventListener('click',()=>setMode(name));
el('editor-save').addEventListener('click',save);
el('editor-discard').addEventListener('click',()=>{cancelDrag();load();});
el('editor-undo').addEventListener('click',()=>restoreHistory(undo,redo));
el('editor-redo').addEventListener('click',()=>restoreHistory(redo,undo));
el('editor-fit').addEventListener('click',fit);
el('editor-zoom-in').addEventListener('click',()=>magnify(1.25));
el('editor-zoom-out').addEventListener('click',()=>magnify(0.8));
el('editor-grid').addEventListener('change',()=>{el('editor-grid').value=step();draw();});
el('editor-snap').addEventListener('change',()=>draw());
el('editor-remove').addEventListener('click',removeSelected);
el('editor-split').addEventListener('click',()=>{
  if(el('editor-split').disabled)return;
  change(()=>{materialize();const i=selection().index,a=draft.room.outline_mm[i],b=draft.room.outline_mm[(i+1)%draft.room.outline_mm.length];
    draft.room.outline_mm.splice(i+1,0,a.map((v,j)=>(v+b[j])/2));selected='corner:'+(i+1);});
});
el('editor-add-bar').addEventListener('click',()=>{
  if(el('editor-add-bar').disabled)return;
  change(()=>{const s=selection();let index=1;while(s.group.bars.some(b=>b.id===`bar-${index}`))index++;
    const bar={id:`bar-${index}`,product_id:s.group.product_id,connected_to:s.bar?.id||'controller',wire_order:s.group.bars.length+1,yaw:0,pitch:0,roll:0,reversed:false};
    s.group.bars.push(bar);selected=`bar:${s.group.id}:${bar.id}`;});
});
for(const name of ['rectangle','l-shape'])el('editor-'+name).addEventListener('click',()=>{
  if(!roomEdit()||locks.has('room'))return;
  change(()=>{const w=draft.room.width_mm,d=draft.room.depth_mm;
    draft.room.outline_mm=name==='rectangle'?[[0,0],[w,0],[w,d],[0,d]]:
      [[0,0],[w,0],[w,d/2],[w/2,d/2],[w/2,d],[0,d]];selected='room';});
});
new ResizeObserver(()=>draw()).observe(plan);
window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});
load();
