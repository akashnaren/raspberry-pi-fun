let target='auto';
let sending=false;
const DEFAULT_MODEL=window.MESH_DEFAULT_MODEL||'qwen2.5:0.5b';
const thread=[]; // {role, content, meta?}

function el(tag,cls,text){const e=document.createElement(tag); if(cls)e.className=cls; if(text!=null)e.textContent=text; return e;}
function friendlyNet(e){
  const s=String(e&&e.message||e||'');
  if(/Load failed|Failed to fetch|NetworkError|network|abort|AbortError/i.test(s))
    return 'Connection dropped (Wi‑Fi blip or server busy). Tap Retry — stay on home Wi‑Fi.';
  return s||'Unknown error';
}
function currentModel(){
  const s=document.getElementById('modelSel');
  return (s&&s.value)||DEFAULT_MODEL;
}
function hideEmpty(){const e=document.getElementById('empty'); if(e)e.remove();}

function setSettingsOpen(on){
  document.getElementById('ioPanel').classList.toggle('open', !!on);
  document.getElementById('overlay').classList.toggle('open', !!on);
}

/* ---- Markdown + LaTeX ---- */
function renderMd(text){
  if(text==null) text='';
  const maths=[];
  const stash=(display, src)=>{
    const i=maths.length;
    maths.push({display, src});
    return '@@MATH'+i+'@@';
  };
  let s=String(text)
    .replace(/\$\$([\s\S]+?)\$\$/g,(_,m)=>stash(true,m))
    .replace(/\\\[([\s\S]+?)\\\]/g,(_,m)=>stash(true,m))
    .replace(/\\\(([\s\S]+?)\\\)/g,(_,m)=>stash(false,m))
    .replace(/\$([^\$\n]+?)\$/g,(_,m)=>stash(false,m));
  let html;
  try{
    if(typeof marked!=='undefined'){
      marked.setOptions({gfm:true, breaks:true});
      html=marked.parse(s);
    } else {
      html=s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
  }catch(_){
    html=s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }
  if(typeof DOMPurify!=='undefined'){
    html=DOMPurify.sanitize(html,{USE_PROFILES:{html:true}});
  }
  html=html.replace(/@@MATH(\d+)@@/g,(_,idx)=>{
    const m=maths[+idx];
    if(!m) return '';
    try{
      if(typeof katex!=='undefined'){
        return katex.renderToString(m.src.trim(),{displayMode:m.display, throwOnError:false, output:'html'});
      }
    }catch(_){}
    return m.src;
  });
  return html;
}
function setBodyContent(node, text, asMd){
  if(asMd){
    node.classList.add('md');
    node.innerHTML=renderMd(text);
  } else {
    node.classList.remove('md');
    node.textContent=text;
  }
}

async function fetchJson(url, opts, retries){
  retries = retries==null?2:retries;
  let last;
  for(let i=0;i<=retries;i++){
    try{
      const ctrl=new AbortController();
      const to=setTimeout(()=>ctrl.abort(), 180000);
      const r=await fetch(url, Object.assign({}, opts||{}, {signal:ctrl.signal, cache:'no-store'}));
      clearTimeout(to);
      const text=await r.text();
      let j={};
      try{j=text?JSON.parse(text):{};}catch(_){j={raw:text};}
      return {r,j};
    }catch(e){
      last=e;
      if(i<retries){ await new Promise(res=>setTimeout(res, 600*(i+1))); continue; }
      throw last;
    }
  }
  throw last;
}

function fillModels(peers){
  const sel=document.getElementById('modelSel');
  const prev=sel.value||DEFAULT_MODEL;
  const set=new Set();
  set.add(DEFAULT_MODEL);
  (peers||[]).forEach(p=>{(p.models||[]).forEach(m=>{ if(m) set.add(m); });});
  const list=[...set].sort((a,b)=>{
    if(a===DEFAULT_MODEL) return -1;
    if(b===DEFAULT_MODEL) return 1;
    return a.localeCompare(b);
  });
  sel.innerHTML='';
  list.forEach(m=>{
    const o=document.createElement('option'); o.value=m; o.textContent=m; sel.appendChild(o);
  });
  if(list.includes(prev)) sel.value=prev; else sel.value=DEFAULT_MODEL;
}

function fillPeers(peers){
  const sel=document.getElementById('peerSel');
  const meshOn=document.getElementById('mesh').checked;
  const prev=target;
  sel.innerHTML='';
  if(meshOn){
    const o=document.createElement('option'); o.value='auto'; o.textContent='Auto'; sel.appendChild(o);
  } else if(target==='auto'){
    target=(peers&&peers[0]&&peers[0].name)||'pi3';
  }
  let up=0;
  (peers||[]).forEach(p=>{
    if(p.ok) up++;
    const o=document.createElement('option');
    o.value=p.name;
    const kind=p.kind==='llamacpp'?'llama':(p.kind||'ollama');
    o.textContent=p.name+(p.ok?'':' (off)')+' · '+kind;
    o.disabled=!p.ok && !meshOn;
    o.title=(p.models||[]).join(', ')||p.note||p.kind||'';
    sel.appendChild(o);
  });
  const values=[...sel.options].map(x=>x.value);
  if(values.includes(prev)) sel.value=prev;
  else if(meshOn){ sel.value='auto'; target='auto'; }
  else if(values.length){ sel.value=values[0]; target=values[0]; }
  return up;
}

async function refresh(){
  try{
    const {r,j}=await fetchJson('/peers', {method:'GET'}, 1);
    if(!r.ok) throw new Error('peers '+r.status);
    document.getElementById('banner').className='';
    const up=fillPeers(j.peers);
    fillModels(j.peers);
    document.getElementById('status').textContent=
      currentModel()+' · '+up+'/'+(j.peers||[]).length+' up';
  }catch(e){
    const b=document.getElementById('banner');
    b.textContent='pi-pair offline from this phone — '+friendlyNet(e);
    b.className='on';
  }
}

function addMsg(role,text,meta,extraClass){
  hideEmpty();
  const d=el('div','msg '+role+(extraClass?(' '+extraClass):''));
  const body=el('div','body');
  if(role==='bot') setBodyContent(body, text, true);
  else body.textContent=text;
  d.appendChild(body);
  if(meta){
    const m=el('div','meta');
    if(meta.peer){ const b=el('span','badge',meta.peer); m.appendChild(b); }
    const bits=[];
    if(meta.model) bits.push(meta.model);
    if(meta.ms!=null) bits.push(meta.ms+' ms');
    if(meta.kind) bits.push(meta.kind);
    if(meta.extra) bits.push(meta.extra);
    if(bits.length){
      if(meta.peer) m.appendChild(document.createTextNode(' · '));
      m.appendChild(document.createTextNode(bits.join(' · ')));
    }
    d.appendChild(m);
  }
  if(role==='bot' || role==='user'){
    const acts=el('div','msg-actions');
    const copy=el('button','mini','Copy');
    copy.onclick=async()=>{
      try{ await navigator.clipboard.writeText(text); copy.textContent='Copied'; setTimeout(()=>copy.textContent='Copy',1200);}
      catch(_){ copy.textContent='Fail'; }
    };
    acts.appendChild(copy);
    if(role==='bot' && meta && meta.raw){
      const rawBtn=el('button','mini','Raw');
      const rawBox=el('pre','raw-box', typeof meta.raw==='string'?meta.raw:JSON.stringify(meta.raw,null,2));
      rawBtn.onclick=()=>{ rawBox.classList.toggle('on'); };
      acts.appendChild(rawBtn);
      d.appendChild(acts);
      d.appendChild(rawBox);
    } else {
      d.appendChild(acts);
    }
  }
  document.getElementById('log').appendChild(d);
  d.scrollIntoView({block:'end', behavior:'smooth'});
  return d;
}

function addThink(){
  hideEmpty();
  const d=el('div','msg think');
  const head=el('div','th-head');
  head.appendChild(el('span','pulse'));
  head.appendChild(el('span','th-title','Thinking'));
  const chev=el('span','th-chev','▾');
  head.appendChild(chev);
  d.appendChild(head);
  const steps=el('div','steps'); d.appendChild(steps);
  head.onclick=()=>d.classList.toggle('collapsed');
  document.getElementById('log').appendChild(d); d.scrollIntoView({block:'end'});
  return {root:d, steps, add(label, detail){
    const row=el('div','step');
    row.appendChild(el('span','t',new Date().toLocaleTimeString()));
    row.appendChild(el('span','',label+(detail?(' — '+detail):'')));
    steps.appendChild(row); d.scrollIntoView({block:'end'});
  }, done(){
    d.classList.add('collapsed');
    const t=head.querySelector('.th-title'); if(t) t.textContent='Tools';
    const p=head.querySelector('.pulse'); if(p) p.style.animation='none';
  }};
}

function addLiveBot(){
  hideEmpty();
  const d=el('div','msg bot streaming');
  const body=el('div','body',''); d.appendChild(body);
  const meta=el('div','meta'); d.appendChild(meta);
  const acts=el('div','msg-actions');
  const copy=el('button','mini','Copy');
  acts.appendChild(copy);
  d.appendChild(acts);
  document.getElementById('log').appendChild(d);
  d.scrollIntoView({block:'end'});
  return {
    root:d, body, meta, acts, copy,
    setText(t){ body.classList.remove('md'); body.textContent=t; d.scrollIntoView({block:'end'}); },
    finish(text, m, raw){
      d.classList.remove('streaming');
      setBodyContent(body, text, true);
      meta.innerHTML='';
      if(m && m.peer){ const b=el('span','badge',m.peer); meta.appendChild(b); }
      const bits=[];
      if(m && m.model) bits.push(m.model);
      if(m && m.ms!=null) bits.push(m.ms+' ms');
      if(m && m.kind) bits.push(m.kind);
      if(m && m.extra) bits.push(m.extra);
      if(bits.length){
        if(m && m.peer) meta.appendChild(document.createTextNode(' · '));
        meta.appendChild(document.createTextNode(bits.join(' · ')));
      }
      copy.onclick=async()=>{
        try{ await navigator.clipboard.writeText(text); copy.textContent='Copied'; setTimeout(()=>copy.textContent='Copy',1200);}
        catch(_){ copy.textContent='Fail'; }
      };
      if(raw){
        const rawBtn=el('button','mini','Raw');
        const rawBox=el('pre','raw-box', typeof raw==='string'?raw:JSON.stringify(raw,null,2));
        rawBtn.onclick=()=>{ rawBox.classList.toggle('on'); };
        acts.appendChild(rawBtn);
        d.appendChild(rawBox);
      }
      d.scrollIntoView({block:'end', behavior:'smooth'});
    },
    markErr(){ d.classList.add('err'); d.classList.remove('streaming'); }
  };
}

async function sendText(t, isRetry){
  if(sending) return;
  sending=true;
  const go=document.getElementById('go'); go.disabled=true;
  if(!isRetry){
    thread.push({role:'user', content:t});
    addMsg('user', t);
  }
  const think=addThink();
  const mesh=document.getElementById('mesh').checked?'on':'off';
  const model=currentModel();
  const temp=parseFloat(document.getElementById('temp').value)||0.7;
  const maxt=parseInt(document.getElementById('maxt').value,10)||256;
  think.add('tool: /peers', 'refresh fleet health');
  try{ await refresh(); think.add('fleet', document.getElementById('status').textContent); }
  catch(e){ think.add('fleet', friendlyNet(e)); }
  think.add('plan', mesh==='on'
    ?(target==='auto'?'Mesh Auto — round-robin healthy peers':'Mesh pin → '+target)
    :('Direct pin → '+target));
  think.add('params', 'temp '+temp+' · max_tokens '+maxt+' · model '+model);
  think.add('tool: chat', 'via mesh proxy (SSE stream)');
  const t0=performance.now();
  try{
    const body={
      model:model,
      messages:[],
      stream:true,
      temperature:temp,
      max_tokens:maxt,
      pi_target:target,
      pi_mesh:mesh
    };
    const sys=(document.getElementById('sys').value||'').trim();
    if(sys) body.messages.push({role:'system', content:sys});
    thread.forEach(x=>{ if(x.role==='user'||x.role==='assistant') body.messages.push({role:x.role, content:x.content}); });

    let r=null, lastErr=null;
    for(let i=0;i<=2;i++){
      try{
        const ctrl=new AbortController();
        const to=setTimeout(()=>ctrl.abort(), 180000);
        r=await fetch('/v1/chat/completions',{
          method:'POST',
          headers:{'content-type':'application/json','X-Pi-Target':target,'X-Pi-Mesh':mesh},
          body:JSON.stringify(body),
          signal:ctrl.signal,
          cache:'no-store'
        });
        clearTimeout(to);
        lastErr=null;
        break;
      }catch(e){
        lastErr=e;
        if(i<2) await new Promise(res=>setTimeout(res, 600*(i+1)));
      }
    }
    if(lastErr) throw lastErr;

    const ct=(r.headers.get('content-type')||'').toLowerCase();
    const isSSE=ct.includes('event-stream');

    if(!isSSE){
      const textBody=await r.text();
      let j={};
      try{j=textBody?JSON.parse(textBody):{};}catch(_){j={raw:textBody};}
      const ms=Math.round(performance.now()-t0);
      if(!r.ok){
        const msg=j.error||JSON.stringify(j)||('HTTP '+r.status);
        think.add('error', msg);
        think.done();
        const bot=addMsg('bot', msg, {extra:'error', raw:j}, 'err');
        const btn=el('button','retry','Retry');
        btn.onclick=()=>{bot.remove(); think.root.remove(); sendText(t,true);};
        bot.appendChild(btn);
        return;
      }
      const peer=j.pi_peer||r.headers.get('X-Pi-Peer')||'?';
      const used=j.pi_model||model;
      const msServer=j.pi_ms!=null?j.pi_ms:ms;
      const kind=j.pi_kind||'';
      think.add('routed', 'peer '+peer+' · '+msServer+' ms server · '+ms+' ms wall');
      think.add('done', 'assistant reply (json)');
      think.done();
      const text=(j.choices&&j.choices[0]&&j.choices[0].message&&j.choices[0].message.content)||JSON.stringify(j);
      thread.push({role:'assistant', content:text, meta:{peer, model:used, ms:msServer, kind}});
      addMsg('bot', text, {peer, model:used, ms:msServer, kind, raw:j});
      return;
    }

    let peer=r.headers.get('X-Pi-Peer')||'?';
    let used=model, kind='', msServer=null;
    think.add('stream', 'SSE live from '+peer);
    const live=addLiveBot();
    let textAccum='';
    let lastChunk=null;
    const reader=r.body.getReader();
    const dec=new TextDecoder();
    let buf='';
    let streamDone=false;
    let streamErr=null;
    while(!streamDone){
      const {value, done}=await reader.read();
      if(done) break;
      buf+=dec.decode(value,{stream:true});
      let nl;
      while((nl=buf.indexOf('\n'))>=0){
        let line=buf.slice(0,nl); buf=buf.slice(nl+1);
        if(line.endsWith('\r')) line=line.slice(0,-1);
        const trimmed=line.trim();
        if(!trimmed || trimmed.startsWith(':')) continue;
        if(!trimmed.startsWith('data:')) continue;
        const payload=trimmed.slice(5).trim();
        if(payload==='[DONE]'){ streamDone=true; break; }
        let j;
        try{ j=JSON.parse(payload); }catch(_){ continue; }
        lastChunk=j;
        if(j.error){ streamErr=String(j.error); streamDone=true; break; }
        if(j.pi_peer) peer=j.pi_peer;
        if(j.pi_model) used=j.pi_model;
        if(j.pi_kind) kind=j.pi_kind;
        if(j.pi_ms!=null) msServer=j.pi_ms;
        const delta=j.choices&&j.choices[0]&&j.choices[0].delta&&j.choices[0].delta.content;
        if(delta){ textAccum+=delta; live.setText(textAccum); }
      }
    }
    const ms=Math.round(performance.now()-t0);
    if(msServer==null) msServer=ms;
    if(streamErr || (!r.ok && !textAccum)){
      const msg=streamErr||('HTTP '+r.status);
      think.add('error', msg);
      think.done();
      live.setText(msg);
      live.markErr();
      live.finish(msg, {extra:'error', peer, model:used, ms:msServer, kind}, lastChunk);
      const btn=el('button','retry','Retry');
      btn.onclick=()=>{live.root.remove(); think.root.remove(); sendText(t,true);};
      live.root.appendChild(btn);
      return;
    }
    think.add('routed', 'peer '+peer+' · '+msServer+' ms server · '+ms+' ms wall');
    think.add('done', 'assistant reply (stream)');
    think.done();
    thread.push({role:'assistant', content:textAccum, meta:{peer, model:used, ms:msServer, kind}});
    live.finish(textAccum, {peer, model:used, ms:msServer, kind}, lastChunk);
  }catch(e){
    const msg=friendlyNet(e);
    think.add('error', msg);
    think.done();
    const bot=addMsg('bot', msg, {extra:'network'}, 'err');
    const btn=el('button','retry','Retry');
    btn.onclick=()=>{bot.remove(); think.root.remove(); sendText(t,true);};
    bot.appendChild(btn);
  }finally{
    sending=false; go.disabled=false; document.getElementById('q').focus();
  }
}

async function send(){
  const q=document.getElementById('q');
  let t=q.value.trim();
  const attached=q.dataset.attachText||'';
  if(attached){
    t = t ? (t+'\n\n---\n'+attached) : attached;
    clearAttach();
  }
  if(!t) return;
  q.value='';
  autoGrow(q);
  await sendText(t,false);
}

function autoGrow(ta){
  ta.style.height='auto';
  ta.style.height=Math.min(180, Math.max(44, ta.scrollHeight))+'px';
}

function threadAsMd(){
  let out='# pi-pair\n\n';
  thread.forEach(t=>{
    out+='### '+(t.role==='user'?'You':'Assistant')+'\n\n'+t.content+'\n\n';
    if(t.meta){
      const bits=[t.meta.peer,t.meta.model,t.meta.ms!=null?(t.meta.ms+' ms'):''].filter(Boolean);
      if(bits.length) out+='_'+bits.join(' · ')+'_\n\n';
    }
  });
  return out;
}
function threadAsTxt(){
  return thread.map(t=>{
    const who=t.role==='user'?'You':'Assistant';
    return who+':\n'+t.content;
  }).join('\n\n---\n\n');
}
function download(name, text, mime){
  const blob=new Blob([text],{type:mime||'text/plain'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob); a.download=name; a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href), 2000);
}

function clearAttach(){
  const q=document.getElementById('q');
  delete q.dataset.attachText;
  document.getElementById('fileChip').classList.remove('on');
  document.getElementById('attach').value='';
}
function loadFile(file){
  if(!file) return;
  const reader=new FileReader();
  reader.onload=()=>{
    const text=String(reader.result||'');
    document.getElementById('q').dataset.attachText=text;
    document.getElementById('fileName').textContent=file.name+' ('+Math.round(text.length/1024*10)/10+' KB)';
    document.getElementById('fileChip').classList.add('on');
  };
  reader.readAsText(file);
}

document.getElementById('go').onclick=send;
document.getElementById('q').addEventListener('input', e=>autoGrow(e.target));
document.getElementById('q').addEventListener('keydown', e=>{
  if(e.key==='Enter' && (e.metaKey||e.ctrlKey)){ e.preventDefault(); send(); }
});
document.getElementById('mesh').onchange=()=>refresh();
document.getElementById('peerSel').onchange=e=>{ target=e.target.value; refresh(); };
document.getElementById('btnIo').onclick=()=>setSettingsOpen(true);
document.getElementById('btnCloseIo').onclick=()=>setSettingsOpen(false);
document.getElementById('overlay').onclick=()=>setSettingsOpen(false);
document.getElementById('btnClear').onclick=()=>{
  thread.length=0;
  const log=document.getElementById('log'); log.innerHTML='';
  const empty=el('div','empty'); empty.id='empty';
  empty.innerHTML='<p>Chat cleared.</p>';
  log.appendChild(empty);
  setSettingsOpen(false);
};
document.getElementById('btnMd').onclick=()=>{
  if(!thread.length) return;
  download('mesh-chat.md', threadAsMd(), 'text/markdown');
};
document.getElementById('btnTxt').onclick=()=>{
  if(!thread.length) return;
  download('mesh-chat.txt', threadAsTxt(), 'text/plain');
};
document.getElementById('btnAttach').onclick=()=>document.getElementById('attach').click();
document.getElementById('attach').onchange=e=>loadFile(e.target.files&&e.target.files[0]);
document.getElementById('fileClear').onclick=clearAttach;
document.getElementById('q').addEventListener('paste', e=>{
  const items=e.clipboardData&&e.clipboardData.items;
  if(!items) return;
  for(const it of items){
    if(it.kind==='file'){ const f=it.getAsFile(); if(f){ e.preventDefault(); loadFile(f); return; } }
  }
});

refresh(); setInterval(refresh, 8000);
document.addEventListener('visibilitychange',()=>{ if(!document.hidden) refresh(); });