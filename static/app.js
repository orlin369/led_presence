const $ = id => document.getElementById(id);
const form = $('settings');
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
  ['front', 'From front reference wall'],
  ['facing-sensor', 'Facing the selected sensor (my view)'],
  ['opposite', 'From opposite wall'],
  ['left', 'From left reference wall'],
  ['right', 'From right wall'],
]) {
  const option = document.createElement('option');
  option.value = value; option.textContent = text; viewpoint.append(option);
}
try { viewpoint.value = localStorage.getItem('led-presence-viewpoint') || 'front'; } catch {}
if (!viewpoint.value) viewpoint.value = 'front';
const viewControl = document.createElement('label');
viewControl.className = 'view-control'; viewControl.htmlFor = 'viewpoint';
viewControl.append('Viewpoint', viewpoint);
$('map').before(viewControl);
$('map').closest('.panel').querySelector('.hint').textContent = 'Top view · millimetres · wall names and measurements stay fixed as the view rotates';
viewpoint.addEventListener('change', () => {
  try { localStorage.setItem('led-presence-viewpoint', viewpoint.value); } catch {}
  if (latest) draw(latest);
});

function viewAngle(c) {
  if (viewpoint.value === 'facing-sensor') {
    const sensor = c.sensors.find(s => s.id === selectedSensor);
    return sensor ? 180 - sensor.mounting.yaw : 180;
  }
  return {front:0, opposite:180, left:90, right:-90}[viewpoint.value] ?? 0;
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
  if (!c.sensors.some(s => s.id === selectedSensor)) selectedSensor = c.sensors[0]?.id;
  for (const name of ['installation_name', 'room.width_mm', 'room.depth_mm', 'room.height_mm', 'physical.notes', 'calibrated']) {
    const el = form.elements.namedItem(name);
    const value = name.split('.').reduce((o, key) => o[key], c);
    if (el.type === 'checkbox') el.checked = value; else el.value = value;
  }
  renderEditors(c.sensors);
}

function renderEditors(sensors) {
  $('sensor-editors').replaceChildren();
  for (const sensor of sensors) {
    const editor = $('sensor-template').content.firstElementChild.cloneNode(true);
    editor.dataset.id = sensor.id;
    editor.querySelector('legend').textContent = sensor.id;
    const radio = editor.querySelector('input[type=radio]');
    radio.value = sensor.id; radio.checked = selectedSensor === sensor.id;
    radio.addEventListener('change', () => { selectedSensor = sensor.id; if (latest) draw(latest); });
    for (const input of editor.querySelectorAll('[data-field]')) {
      if (input.type === 'checkbox') input.checked = readPath(sensor,input.dataset.field);
      else input.value = readPath(sensor,input.dataset.field);
    }
    editor.querySelector('.remove-sensor').addEventListener('click', () => {
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
  for (const name of ['installation_name', 'room.width_mm', 'room.depth_mm', 'room.height_mm', 'physical.notes', 'calibrated']) {
    const el = form.elements.namedItem(name), keys = name.split('.'), key = keys.pop();
    const parent = keys.reduce((o, k) => o[k], c);
    parent[key] = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) : el.value;
  }
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
      room_x_mm:sensor.mounting.left_wall+p.x_mm*Math.cos(angle)-p.y_mm*Math.sin(angle),
      room_y_mm:sensor.mounting.front_wall+p.x_mm*Math.sin(angle)+p.y_mm*Math.cos(angle)}];
  });
}

function draw(state) {
  const c = dirty ? draft() : state.config, room = c.room;
  if (!(room.width_mm > 0 && room.depth_mm > 0)) return;
  const tracks = previewTracks(state, c);
  const corners = [{x:0,y:0},{x:room.width_mm,y:0},{x:room.width_mm,y:room.depth_mm},{x:0,y:room.depth_mm}];
  const points = [...corners, ...c.sensors.map(s=>({x:s.mounting.left_wall,y:s.mounting.front_wall})),
    ...tracks.map(t=>({x:t.room_x_mm,y:t.room_y_mm}))];
  if (!points.every(p=>Number.isFinite(p.x)&&Number.isFinite(p.y))) return;
  const degrees = viewAngle(c), projection = mapProjection(points,degrees);
  mapBounds = projection;
  const point = (x,y) => projection.point(x,y);
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
    const p=point(x,y), normal=projection.direction(dx,dy);
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
  label(room.width_mm/2,room.depth_mm,0,1,32,'OPPOSITE WALL',true);
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
  if (!config || (!dirty && JSON.stringify(config) !== JSON.stringify(state.config))) populate(state.config);
  $('name').textContent = state.config.installation_name;
  const active=state.sensors.filter(s=>s.enabled), online=active.filter(s=>s.connected);
  $('connection').textContent=`${online.length} / ${active.length} sensors connected`;
  $('connection').className=online.length===active.length && active.length?'':'error';
  $('calibration').textContent=state.config.calibrated?'Geometry verified':'Uncalibrated geometry';
  const fresh=online.filter(s=>!s.stale);
  $('count').textContent=fresh.length?state.tracks.length:'—';
  $('online').textContent=`${online.length} / ${active.length}`;
  $('freshness').textContent=`${fresh.length} with fresh tracking data`;
  const occupied=online.some(s=>s.zones.some(z=>z.occupied));
  const complete=active.length && online.length===active.length && online.every(s=>s.zones.length && s.zones.every(z=>typeof z.occupied==='boolean'));
  $('occupancy').textContent=occupied?'Occupied':complete?'Clear':'Unknown';
  $('coverage').textContent=complete?'All enabled sensors reporting':'Partial sensor coverage';
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
  if(!state.sensors.length)$('sensor-status').textContent='No sensors configured. Add a sensor in the installation form.';
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
  draw(state);
}

function changed(){dirty=true;$('save-status').textContent='Unsaved changes.';if(latest)draw(latest);}
form.addEventListener('input',event=>{if(event.target.name!=='selected-sensor')changed();});
$('add-sensor').addEventListener('click',()=>{
  if(!config)return;
  const c=draft();
  if(c.sensors.length>=32){$('save-status').textContent='Maximum 32 sensors.';return;}
  let index=1;while(c.sensors.some(s=>s.id===`sensor-${index}`))index++;
  const id=`sensor-${index}`;
  c.sensors.push({id,name:`Presence ${index}`,device_host:'',enabled:true,mounting:{height:2000,left_wall:0,front_wall:0,yaw:0,pitch:0,roll:0}});
  selectedSensor=id;renderEditors(c.sensors);changed();
});
$('map').addEventListener('click',event=>{
  if(!mapBounds||!config||!selectedSensor)return;
  const editor=[...$('sensor-editors').children].find(el=>el.dataset.id===selectedSensor);if(!editor)return;
  const point=$('map').createSVGPoint();point.x=event.clientX;point.y=event.clientY;
  const p=point.matrixTransform($('map').getScreenCTM().inverse());
  const roomPoint=mapBounds.inverse(p.x,p.y);
  editor.querySelector('[data-field="mounting.left_wall"]').value=Math.round(roomPoint.x);
  editor.querySelector('[data-field="mounting.front_wall"]').value=Math.round(roomPoint.y);changed();
});
form.addEventListener('submit',async event=>{
  event.preventDefault();
  try {
    const response=await fetch('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(draft())});
    const result=await response.json();if(!response.ok)throw new Error(result.error);
    populate(result);dirty=false;$('save-status').textContent='Installation saved on this machine.';await poll();
  }catch(error){$('save-status').textContent='Could not save: '+error.message;}
});
async function poll(){
  try{const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok)throw new Error('App unavailable');render(await response.json());}
  catch(error){if(latest)render({...latest,tracks:[],sensors:latest.sensors.map(s=>({...s,connected:false,stale:true}))});$('connection').textContent='App connection lost';$('connection').className='error';}
}
async function loop(){await poll();setTimeout(loop,1000);}loop();
