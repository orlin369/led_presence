const $ = id => document.getElementById(id);
const form = $('settings'), roomForm = $('room-settings');
let roomDirty=false, sensorDirty=false;
const roomFieldNames=['installation_name','room.width_mm','room.depth_mm','room.height_mm','physical.notes','calibrated'];
const sensorsTabActive=()=>!!form&&!$('geometry-panel-sensors')?.hidden;
const palette = ['#b7ed86', '#6cdbec', '#f2b57d', '#c9a1ff', '#ff94bc', '#f0de7d'];
let config, latest, dirty = false, mapBounds, selectedSensor;
const readPath = (object, path) => path.split('.').reduce((value, key) => value[key], object);
function writePath(object, path, value) {
  const keys=path.split('.'), key=keys.pop();
  const parent=keys.reduce((value, part) => value[part] ??= {}, object);
  parent[key]=value;
}
const num = value => Number.isFinite(value) ? Math.round(value).toLocaleString() : '—';
const color = (id, c) => palette[Math.max(0, c.sensors.findIndex(s => s.id === id)) % palette.length];

const viewpoint = document.createElement('select');
viewpoint.id = 'viewpoint';
for (const [value, text] of [
  ['facing-sensor', 'FACING THE SELECTED SENSOR'],
  ['front', 'FROM FRONT REFERENCE WALL'],
  ['back', 'FROM BACK WALL'],
  ['left', 'FROM LEFT REFERENCE WALL'],
  ['right', 'FROM RIGHT WALL'],
]) {
  const option = document.createElement('option');
  option.value = value; option.textContent = text; viewpoint.append(option);
}
viewpoint.value = 'facing-sensor';
try { viewpoint.value = localStorage.getItem('led-presence-viewpoint-v2') || 'facing-sensor'; } catch {}
if (!viewpoint.value) viewpoint.value = 'facing-sensor';
const viewControl = document.createElement('label');
viewControl.className = 'view-control'; viewControl.htmlFor = 'viewpoint';
viewControl.append('Viewpoint', viewpoint);
$('map').before(viewControl);
const sensorSelect = document.createElement('select');
sensorSelect.id = 'map-sensor';
const sensorControl = document.createElement('label');
sensorControl.className = 'view-control'; sensorControl.htmlFor = 'map-sensor';
sensorControl.append('Reference sensor', sensorSelect);
$('map').before(sensorControl);
let animationState={running:false},animationBusy=false,animationAction='',animationMessage='';
let animationToggle,animationStatus,animationSelect;
if(!form) {
  const controls=document.createElement('div');controls.className='sensor-program-control';
  sensorControl.replaceWith(controls);sensorControl.className='sensor-picker';
  animationToggle=document.createElement('button');animationToggle.id='animation-toggle';animationToggle.type='button';animationToggle.textContent='Start animation';animationToggle.disabled=true;
  animationStatus=document.createElement('p');animationStatus.className='hint';animationStatus.id='animation-status';animationStatus.setAttribute('role','status');
  animationSelect=document.createElement('select');animationSelect.id='animation-select';animationSelect.disabled=true;
  const animationLabel=document.createElement('label');animationLabel.className='animation-picker';animationLabel.htmlFor='animation-select';animationLabel.append('Animation',animationSelect);
  controls.append(sensorControl,animationToggle,animationLabel,animationStatus);
  animationToggle.addEventListener('click',()=>applyAnimation(!(animationState.running||animationState.restoration_pending?.length)));
  animationSelect.addEventListener('change',()=>{
    try{localStorage.setItem('led-presence-animation',animationSelect.value);}catch{}
    if(animationState.running)applyAnimation(true);
    else if(latest)renderAnimationProgram(latest);
  });
  async function applyAnimation(enabled) {
    animationBusy=true;animationAction=enabled?(animationState.running?'Changing animation...':'Starting...'):'Stopping...';animationMessage='';
    if(latest)renderAnimationProgram(latest);
    try {
      const response=await fetch('/api/animation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled,sensor_id:selectedSensor,animation_id:animationSelect.value})});
      const result=await response.json();if(!response.ok)throw new Error(result.error);
      animationState=result;await poll();
    }catch(error){animationMessage=error.message;}
    finally{animationBusy=false;if(latest)renderAnimationProgram(latest);}
  }
}
function renderAnimationProgram(state) {
  if(!animationToggle)return;
  animationState=state.lighting?.program||{running:false};
  const animations=animationState.animations||[];
  if(!animationSelect.options.length&&animations.length){
    for(const item of animations){const option=document.createElement('option');option.value=item.id;option.textContent=item.name;animationSelect.append(option);}
    let preferred='spotlight';try{preferred=localStorage.getItem('led-presence-animation')||preferred;}catch{}
    animationSelect.value=animations.some(a=>a.id===preferred)?preferred:animations[0].id;
  }
  if(animationState.running&&!animationBusy)animationSelect.value=animationState.animation_id;
  animationSelect.disabled=animationBusy||!!state.app_offline||!animations.length||!!animationState.restoration_pending?.length;
  const selectedAnimation=animations.find(a=>a.id===animationSelect.value);
  animationSelect.title=selectedAnimation?.description||'';
  const active=animationState.running||animationState.restoration_pending?.length;
  if(active&&animationState.sensor_id){selectedSensor=animationState.sensor_id;sensorSelect.value=selectedSensor;}
  const sensor=state.sensors.find(s=>s.id===selectedSensor);
  sensorSelect.disabled=!!active||animationBusy;
  animationToggle.textContent=animationBusy?animationAction:active?'Stop animation':'Start animation';
  animationToggle.setAttribute('aria-pressed',String(!!animationState.running));
  const hasLights=state.config.lighting.controllers.some(c=>c.enabled);
  animationToggle.disabled=!!state.app_offline||animationBusy||(!active&&(!sensor?.connected||!hasLights));
  animationStatus.textContent=state.app_offline?'App connection lost; animation status unavailable.':animationMessage||animationState.error||(animationBusy?animationAction:active?
    `Running from ${sensor?.name||'sensor'} · ${animationState.controller_ids.length} controller(s)${sensor?.connected?'':' · sensor offline, fading out'}`:
    !hasLights?'Enable a light controller on Devices.':!sensor?.connected?'Waiting for the selected sensor.':selectedAnimation?.description||'Ready · uses this sensor and all enabled light controllers.');
}
sensorSelect.addEventListener('change', () => {
  animationMessage='';
  selectedSensor = sensorSelect.value;
  if(form) for(const radio of form.querySelectorAll('input[name="selected-sensor"]')) radio.checked=radio.value===selectedSensor;
  if(latest){renderAnimationProgram(latest);draw(latest);}
});
$('map').closest('.panel').querySelector('.hint').textContent = 'Top view · millimetres · wall names and measurements stay fixed as the view rotates';
viewpoint.addEventListener('change', () => {
  try { localStorage.setItem('led-presence-viewpoint-v2', viewpoint.value); } catch {}
  if (latest) draw(latest);
});

function viewAngle(c) {
  if (viewpoint.value === 'facing-sensor') {
    const sensor = c.sensors.find(s => s.id === selectedSensor);
    return sensor ? 180 - sensor.mounting.yaw : 180;
  }
  return {front:0, back:180, left:-90, right:90}[viewpoint.value] ?? 180;
}

function mapProjection(points, degrees) {
  const angle = degrees * Math.PI / 180, cos = Math.cos(angle), sin = Math.sin(angle);
  const rotate = (x, y) => ({x:x*cos-y*sin, y:x*sin+y*cos});
  const rotated = points.map(p => rotate(p.x,p.y));
  const minX = Math.min(...rotated.map(p=>p.x)), maxX = Math.max(...rotated.map(p=>p.x));
  const minY = Math.min(...rotated.map(p=>p.y)), maxY = Math.max(...rotated.map(p=>p.y));
  const scale = 520 / Math.max(maxX-minX,maxY-minY,1);
  const left = (640-(maxX-minX)*scale)/2, top = (640-(maxY-minY)*scale)/2;
  return {
    point(x,y) { const p=rotate(x,y); return {x:left+(p.x-minX)*scale,y:top+(maxY-p.y)*scale}; },
    inverse(x,y) {
      const rx=(x-left)/scale+minX, ry=maxY-(y-top)/scale;
      return {x:rx*cos+ry*sin,y:-rx*sin+ry*cos};
    },
    direction(x,y) { const p=rotate(x,y); return {x:p.x,y:-p.y}; },
  };
}

function populate(c) {
  config = structuredClone(c);
  const sensors=sensorDirty?draft().sensors:c.sensors;
  if (!sensors.some(s => s.id === selectedSensor)) selectedSensor = sensors[0]?.id;
  sensorSelect.replaceChildren();
  for(const sensor of sensors) {
    const option=document.createElement('option');option.value=sensor.id;option.textContent=sensor.name;sensorSelect.append(option);
  }
  sensorSelect.value=selectedSensor||'';
  if(roomForm&&!roomDirty) for(const name of roomFieldNames) {
    const input=roomForm.elements.namedItem(name),value=readPath(c,name);
    if(input.type==='checkbox')input.checked=value;else input.value=value;
  }
  if(form&&!sensorDirty)renderEditors(c.sensors);
}

function renderEditors(sensors) {
  $('sensor-editors').replaceChildren();
  for (const sensor of sensors) {
    const editor = $('sensor-template').content.firstElementChild.cloneNode(true);
    editor.dataset.id = sensor.id;
    editor.querySelector('legend').textContent = `${sensor.name} (${sensor.id})`;
    const radio = editor.querySelector('input[type=radio]');
    radio.value = sensor.id; radio.checked = selectedSensor === sensor.id;
    radio.addEventListener('change', () => { selectedSensor = sensor.id; sensorSelect.value=sensor.id; if (latest) draw(latest); });
    for (const input of editor.querySelectorAll('[data-field]')) {
      if (input.type === 'checkbox') input.checked = readPath(sensor,input.dataset.field);
      else input.value = readPath(sensor,input.dataset.field);
    }
    editor.querySelector('.remove-sensor')?.addEventListener('click', () => {
      editor.remove();
      if (selectedSensor === sensor.id) {
        selectedSensor = $('sensor-editors').firstElementChild?.dataset.id;
        const next = $('sensor-editors').querySelector('input[type=radio]');
        if (next) next.checked = true;
      }
      changed();
    });
    $('sensor-editors').append(editor);
  }
}

function draft() {
  const c = structuredClone(config);
  if(roomForm) for(const name of roomFieldNames) {
    const input=roomForm.elements.namedItem(name);
    writePath(c,name,input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value);
  }
  if(!form)return c;
  c.sensors = [...$('sensor-editors').children].map(editor => {
    const sensor = structuredClone(config.sensors.find(s => s.id === editor.dataset.id) || {id: editor.dataset.id});
    for (const input of editor.querySelectorAll('[data-field]')) {
      writePath(sensor,input.dataset.field,input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value);
    }
    return sensor;
  });
  return c;
}

function previewTracks(state, c) {
  return state.tracks.flatMap(track => {
    const sensor = c.sensors.find(s => s.id === track.sensor_id && s.enabled);
    const saved = state.config.sensors.find(s => s.id === track.sensor_id);
    if (!sensor || sensor.device_host !== saved?.device_host) return [];
    const angle = sensor.mounting.yaw * Math.PI / 180, p = track.reported_mm;
    return [{...track, sensor_name:sensor.name,
      room_x_mm:sensor.mounting.left_wall-p.x_mm*Math.cos(angle)+p.y_mm*Math.sin(angle),
      room_y_mm:sensor.mounting.front_wall+p.x_mm*Math.sin(angle)+p.y_mm*Math.cos(angle)}];
  });
}

function draw(state) {
  const c = dirty ? draft() : state.config, room = c.room;
  if (!(room.width_mm > 0 && room.depth_mm > 0)) return;
  const tracks = previewTracks(state, c);
  const corners = [{x:0,y:0},{x:room.width_mm,y:0},{x:room.width_mm,y:room.depth_mm},{x:0,y:room.depth_mm}];
  const lighting = state.lighting;
  const lightPoints = lighting ? [...lighting.controllers.map(c=>c.controller),...lighting.bars.flatMap(b=>[b.start,b.end])].map(p=>({x:p[0],y:p[1]})) : [];
  const points = [...corners, ...c.sensors.map(s=>({x:s.mounting.left_wall,y:s.mounting.front_wall})),
    ...tracks.map(t=>({x:t.room_x_mm,y:t.room_y_mm})),...lightPoints];
  if (!points.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y))) return;
  const degrees = viewAngle(c);
  // Convert viewer-reference coordinates into the existing camera projection.
  const projection = mapProjection(points.map(p=>({x:room.width_mm-p.x,y:p.y})),degrees);
  const point = (x,y) => projection.point(room.width_mm-x,y);
  mapBounds = {point,inverse(x,y){const p=projection.inverse(x,y);return{x:room.width_mm-p.x,y:p.y};}};
  const map = $('map'); map.replaceChildren();
  function add(tag, attrs, text) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    if (text !== undefined) el.textContent = text;
    map.append(el); return el;
  }
  function line(x1,y1,x2,y2) {
    const a=point(x1,y1), b=point(x2,y2);
    add('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#2c4148'});
  }
  function label(x,y,dx,dy,offset,text,wall=false) {
    const p=point(x,y), normal=projection.direction(-dx,dy);
    const tx=p.x+normal.x*offset, ty=p.y+normal.y*offset;
    let rotation=wall?Math.atan2(-normal.x,normal.y)*180/Math.PI:0;
    if(rotation>90)rotation-=180;
    if(rotation < -90)rotation+=180;
    add('text',{x:tx,y:ty,fill:wall?'#b4c6ca':'#8aa4ad','font-size':wall?10:10,
      'text-anchor':'middle','dominant-baseline':'middle',transform:`rotate(${rotation} ${tx} ${ty})`},text);
  }
  add('polygon',{points:corners.map(p=>{const v=point(p.x,p.y);return `${v.x},${v.y}`;}).join(' '),fill:'#192b30',stroke:'#68808a'});
  label(room.width_mm/2,0,0,-1,44,'FRONT REFERENCE WALL',true);
  label(0,room.depth_mm/2,-1,0,44,'LEFT REFERENCE WALL',true);
  label(room.width_mm/2,room.depth_mm,0,1,32,'BACK WALL',true);
  label(room.width_mm,room.depth_mm/2,1,0,32,'RIGHT WALL',true);
  const step = Math.max(1, Math.ceil(Math.max(room.width_mm,room.depth_mm)/10000)*1000);
  for (let x=0; x<=room.width_mm; x+=step) {
    line(x,0,x,room.depth_mm);
    label(x,0,0,-1,20,x);
  }
  for (let y=0; y<=room.depth_mm; y+=step) {
    line(0,y,room.width_mm,y);
    label(0,y,-1,0,20,y);
  }
  if(lighting) {
    for(const bar of lighting.bars) {
      const a=point(bar.start[0],bar.start[1]),b=point(bar.end[0],bar.end[1]);
      add('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#536873','stroke-width':10,'stroke-linecap':'round'});
      add('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:`rgb(${bar.color.join(',')})`,'stroke-width':6,'stroke-linecap':'round'});
      add('text',{x:(a.x+b.x)/2,y:(a.y+b.y)/2+18,fill:'#a8c6cf','font-size':10,'text-anchor':'middle'},`${bar.controller_name}: ${bar.id}`);
    }
    for(const controller of lighting.controllers){
    const root=point(controller.controller[0],controller.controller[1]);
    add('rect',{x:root.x-6,y:root.y-6,width:12,height:12,rx:2,fill:'#8eb4c6'});
    add('text',{x:root.x+10,y:root.y-10,fill:'#8eb4c6','font-size':10},controller.name);
    }
  }
  for (const s of c.sensors) {
    const fill = color(s.id,c);
    const p=point(s.mounting.left_wall,s.mounting.front_wall);
    if (s.id === selectedSensor) add('circle',{cx:p.x,cy:p.y,r:22,fill:'none',stroke:fill,'stroke-dasharray':'3 4'});
    add('path',{d:'M 0 -15 L 10 10 L 0 5 L -10 10 Z',fill,opacity:s.enabled?1:0.35,transform:`translate(${p.x} ${p.y}) rotate(${-s.mounting.yaw-degrees})`});
    add('text',{x:p.x+16,y:p.y-20,fill,'font-size':11},s.name);
  }
  for (const t of tracks) {
    const fill = color(t.sensor_id,c);
    const p=point(t.room_x_mm,t.room_y_mm);
    const outside=t.room_x_mm<0||t.room_y_mm<0||t.room_x_mm>room.width_mm||t.room_y_mm>room.depth_mm;
    add('circle',{cx:p.x,cy:p.y,r:16,fill:fill+'22',stroke:fill,'stroke-dasharray':outside?'4 3':'none'});
    add('circle',{cx:p.x,cy:p.y,r:5,fill});
    add('text',{x:p.x+20,y:p.y-12,fill,'font-size':11},`${t.sensor_name} #${t.id}`);
  }
  $('legend').replaceChildren();
  for (const sensor of c.sensors) {
    const el=document.createElement('span');el.style.color=color(sensor.id,c);
    el.textContent=`▲ / ● ${sensor.name}${sensor.enabled?'':' (disabled)'}`;$('legend').append(el);
  }
  $('map-note').textContent=dirty?'Unsaved geometry preview. Save to apply changes to the installation.':'Each colour identifies one sensor and its tracks. Dashed tracks are outside the room. Stale positions are hidden.';
}

function render(state) {
  latest = state;
  if (!config || JSON.stringify(config) !== JSON.stringify(state.config)) populate(state.config);
  renderAnimationProgram(state);
  const active=state.sensors.filter(s=>s.enabled), online=active.filter(s=>s.connected);
  if($('connection')) {
    $('connection').textContent=`${online.length} / ${active.length} sensors connected`;
    $('connection').className=online.length===active.length && active.length?'':'error';
  }
  $('calibration').textContent=state.config.calibrated?'Geometry verified':'Uncalibrated geometry';
  if($('count')) {
  const fresh=online.filter(s=>!s.stale);
  $('count').textContent=fresh.length?state.tracks.length:'—';
  $('online').textContent=`${online.length} / ${active.length}`;
  $('freshness').textContent=`${fresh.length} with fresh tracking data`;
  const occupied=online.some(s=>s.zones.some(z=>z.occupied));
  const complete=active.length && online.length===active.length && online.every(s=>s.zones.length && s.zones.every(z=>typeof z.occupied==='boolean'));
  $('occupancy').textContent=occupied?'Occupied':complete?'Clear':'Unknown';
  $('coverage').textContent=complete?'All enabled sensors reporting':'Partial sensor coverage';
  $('tracks').replaceChildren();
  for (const t of state.tracks) {
    const row=document.createElement('tr'), p=t.reported_mm;
    for(const text of [`${t.sensor_name} / #${t.id}`,`${num(t.room_x_mm)} / ${num(t.room_y_mm)}`,`${num(p.x_mm)} / ${num(p.y_mm)} / ${num(p.z_mm)}`,`${num(p.minz_mm)} / ${num(p.maxz_mm)}`]) {
      const cell=document.createElement('td');cell.textContent=text;row.append(cell);
    }
    $('tracks').append(row);
  }
  if(!state.tracks.length){
    const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=4;
    cell.textContent=fresh.length?'No objects currently tracked.':'No fresh tracking data available.';row.append(cell);$('tracks').append(row);
  }
  $('raw').textContent=JSON.stringify({units:state.units,sensors:state.sensors},null,2);
  }
  if($('sensor-status')) {
  $('sensor-status').replaceChildren();
  for (const sensor of state.sensors) {
    const card=document.createElement('article');
    const heading=document.createElement('h3');heading.textContent=sensor.name;heading.style.color=color(sensor.id,state.config);card.append(heading);
    const lines=[`${sensor.device_host} · ${!sensor.enabled?'Disabled':sensor.connected?'Connected':'Offline'}`,
      sensor.connected?(sensor.stale?'Waiting for fresh tracks':`${sensor.tracks.length} live tracks`):sensor.error||'Waiting for connection'];
    if(sensor.connected) {
      lines.push(`Light: ${sensor.status['illuminance:0']?.illumination??'—'} · Wi-Fi: ${sensor.status.wifi?.rssi??'—'} dBm`);
      lines.push(`Device mount: ${num(sensor.mounting.height_mm)} mm · tilt ${sensor.mounting.tilt_deg??'—'}°`);
    }
    for(const text of lines){const p=document.createElement('p');p.className='hint';p.textContent=text;card.append(p);}
    $('sensor-status').append(card);
  }
  if(!state.sensors.length)$('sensor-status').textContent='No sensors configured. Add a sensor on the Geometry page.';
  }
  if(window.LightingUI) window.LightingUI.update(state);
  draw(state);
}

function changed(){sensorDirty=true;dirty=true;$('save-status').textContent='Unsaved sensor changes.';if(latest)draw(latest);}
roomForm?.addEventListener('input',()=>{roomDirty=true;dirty=true;$('room-save-status').textContent='Unsaved room changes.';if(latest)draw(latest);});
form?.addEventListener('input',event=>{if(event.target.name!=='selected-sensor')changed();});
$('add-sensor')?.addEventListener('click',()=>{
  if(!config)return;
  const c=draft();
  if(c.sensors.length>=32){$('save-status').textContent='Maximum 32 sensors.';return;}
  let index=1;while(c.sensors.some(s=>s.id===`sensor-${index}`))index++;
  const id=`sensor-${index}`;
  c.sensors.push({id,name:`Presence ${index}`,device_host:'',enabled:true,mounting:{height:2000,left_wall:0,front_wall:0,yaw:0,pitch:0,roll:0}});
  selectedSensor=id;renderEditors(c.sensors);changed();
});
$('map').addEventListener('click',event=>{
  if(!sensorsTabActive()||!mapBounds||!config||!selectedSensor)return;
  const editor=[...$('sensor-editors').children].find(el=>el.dataset.id===selectedSensor);if(!editor)return;
  const point=$('map').createSVGPoint();point.x=event.clientX;point.y=event.clientY;
  const p=point.matrixTransform($('map').getScreenCTM().inverse());
  const roomPoint=mapBounds.inverse(p.x,p.y);
  editor.querySelector('[data-field="mounting.left_wall"]').value=Math.round(roomPoint.x);
  editor.querySelector('[data-field="mounting.front_wall"]').value=Math.round(roomPoint.y);changed();
});
async function saveGeometry(scope) {
  const status=scope==='room'?$('room-save-status'):$('save-status');
  try {
    const current=await fetch('/api/config',{cache:'no-store'});
    if(!current.ok)throw new Error('Could not read installation');
    const merged=await current.json(),edited=draft();
    const keys=scope==='room'?['installation_name','room','physical','calibrated']:['sensors'];
    for(const key of keys){
      if(key==='sensors')for(const sensor of merged.sensors){const draftSensor=edited.sensors.find(s=>s.id===sensor.id);if(draftSensor)sensor.mounting=draftSensor.mounting;}
      else merged[key]=edited[key];
    }
    const response=await fetch('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(merged)});
    const result=await response.json();if(!response.ok)throw new Error(result.error);
    if(scope==='room')roomDirty=false;else sensorDirty=false;
    dirty=roomDirty||sensorDirty;populate(result);
    status.textContent=scope==='room'?'Room saved.':'Sensors saved.';
    await poll();
  }catch(error){status.textContent='Could not save: '+error.message;}
}
form?.addEventListener('submit',event=>{event.preventDefault();saveGeometry('sensors');});
roomForm?.addEventListener('submit',event=>{event.preventDefault();saveGeometry('room');});

if(roomForm) {
  const tabs=[...document.querySelectorAll('.geometry-tabs [role=tab]')];
  function activateGeometryTab(name,updateHash=false) {
    if(!['room','sensors','lights'].includes(name))name='room';
    for(const tab of tabs) {
      const active=tab.id===`geometry-tab-${name}`;
      tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;
      $(tab.getAttribute('aria-controls')).hidden=!active;
    }
    $('room-sensor-workspace').hidden=name==='lights';
    $('map').style.cursor=name==='sensors'?'crosshair':'default';
    $('geometry-placement-note').textContent=name==='sensors'?'Select a sensor and click the plan to edit its mounting position.':'Room preview. Open the Sensors tab to place a sensor.';
    if(updateHash)history.replaceState(null,'',`#${name}`);
  }
  for(const [index,tab] of tabs.entries()) {
    tab.addEventListener('click',()=>activateGeometryTab(tab.id.replace('geometry-tab-',''),true));
    tab.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();
      const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
      tabs[next].focus();tabs[next].click();
    });
  }
  window.addEventListener('hashchange',()=>activateGeometryTab(location.hash.slice(1)));
  activateGeometryTab(location.hash.slice(1));
}

async function poll(){
  try{const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok)throw new Error('App unavailable');render(await response.json());}
  catch(error){if(latest)render({...latest,app_offline:true,tracks:[],sensors:latest.sensors.map(s=>({...s,connected:false,stale:true}))});if($('connection')){$('connection').textContent='App connection lost';$('connection').className='error';}}
}
async function loop(){await poll();setTimeout(loop,1000);}loop();
