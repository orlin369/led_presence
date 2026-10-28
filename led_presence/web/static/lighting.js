(() => {
  const el=id=>document.getElementById(id), form=el('lighting-settings');
  const hasBarEditors=!!el('bar-editors'),selector=el('light-controller');
  let collection,selectedId;const drafts=new Map();
  let saved, editing=false, frame, receivedAt=0, lastState, busy=false, outputMessage='';
  const brightnessControls=new Map();
  const read=(o,path)=>path.split('.').reduce((value,key)=>value[key],o);
  const write=(o,path,value)=>{const keys=path.split('.'),key=keys.pop();keys.reduce((v,k)=>v[k],o)[key]=value;};
  const rgb=color=>`rgb(${color.join(',')})`;

  function normalized(group) {
    return {controller:structuredClone(group.controller),bars:structuredClone(group.bars),animation:structuredClone(group.animation),
      product:structuredClone(collection.products.find(p=>p.id===group.product_id))};
  }
  function choose(id) {
    if(saved&&editing&&selectedId)drafts.set(selectedId,draft());
    selectedId=id;try{localStorage.setItem('led-presence-controller',id);}catch{}
    const group=collection.controllers.find(g=>g.id===id);if(!group)return;
    const cached=drafts.get(id);populate(cached||normalized(group));editing=!!cached;outputMessage='';
    el('lighting-save-status').textContent=cached?'Unsaved changes for this controller.':'';
    frame=lastState?.lighting.controllers.find(g=>g.id===id);drawPreview();status();
  }
  selector.addEventListener('change',()=>choose(selector.value));
  function populate(config) {
    saved=structuredClone(config);
    for(const input of form.querySelectorAll('[data-light]')) {
      const value=read(config,input.dataset.light);
      if(input.type==='checkbox')input.checked=value;else input.value=value;
    }
    if(hasBarEditors)renderRows(config.bars);
  }
  function renderRows(bars) {
    el('bar-editors').replaceChildren();
    for(const bar of bars) {
      const row=document.createElement('tr');row.dataset.id=bar.id;
      const name=document.createElement('td');name.textContent=bar.id;row.append(name);
      const parent=document.createElement('select');parent.dataset.bar='connected_to';parent.setAttribute('aria-label',`${bar.id} connected to`);
      for(const id of ['controller',...bars.filter(b=>b.id!==bar.id).map(b=>b.id)]){
        const option=document.createElement('option');option.value=id;option.textContent=id;parent.append(option);
      }
      parent.value=bar.connected_to;const cell=document.createElement('td');cell.append(parent);row.append(cell);
      for(const field of ['wire_order','yaw','pitch','roll','reversed']) {
        const td=document.createElement('td'),input=document.createElement('input');input.dataset.bar=field;
        input.setAttribute('aria-label',`${bar.id} ${field}`);input.setAttribute('form','lighting-settings');
        if(field==='reversed'){input.type='checkbox';input.checked=bar[field];}
        else{input.type='number';input.required=true;input.step=field==='wire_order'?'1':'any';input.min=field==='wire_order'?'1':'-360';input.max=field==='wire_order'?String(bars.length):'360';input.value=bar[field];}
        td.append(input);row.append(td);
      }
      const td=document.createElement('td'),remove=document.createElement('button');remove.type='button';remove.textContent='Remove';remove.className='secondary';
      remove.disabled=bars.length===1||bars.some(b=>b.connected_to===bar.id);
      remove.title=remove.disabled?'Reconnect downstream bars before removing this bar':'';
      remove.addEventListener('click',()=>{
        const config=draft();config.bars=config.bars.filter(b=>b.id!==bar.id).sort((a,b)=>a.wire_order-b.wire_order);
        config.bars.forEach((b,i)=>b.wire_order=i+1);renderRows(config.bars);changed();
      });td.append(remove);row.append(td);el('bar-editors').append(row);
    }
  }
  function draft() {
    const config=structuredClone(saved);
    for(const input of form.querySelectorAll('[data-light]'))write(config,input.dataset.light,input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value);
    if(hasBarEditors) config.bars=[...el('bar-editors').children].map(row=>{
      const bar=structuredClone(saved.bars.find(b=>b.id===row.dataset.id)||{id:row.dataset.id,product_id:config.product.id});
      for(const input of row.querySelectorAll('[data-bar]'))bar[input.dataset.bar]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;
      return bar;
    });return config;
  }
  function changed(){editing=true;el('lighting-save-status').textContent='Unsaved changes. Saving applies them and stops active lighting output.';}
  form?.addEventListener('input',changed);el('bar-editors')?.addEventListener('input',changed);
  el('add-bar')?.addEventListener('click',()=>{
    if(!saved)return;const config=draft();if(config.bars.length>=64)return;
    let i=1;while(config.bars.some(b=>b.id===`bar-${i}`))i++;
    config.bars.push({id:`bar-${i}`,product_id:config.product.id,connected_to:config.bars.at(-1)?.id||'controller',wire_order:config.bars.length+1,yaw:0,pitch:0,roll:0,reversed:false});renderRows(config.bars);changed();
  });
  form?.addEventListener('submit',async event=>{
    event.preventDefault();
    try{
      const current=await fetch('/api/config',{cache:'no-store'});if(!current.ok)throw new Error('Could not read installation');
      const config=await current.json(),edited=draft();
      const group=config.lighting.controllers.find(g=>g.id===selectedId);
      if(!group)throw new Error('This controller was removed on Devices');
      if(hasBarEditors) {
        if(group.product_id!==edited.product.id)throw new Error('Product changed on Devices; reload the geometry before saving');
        group.controller.mounting=edited.controller.mounting;group.bars=edited.bars;
        const product=config.lighting.products.find(p=>p.id===group.product_id);
        for(const key of ['length_mm','pixels_per_bar','verified'])product[key]=edited.product[key];
      } else group.animation=edited.animation;
      const response=await fetch('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)});
      const result=await response.json();if(!response.ok)throw new Error(result.error);
      collection=result.lighting;populate(normalized(collection.controllers.find(g=>g.id===selectedId)));editing=false;drafts.delete(selectedId);outputMessage='';el('lighting-save-status').textContent=hasBarEditors?'Light geometry saved.':'Animation saved.';
      await poll();
    }catch(error){el('lighting-save-status').textContent='Could not save: '+error.message;}
  });

  function drawPreview() {
    if(!frame)return;
    const svg=el('lighting-preview');svg.replaceChildren();
    const elevation=el('lighting-view').value==='front';
    const flatten=p=>({x:p[0],y:p[elevation?2:1]});
    const points=[flatten(frame.controller),...frame.bars.flatMap(b=>[flatten(b.start),flatten(b.end)])];
    const minX=Math.min(...points.map(p=>p.x)),maxX=Math.max(...points.map(p=>p.x));
    const minY=Math.min(...points.map(p=>p.y)),maxY=Math.max(...points.map(p=>p.y));
    const scale=Math.min(520/Math.max(maxX-minX,300),320/Math.max(maxY-minY,300));
    const point=p=>{const v=flatten(p);return{x:320+(v.x-(minX+maxX)/2)*scale,y:230+(elevation?-1:1)*(v.y-(minY+maxY)/2)*scale};};
    function add(tag,attrs,text){const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))node.setAttribute(k,v);if(text!==undefined)node.textContent=text;svg.append(node);return node;}
    const fresh=Date.now()-receivedAt<2000;
    for(const bar of frame.bars){
      const a=point(bar.start),b=point(bar.end);
      add('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#577394','stroke-width':18,'stroke-linecap':'round'});
      const pixels=bar.reversed?[...bar.pixels].reverse():bar.pixels;
      for(let i=0;i<pixels.length;i++){
        const u=i/pixels.length,v=(i+1)/pixels.length;
        add('line',{x1:a.x+(b.x-a.x)*u,y1:a.y+(b.y-a.y)*u,x2:a.x+(b.x-a.x)*v,y2:a.y+(b.y-a.y)*v,stroke:fresh?rgb(pixels[i]):'#182d49','stroke-width':11,'stroke-linecap':'round'});
      }
      add('text',{x:(a.x+b.x)/2,y:(a.y+b.y)/2-18,fill:'#f3f8fd','font-size':11,'text-anchor':'middle'},`${bar.id} · #${bar.wire_order}`);
      add('circle',{cx:b.x,cy:b.y,r:3,fill:'#b3c2d5'});
    }
    const root=point(frame.controller);add('rect',{x:root.x-10,y:root.y-10,width:20,height:20,rx:4,fill:'#4495d1'});
    add('text',{x:root.x,y:root.y+32,fill:'#4495d1','font-size':12,'text-anchor':'middle'},'CONTROLLER / START');
    add('text',{x:24,y:30,fill:'#b3c2d5','font-size':11},elevation?'FRONT ELEVATION · horizontal / height · mm':'TOP VIEW · LEFT REFERENCE WALL / FRONT REFERENCE WALL · mm');
  }
  function status() {
    if(!frame)return;
    el('lighting-status').textContent=frame.output_enabled?'Physical output active':'Software preview only';
    const notes=[`${frame.bars.length} bars · ${frame.mapped_pixels} mapped pixels · controller ${frame.controller_connected?'online':'offline'}`];
    if(frame.controller_pixels!==null)notes.push(`Controller configured for ${frame.controller_pixels} pixels.`);
    if(!frame.product_verified)notes.push('Example geometry: verify length and pixels per bar before output.');
    if(frame.controller_pixels!==null && frame.controller_pixels!==frame.mapped_pixels)notes.push('Pixel mapping does not match the controller.');
    if(frame.mapping_mode==='position-program')notes.splice(0,notes.length,`Position-following animation · ${frame.controller_pixels} configured LEDs · ${frame.program_mapping_estimated?'estimated LED positions along the bar geometry; verify pixels per bar for precise mapping':'verified LED positions'}.`);
    el('lighting-preview-note').textContent=notes.join(' ');
    if(el('lighting-start'))el('lighting-start').disabled=busy||!frame.enabled||frame.output_enabled||!frame.product_verified||!frame.mapped_pixels||!frame.controller_connected||frame.mapped_pixels!==frame.controller_pixels;
    if(el('lighting-stop'))el('lighting-stop').disabled=busy;
    if(busy)return;
    if(outputMessage)el('lighting-output-message').textContent=outputMessage;
    else if(frame.error)el('lighting-output-message').textContent=frame.error;
    else el('lighting-output-message').textContent=frame.output_enabled?`Streaming presence animation · ${frame.frames_sent} frames sent`:'Hardware output is stopped. The preview does not change the lights.';
  }
  async function output(enabled) {
    if(enabled&&editing){el('lighting-output-message').textContent='Save the lighting settings before starting output.';return;}
    outputMessage='';busy=true;status();el('lighting-output-message').textContent=enabled?'Starting presence lighting...':'Restoring previous effect...';
    try{
      const response=await fetch('/api/lighting/output',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled,controller_id:selectedId})});
      const result=await response.json();if(!response.ok)throw new Error(result.error);
      frame=result.controllers.find(g=>g.id===selectedId);receivedAt=Date.now();busy=false;status();
    }catch(error){busy=false;outputMessage=error.message;status();}
  }
  el('lighting-start')?.addEventListener('click',()=>output(true));el('lighting-stop')?.addEventListener('click',()=>output(false));
  el('lighting-view').addEventListener('change',drawPreview);
  async function poll(){
    try{
      const response=await fetch('/api/lighting',{cache:'no-store',signal:AbortSignal.timeout(6000)});if(!response.ok)throw new Error('Lighting API unavailable');
      const result=await response.json();frame=result.controllers.find(g=>g.id===selectedId);receivedAt=Date.now();drawPreview();status();
      if(lastState&&typeof draw==='function'){lastState.lighting=result;draw(lastState);}
      renderControllerStatus(result.controllers);
    }catch(error){el('lighting-status').textContent='Lighting connection lost';if(el('lighting-start'))el('lighting-start').disabled=true;drawPreview();}
  }
  async function loop(){await poll();setTimeout(loop,100);}loop();
  function renderControllerStatus(controllers) {
    const container=el('controller-status');
    for(const [id,control] of brightnessControls){
      if(!controllers.some(group=>group.id===id)){
        clearTimeout(control.timer);control.card.remove();brightnessControls.delete(id);
      }
    }
    for(const group of controllers){
      let control=brightnessControls.get(group.id);
      if(!control){
        const card=document.createElement('article'),heading=document.createElement('h3'),status=document.createElement('p');
        status.className='hint';card.append(heading,status);
        control={card,heading,status,pending:null,busy:false,dragging:false,error:'',timer:null};
        if(!hasBarEditors){
          const label=document.createElement('label'),caption=document.createElement('span'),value=document.createElement('output'),slider=document.createElement('input'),message=document.createElement('p');
          label.className='device-brightness';slider.type='range';slider.min='0';slider.max='100';slider.step='1';slider.id=`brightness-${group.id}`;slider.dataset.controllerBrightness=group.id;
          label.htmlFor=slider.id;value.htmlFor=slider.id;caption.append('Brightness ',value);label.append(caption,slider);message.className='hint';message.setAttribute('role','status');card.append(label,message);
          Object.assign(control,{slider,value,message});
          slider.addEventListener('pointerdown',()=>{control.dragging=true;});
          for(const event of ['pointerup','pointercancel','lostpointercapture','blur'])slider.addEventListener(event,()=>{
            control.dragging=false;clearTimeout(control.timer);saveDeviceBrightness(group.id);
          });
          slider.addEventListener('input',()=>{
            control.pending=Number(slider.value);value.value=`${slider.value}%`;slider.setAttribute('aria-valuetext',value.value);
            clearTimeout(control.timer);control.timer=setTimeout(()=>saveDeviceBrightness(group.id),120);
          });
          slider.addEventListener('change',()=>{clearTimeout(control.timer);saveDeviceBrightness(group.id);});
        }
        brightnessControls.set(group.id,control);container.append(card);
      }
      control.heading.textContent=group.name;control.status.textContent=!group.enabled?'Disabled':group.output_enabled?'Output active':group.controller_connected?'Online':'Offline / reconnecting';
      if(control.slider){
        control.slider.setAttribute('aria-label',`${group.name} brightness`);
        const disabled=!group.enabled||!group.controller_connected;
        if(control.slider.disabled!==disabled)control.slider.disabled=disabled;
        if(!control.dragging&&!control.busy&&control.pending===null){
          const value=String(group.brightness_pct??25);
          if(control.slider.value!==value)control.slider.value=value;
          control.value.value=`${control.slider.value}%`;control.slider.setAttribute('aria-valuetext',control.value.value);
        }
        control.message.textContent=control.error;
      }
    }
  }
  async function saveDeviceBrightness(id){
    const control=brightnessControls.get(id);if(!control||control.busy||control.pending===null)return;
    const value=control.pending;control.pending=null;control.busy=true;control.error='';
    try{
      const response=await fetch('/api/lighting/brightness',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({controller_id:id,brightness_pct:value})});
      const result=await response.json();if(!response.ok)throw new Error(result.error);
      const group=lastState?.lighting.controllers.find(g=>g.id===id);if(group)group.brightness_pct=result.brightness_pct;
    }catch(error){control.error=error.message;}
    finally{
      control.busy=false;
      if(brightnessControls.get(id)!==control)return;
      if(control.pending!==null)saveDeviceBrightness(id);else await poll();
    }
  }
  window.LightingUI={update(state){
    lastState=state;collection=state.config.lighting;
    for(const id of drafts.keys())if(!collection.controllers.some(g=>g.id===id))drafts.delete(id);
    const options=collection.controllers.map(g=>[g.id,g.name]);
    if(JSON.stringify(options)!==selector.dataset.options){
      selector.replaceChildren();for(const[id,name]of options){const option=document.createElement('option');option.value=id;option.textContent=name;selector.append(option);}selector.dataset.options=JSON.stringify(options);
    }
    if(!selectedId){try{selectedId=localStorage.getItem('led-presence-controller');}catch{}}
    if(!collection.controllers.some(g=>g.id===selectedId)){selectedId=collection.controllers[0]?.id;saved=undefined;editing=false;}
    selector.value=selectedId||'';
    el('no-light-controllers').hidden=!!selectedId;form.hidden=!selectedId;
    document.querySelector('.lighting-save button').disabled=!selectedId;
    if(!selectedId){frame=undefined;el('lighting-preview').replaceChildren();el('lighting-status').textContent='No light controllers';
      if(el('bar-editors'))el('bar-editors').replaceChildren();
      for(const id of ['lighting-start','lighting-stop'])if(el(id))el(id).disabled=true;
      renderControllerStatus([]);return;
    }
    const group=collection.controllers.find(g=>g.id===selectedId),config=normalized(group);
    if(!saved||(!editing&&JSON.stringify(saved)!==JSON.stringify(config)))populate(config);
    if(state.lighting){frame=state.lighting.controllers.find(g=>g.id===selectedId);receivedAt=Date.now();drawPreview();status();renderControllerStatus(state.lighting.controllers);}
  }};
})();
