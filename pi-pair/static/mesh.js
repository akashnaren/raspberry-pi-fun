let sending=false;
let lastRaw=null;
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

let thinking='medium';

function effortLabel(name){
  const key=String(name||'').toLowerCase();
  if(key==='low') return 'Low';
  if(key==='medium') return 'Medium';
  if(key==='high') return 'High';
  return '';
}
function showEffort(parent, name){
  const label=effortLabel(name);
  if(!label || !parent) return;
  const node=el('div','effort', label);
  const labels=parent.querySelector('.label-row');
  if(labels) parent.insertBefore(node, labels);
  else parent.appendChild(node);
}

function shownError(msg){
  const s=String(msg&&msg.message||msg||'');
  if(/Load failed|Failed to fetch|NetworkError|network|abort|AbortError/i.test(s))
    return 'Connection dropped. Try again.';
  return 'The reply did not come back. Try again.';
}

function fillModels(rows){
  const sel=document.getElementById('modelSel');
  if(!sel) return;
  const prev=sel.value||DEFAULT_MODEL;
  const set=new Set();
  set.add(DEFAULT_MODEL);
  (rows||[]).forEach(p=>{(p.models||[]).forEach(m=>{ if(m) set.add(m); });});
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

function thumbIcon(){
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');
  svg.setAttribute('width','16');
  svg.setAttribute('height','16');
  svg.setAttribute('aria-hidden','true');
  const path=document.createElementNS('http://www.w3.org/2000/svg','path');
  path.setAttribute('fill','none');
  path.setAttribute('stroke','currentColor');
  path.setAttribute('stroke-width','1.6');
  path.setAttribute('stroke-linecap','round');
  path.setAttribute('stroke-linejoin','round');
  path.setAttribute('d','M8 11v8a1 1 0 0 0 1 1h7.2a2 2 0 0 0 1.9-1.4l1.3-5.2A2 2 0 0 0 17.5 11H14V7.2A2.2 2.2 0 0 0 11.8 5c-.5 0-.9.3-1.1.7L8 11zM8 11H5.5A1.5 1.5 0 0 0 4 12.5v5A1.5 1.5 0 0 0 5.5 19H8');
  svg.appendChild(path);
  return svg;
}

async function refresh(){
  try{
    const {r,j}=await fetchJson('/health', {method:'GET'}, 1);
    if(!r.ok) throw new Error('offline');
    document.getElementById('banner').className='';
    fillModels(j.peers);
  }catch(e){
    const b=document.getElementById('banner');
    b.textContent='Offline. '+friendlyNet(e);
    b.className='on';
  }
}

function attachLabel(parent, prompt, answer){
  const row=el('div','label-row');
  const up=el('button','icon-btn');
  const down=el('button','icon-btn down');
  const fix=el('button','text-btn','Correct');
  const note=el('span','label-note','');
  up.type=down.type=fix.type='button';
  up.appendChild(thumbIcon());
  down.appendChild(thumbIcon());
  up.setAttribute('aria-label','Thumbs up');
  down.setAttribute('aria-label','Thumbs down');
  fix.setAttribute('aria-label','Corrected answer');
  const box=el('div','fix-box');
  const input=document.createElement('textarea');
  input.rows=2;
  input.placeholder='Corrected answer';
  const save=el('button','mini','Save');
  save.type='button';
  box.appendChild(input);
  box.appendChild(save);
  let vote='';
  function paint(){
    up.classList.toggle('on', vote==='up');
    down.classList.toggle('on', vote==='down');
  }
  async function sendLabel(next){
    vote=next;
    paint();
    note.textContent='saving';
    const correction=(input.value||'').trim();
    try{
      const r=await fetch('/v1/flywheel/feedback',{
        method:'POST',
        headers:{'content-type':'application/json'},
        body:JSON.stringify({vote:vote, prompt:prompt, answer:answer, correction:correction})
      });
      let j={};
      try{ j=await r.json(); }catch(_){ j={}; }
      if(!r.ok) throw new Error(j.error||('HTTP '+r.status));
      note.textContent='saved';
    }catch(_){
      note.textContent='not saved';
    }
  }
  up.onclick=()=>sendLabel('up');
  down.onclick=()=>{ box.classList.add('on'); sendLabel('down'); };
  fix.onclick=()=>box.classList.toggle('on');
  save.onclick=()=>sendLabel(vote||'down');
  row.appendChild(up);
  row.appendChild(down);
  row.appendChild(fix);
  row.appendChild(note);
  parent.appendChild(row);
  parent.appendChild(box);
}

function addMsg(role,text,extraClass,prompt,effort){
  hideEmpty();
  const d=el('div','msg '+role+(extraClass?(' '+extraClass):''));
  const body=el('div','body');
  if(role==='bot') setBodyContent(body, text, true);
  else body.textContent=text;
  d.appendChild(body);
  if(role==='user'){
    const acts=el('div','msg-actions');
    const copy=el('button','mini','Copy');
    copy.type='button';
    copy.onclick=async()=>{
      try{ await navigator.clipboard.writeText(text); copy.textContent='Copied'; setTimeout(()=>copy.textContent='Copy',1200);}
      catch(_){ copy.textContent='Fail'; }
    };
    acts.appendChild(copy);
    d.appendChild(acts);
  }
  if(role==='bot' && !extraClass && prompt) attachLabel(d, prompt, text);
  if(role==='bot' && !extraClass) showEffort(d, effort);
  document.getElementById('log').appendChild(d);
  d.scrollIntoView({block:'end', behavior:'smooth'});
  return d;
}

function addLiveBot(){
  hideEmpty();
  const d=el('div','msg bot streaming');
  const body=el('div','body',''); d.appendChild(body);
  document.getElementById('log').appendChild(d);
  d.scrollIntoView({block:'end'});
  return {
    root:d, body,
    setText(t){ body.classList.remove('md'); body.textContent=t; d.scrollIntoView({block:'end'}); },
    finish(text, failed, prompt, effort){
      d.classList.remove('streaming');
      setBodyContent(body, text, !failed);
      if(prompt && !failed) attachLabel(d, prompt, text);
      if(!failed) showEffort(d, effort);
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
  const model=currentModel();
  const effort=thinking||'medium';
  refresh().catch(()=>{});
  try{
    const body={
      model:model,
      messages:[],
      stream:true,
      think:effort,
      pi_target:'auto',
      pi_mesh:'on'
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
          headers:{'content-type':'application/json','X-Pi-Target':'auto','X-Pi-Mesh':'on'},
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

    const effortUsed=r.headers.get('X-Pi-Think')||effort;
    const ct=(r.headers.get('content-type')||'').toLowerCase();
    const isSSE=ct.includes('event-stream');

    if(!isSSE){
      const textBody=await r.text();
      let j={};
      try{j=textBody?JSON.parse(textBody):{};}catch(_){j={};}
      lastRaw=j;
      if(!r.ok){
        const msg=shownError(j.error||('HTTP '+r.status));
        const bot=addMsg('bot', msg, 'err');
        const btn=el('button','retry','Retry');
        btn.type='button';
        btn.onclick=()=>{bot.remove(); sendText(t,true);};
        bot.appendChild(btn);
        return;
      }
      const text=(j.choices&&j.choices[0]&&j.choices[0].message&&j.choices[0].message.content)||'';
      thread.push({role:'assistant', content:text});
      addMsg('bot', text, '', t, j.pi_think||effortUsed);
      return;
    }

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
        const delta=j.choices&&j.choices[0]&&j.choices[0].delta&&j.choices[0].delta.content;
        if(delta){ textAccum+=delta; live.setText(textAccum); }
      }
    }
    lastRaw=lastChunk;
    if(streamErr || (!r.ok && !textAccum)){
      const msg=shownError(streamErr||('HTTP '+r.status));
      live.setText(msg);
      live.markErr();
      live.finish(msg, true);
      const btn=el('button','retry','Retry');
      btn.type='button';
      btn.onclick=()=>{live.root.remove(); sendText(t,true);};
      live.root.appendChild(btn);
      return;
    }
    let streamedEffort=effortUsed;
    if(lastChunk && lastChunk.pi_think) streamedEffort=lastChunk.pi_think;
    thread.push({role:'assistant', content:textAccum});
    live.finish(textAccum, false, t, streamedEffort);
  }catch(e){
    const msg=shownError(e);
    const bot=addMsg('bot', msg, 'err');
    const btn=el('button','retry','Retry');
    btn.type='button';
    btn.onclick=()=>{bot.remove(); sendText(t,true);};
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
  let out='# Pi 0.2 High\n\n';
  thread.forEach(t=>{
    out+='### '+(t.role==='user'?'You':'Assistant')+'\n\n'+t.content+'\n\n';
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
  document.getElementById('fileTag').classList.remove('on');
  document.getElementById('attach').value='';
}
function loadFile(file){
  if(!file) return;
  const reader=new FileReader();
  reader.onload=()=>{
    const text=String(reader.result||'');
    document.getElementById('q').dataset.attachText=text;
    document.getElementById('fileName').textContent=file.name+' ('+Math.round(text.length/1024*10)/10+' KB)';
    document.getElementById('fileTag').classList.add('on');
  };
  reader.readAsText(file);
}

document.getElementById('go').onclick=send;
document.getElementById('q').addEventListener('input', e=>autoGrow(e.target));
document.getElementById('q').addEventListener('keydown', e=>{
  if(e.key!=='Enter') return;
  if(e.shiftKey) return;
  if(e.ctrlKey || e.metaKey){
    e.preventDefault();
    const ta=e.target;
    const start=ta.selectionStart;
    const end=ta.selectionEnd;
    const next=ta.value.slice(0,start)+'\n'+ta.value.slice(end);
    ta.value=next;
    ta.selectionStart=ta.selectionEnd=start+1;
    autoGrow(ta);
    return;
  }
  e.preventDefault();
  send();
});
document.querySelectorAll('.think-btn').forEach(btn=>{
  btn.onclick=()=>{
    thinking=btn.getAttribute('data-think')||'medium';
    document.querySelectorAll('.think-btn').forEach(other=>other.classList.toggle('on', other===btn));
  };
});
document.getElementById('btnIo').onclick=()=>setSettingsOpen(true);
document.getElementById('btnCloseIo').onclick=()=>setSettingsOpen(false);
document.getElementById('overlay').onclick=()=>setSettingsOpen(false);
document.getElementById('btnClear').onclick=()=>{
  thread.length=0; lastRaw=null;
  const log=document.getElementById('log'); log.innerHTML='';
  const empty=el('div','empty'); empty.id='empty';
  empty.innerHTML='<p>Send a message.</p>';
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