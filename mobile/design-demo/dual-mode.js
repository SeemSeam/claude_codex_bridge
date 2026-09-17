// Isolated design prototype: never opens a terminal or sends real input.
const dualStyle=document.createElement('style');
dualStyle.textContent=`
.mode-tabs{display:flex;gap:4px;padding:5px 16px;background:var(--surface);border-bottom:1px solid var(--line);flex-shrink:0}
.mode-tabs button{flex:1;font-size:13px}.mode-tabs .chosen{background:var(--brand);color:var(--surface)}
.live-preview{border-top:1px solid var(--line);background:var(--surface);flex-shrink:0}
.preview-head{display:flex;align-items:center;gap:4px;padding:0 10px}.preview-head button{font-size:12px}
.preview-toggle{flex:1;text-align:left;min-width:0}.preview-output{margin:0 12px 10px;padding:10px 12px;border-radius:9px;background:#14212b;color:#b6d9c4;font:12px/1.5 monospace;max-height:105px;overflow:auto;white-space:pre-wrap}
.terminal-output{background:#14212b;color:#b6d9c4;font:13px/1.8 monospace;white-space:pre-wrap;overflow:auto}
.terminal-input{border:1px solid var(--line);border-radius:8px;padding:8px;width:100%;background:var(--bg);color:var(--ink)}
.terminal-controls{display:flex;flex-wrap:wrap;gap:4px;padding-top:8px}.terminal-controls button{font-size:12px;padding:5px 9px}
.phone .content{min-height:0}.phone .top,.phone .composer{flex-shrink:0}
.dual-target{font-size:19px;font-weight:750;overflow-wrap:anywhere}.mode-tabs button:disabled{opacity:.6}
@media(max-height:650px){.preview-output{max-height:60px}.phone .agentbar{padding:4px 16px}}
`;
document.head.append(dualStyle);
const terminalDrafts=new Map(),scrollPositions=new Map(),previewStates=new Map(),terminalSteps=new Map(),messagesByTarget=new Map();
let paintedTarget='',paintedMode='';
const targetKey=()=>selectedProject+' / '+agent;
const outputFor=()=>{
  const a=agents.find(x=>x.name===agent),step=terminalSteps.get(targetKey())||0;
  const lines=[`# 工作站 / ${selectedProject} / ${agent}`,`# ${a.provider} · 模拟终端快照`, '',
    ...(a.state==='working'?['Reading layout constraints…','✓ status colors','✓ unread badge independence',`Running tests… ${42+step}/68`]:a.state==='error'?['HTTP 429 · usage limit reached','Task stopped before completion.','Waiting for operator.']:['✓ Task complete','Ready for next input.']),
    ...(step?[`[演示更新 ${step}] layout check finished`]:[])];
  return lines.join('\n');
};
function saveSurface(){
  if(!paintedTarget)return;
  const field=paintedMode==='chat'?$('#draft'):$('#terminal-draft');
  if(field)(paintedMode==='chat'?drafts:terminalDrafts).set(paintedTarget,field.value);
  const surface=paintedMode==='chat'?$('#timeline'):$('#terminal-output');
  if(surface)scrollPositions.set(paintedTarget+'|'+paintedMode,surface.scrollTop);
}
function modeSwitch(mode){saveSurface();closeSheet();page=mode;render();}
function togglePreview(){saveSurface();previewStates.set(targetKey(),!previewStates.get(targetKey()));render();}
function demoTick(){if(offline){toast('离线：保留上次快照，不伪造新输出');return}saveSurface();terminalSteps.set(targetKey(),(terminalSteps.get(targetKey())||0)+1);render();}
function terminalKey(key){if(offline)return;toast(`模拟 ${key} → ${agent}；未向终端发送任何字节`);}
const priorRender=render;
render=function(){
  saveSurface();priorRender();paintedTarget='';paintedMode='';
  if(page!=='chat'&&page!=='terminal')return;
  const target=targetKey(),a=agents.find(x=>x.name===agent),expanded=previewStates.get(target)||false;
  const top=$('#app .top');
  top.innerHTML=`<div class="row between"><button aria-label="返回项目" onclick="nav('home')">‹</button><div class="grow"><div class="dual-target">${esc(agent)}</div><div class="sub ellipsis" title="${esc(target)}">${esc(selectedProject)} / ${esc(a.window)} · ${esc(a.provider)}</div></div>${badge(a.state,a.label)}<button onclick="sheet('more')">更多</button></div>`;
  top.insertAdjacentHTML('afterend',`<div class="mode-tabs" role="group" aria-label="显示模式"><button aria-pressed="${page==='chat'}" class="${page==='chat'?'chosen':''}" onclick="modeSwitch('chat')">▤ 对话</button><button aria-pressed="${page==='terminal'}" class="${page==='terminal'?'chosen':''}" onclick="modeSwitch('terminal')">⌘ 终端</button></div>`);
  if(page==='chat'){
    $('#app .agentbar').innerHTML=`<div class="row between"><span class="sub ellipsis">${offline?'上次同步状态 · 可能已过期':esc(a.task)}</span><button onclick="nav('agents')">切换 Agent ›</button></div>`;
    for(const text of messagesByTarget.get(target)||[]){const b=document.createElement('div');b.className='bubble user';b.textContent=text;$('#timeline').append(b);}
    const preview=document.createElement('section');preview.className='live-preview';preview.setAttribute('aria-label','只读终端现场');
    preview.innerHTML=`<div class="preview-head"><button class="preview-toggle" aria-expanded="${expanded}" onclick="togglePreview()">${expanded?'⌄':'›'} 终端现场 <span class="sub">· 只读 · ${offline?'缓存快照':'模拟快照'}</span></button><button onclick="modeSwitch('terminal')" aria-label="进入完整终端">完整终端 ↗</button></div>${expanded?`<pre class="preview-output">${esc(outputFor().split('\n').slice(-4).join('\n'))}</pre><div class="row between" style="padding:0 12px 6px"><span class="sub">${offline?'离线期间不更新':'此演示通过按钮更新，不自动轮询'}</span><button onclick="demoTick()" ${offline?'disabled':''}>模拟新输出</button></div>`:''}`;
    $('#app .composer').before(preview);
    $('#draft').value=drafts.get(target)||'';
    $('#timeline').scrollTop=scrollPositions.get(target+'|chat')||0;
  }else{
    const surface=$('#app .content');surface.id='terminal-output';surface.className='content terminal-output';surface.removeAttribute('style');surface.textContent=outputFor()+'\n\n'+Array.from({length:12},(_,i)=>`[history ${i+1}] retained output for ${agent}`).join('\n');
    $('#app .composer').innerHTML=`<div class="sub">终端输入 → <strong>${esc(agent)}</strong> · 与消息草稿分开保存</div><input id="terminal-draft" class="terminal-input" aria-label="终端模拟输入草稿" placeholder="模拟输入，不会执行" value="${esc(terminalDrafts.get(target)||'')}"><div class="terminal-controls">${['Esc','Tab','Ctrl-C','↑','↓'].map(k=>`<button class="outline" ${offline?'disabled':''} onclick="terminalKey('${k}')">${k}</button>`).join('')}<button class="primary" ${offline?'disabled':''} onclick="terminalKey('Enter')">模拟 Enter</button><button onclick="demoTick()" ${offline?'disabled':''}>模拟新输出</button></div>`;
    $('#terminal-draft').addEventListener('input',e=>terminalDrafts.set(target,e.target.value));
    surface.scrollTop=scrollPositions.get(target+'|terminal')||0;
  }
  paintedTarget=target;paintedMode=page;
  $('#review').innerHTML=`<div class="note"><h3>双模式保持独立，共享同一目标</h3><p>两种视图都显示 ${esc(agent)}。切换不发送输入，不切换 Agent；各自保留草稿与滚动位置。</p><p>对话中的终端现场只读，展开看最后几行，完整控制必须进入终端模式。</p></div><div class="note"><h3>我的审查结论</h3><p>手机默认收起现场更合理；展开后只保留约四行，避免挤占消息和输入区。</p><p>窄屏不做默认上下分屏。平板分屏先留作后续方案。</p><p>预览不是工作状态的判断依据。离线提示覆盖两种模式，保留快照但禁止模拟发送。</p></div>`;
};
const beforeDualSend=sendDemo;
sendDemo=function(){const text=$('#draft')?.value.trim();if(offline)return;if(text){const key=targetKey();messagesByTarget.set(key,[...(messagesByTarget.get(key)||[]),text]);}beforeDualSend();};
// Default to the new proposal so reviewers land directly on the added flow.
page='chat';selectedProject='CCB Bridge';agent='agent1';render();
