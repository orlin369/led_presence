(() => {
  const el=id=>document.getElementById(id), form=el('lighting-settings');
  const hasBarEditors=!!el('bar-editors'),selector=el('light-controller');
  let collection,selectedId;const drafts=new Map();
  let saved, editing=false, frame, receivedAt=0, lastState, busy=false, outputMessage='';
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
      add('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,stroke:'#3a5260','stroke-width':18,'stroke-linecap':'round'});
      const pixels=bar.reversed?[...bar.pixels].reverse():bar.pixels;
      for(let i=0;i<pixels.length;i++){
        const u=i/pixels.length,v=(i+1)/pixels.length;
        add('line',{x1:a.x+(b.x-a.x)*u,y1:a.y+(b.y-a.y)*u,x2:a.x+(b.x-a.x)*v,y2:a.y+(b.y-a.y)*v,stroke:fresh?rgb(pixels[i]):'#26363d','stroke-width':11,'stroke-linecap':'round'});
      }
      add('text',{x:(a.x+b.x)/2,y:(a.y+b.y)/2-18,fill:'#d2e9ec','font-size':11,'text-anchor':'middle'},`${bar.id} · #${bar.wire_order}`);
      add('circle',{cx:b.x,cy:b.y,r:3,fill:'#92aab5'});
    }
    const root=point(frame.controller);add('rect',{x:root.x-10,y:root.y-10,width:20,height:20,rx:4,fill:'#b7ed86'});
    add('text',{x:root.x,y:root.y+32,fill:'#b7ed86','font-size':12,'text-anchor':'middle'},'CONTROLLER / START');
    add('text',{x:24,y:30,fill:'#829ca6','font-size':11},elevation?'FRONT ELEVATION · horizontal / height · mm':'TOP VIEW · LEFT REFERENCE WALL / FRONT REFERENCE WALL · mm');
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
    const container=el('controller-status');container.replaceChildren();
    for(const group of controllers){const card=document.createElement('article'),heading=document.createElement('h3'),status=document.createElement('p');
      heading.textContent=group.name;status.className='hint';status.textContent=!group.enabled?'Disabled':group.output_enabled?'Output active':group.controller_connected?'Online / preview only':'Offline / reconnecting';
      card.append(heading,status);container.append(card);}
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
