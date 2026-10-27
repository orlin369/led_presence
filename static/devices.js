(() => {
  const $=id=>document.getElementById(id), form=$('devices-settings');
  let saved, dirty=false;
  const read=(object,path)=>path.split('.').reduce((value,key)=>value[key],object);
  const write=(object,path,value)=>{const keys=path.split('.'),key=keys.pop();keys.reduce((v,k)=>v[k],object)[key]=value;};
  function changed(){dirty=true;$('devices-save-status').textContent='Unsaved device changes.';}
  function input(label,field,value,type='text') {
    const wrapper=document.createElement('label');wrapper.append(label);
    const control=document.createElement('input');control.type=type;control.dataset.deviceField=field;
    if(type==='checkbox'){wrapper.className='checkbox';control.checked=value;}else{control.value=value;control.required=true;}
    if(type==='number'){control.min=field.endsWith('ddp_port')?'1':'0';control.max=field.endsWith('ddp_port')?'65535':'255';}
    wrapper.append(control);return wrapper;
  }
  function row(kind,device) {
    const fieldset=document.createElement('fieldset');fieldset.className='sensor-editor';fieldset.dataset.id=device.id;fieldset._newDevice=structuredClone(device);
    const legend=document.createElement('legend');legend.textContent=device.id;fieldset.append(legend);
    const fields=document.createElement('div');fields.className='fields';
    for(const [label,field,type] of [['Name','name','text'],['IPv4 address',kind==='sensor'?'device_host':'controller.device_host','text']])fields.append(input(label,field,read(device,field),type));
    if(kind==='light') {
      fields.append(input('Component ID','controller.component_id',device.controller.component_id,'number'),input('DDP port','controller.ddp_port',device.controller.ddp_port,'number'));
      const label=document.createElement('label');label.append('Product');const select=document.createElement('select');select.dataset.deviceField='product_id';
      for(const product of saved.lighting.products){const option=document.createElement('option');option.value=product.id;option.textContent=product.name;select.append(option);}
      select.value=device.product_id;label.append(select);fields.append(label);
    }
    fieldset.append(fields,input('Connect to this device','enabled',device.enabled,'checkbox'));
    const status=document.createElement('p');status.className='hint';status.dataset.online='';fieldset.append(status);
    const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='Remove device';
    remove.addEventListener('click',()=>{fieldset.remove();changed();});fieldset.append(remove);
    $(kind==='sensor'?'device-sensors':'device-lights').append(fieldset);
  }
  function populate(config) {
    saved=structuredClone(config);$('device-sensors').replaceChildren();$('device-lights').replaceChildren();
    for(const sensor of config.sensors)row('sensor',sensor);
    for(const light of config.lighting.controllers)row('light',light);
  }
  function nextId(prefix) {
    const ids=[...document.querySelectorAll('.device-list [data-id]')].map(row=>row.dataset.id);
    ids.push(...saved.sensors.map(s=>s.id),...saved.lighting.controllers.map(c=>c.id));
    let index=1;while(ids.includes(`${prefix}-${index}`))index++;return `${prefix}-${index}`;
  }
  $('add-device-sensor').addEventListener('click',()=>{
    if(!saved)return;const id=nextId('sensor');
    row('sensor',{id,name:`Presence ${id.split('-').at(-1)}`,device_host:'',enabled:true,mounting:{height:2000,left_wall:0,front_wall:0,yaw:0,pitch:0,roll:0}});changed();
  });
  $('add-device-light').addEventListener('click',()=>{
    if(!saved)return;const id=nextId('lights'),product=saved.lighting.products[0];
    row('light',{id,name:`Lights ${id.split('-').at(-1)}`,enabled:true,product_id:product.id,
      controller:{device_host:'',component_id:0,ddp_port:4048,mounting:{height:0,left_wall:0,front_wall:0,yaw:0,pitch:0,roll:0}},
      bars:Array.from({length:9},(_,index)=>({id:`bar-${index+1}`,product_id:product.id,connected_to:index?`bar-${index}`:'controller',wire_order:index+1,yaw:index?0:-90,pitch:0,roll:0,reversed:false})),
      animation:{mode:'aura',brightness_pct:35,radius_mm:1800,speed:.6,fade_seconds:.6,idle_pct:0}});changed();
  });
  form.addEventListener('input',changed);
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    try {
      const current=await fetch('/api/config',{cache:'no-store'});if(!current.ok)throw new Error('Could not read installation');const config=await current.json();
      function collect(id,existing,kind){return [...$(id).children].map(row=>{
        const device=structuredClone(existing.find(d=>d.id===row.dataset.id)||row._newDevice);
        for(const field of row.querySelectorAll('[data-device-field]'))write(device,field.dataset.deviceField,field.type==='checkbox'?field.checked:field.type==='number'?Number(field.value):field.value);
        if(kind==='light')for(const bar of device.bars)bar.product_id=device.product_id;
        return device;
      });}
      config.sensors=collect('device-sensors',config.sensors,'sensor');
      config.lighting.controllers=collect('device-lights',config.lighting.controllers,'light');
      const response=await fetch('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
      const result=await response.json();if(!response.ok)throw new Error(result.error);
      dirty=false;populate(result);$('devices-save-status').textContent='Devices saved.';
    }catch(error){$('devices-save-status').textContent='Could not save: '+error.message;}
  });
  async function poll(){
    try {
      const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(10000)});if(!response.ok)throw new Error('App unavailable');const state=await response.json();
      if(!saved||(!dirty&&JSON.stringify(saved)!==JSON.stringify(state.config)))populate(state.config);
      if(window.LightingUI)window.LightingUI.update(state);
      for(const [id,statuses] of [['device-sensors',state.sensors],['device-lights',state.lighting.controllers]])for(const row of $(id).children){
        const device=statuses.find(d=>d.id===row.dataset.id);
        row.querySelector('[data-online]').textContent=!device?'Not saved yet':!device.enabled?'Disabled':(device.connected??device.controller_connected)?'Connected':'Offline / reconnecting';
      }
    }catch(error){if(!saved)$('devices-save-status').textContent=error.message;}
    setTimeout(poll,1000);
  }
  poll();
})();
