// Isolated visual prototype. No network, provider input, or runtime mutations.
const $=s=>document.querySelector(s);
// Reuse familiar icon shapes instead of ambiguous Unicode placeholders.
document.querySelector('[aria-label="附件"]').innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 12V7a4 4 0 0 1 8 0v10a6 6 0 0 1-12 0V8m4 0v9a2 2 0 0 0 4 0V7"/></svg>';
document.querySelector('[aria-label="配置"]').innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18M8 3v6m8 0v6m-6 0v6"/></svg>';
const hosts={main:[{name:'demo',state:'idle',label:'空闲',provider:'Omp'},{name:'agent1',state:'working',label:'工作中',provider:'Codex',unread:true}],window2:[{name:'agent3',state:'error',label:'异常',provider:'Codex'}]};
let windowName='main',agent=hosts.main[0],mode='chat',collapsed=false;
const snapshots=new Map();
const key=()=>`${windowName}/${agent.name}/${mode}`;
function save(){snapshots.set(key(),{draft:$('textarea').value,scroll:$('#timeline').scrollTop})}
function fold(value){collapsed=value;$('#switchers').hidden=value;$('#summary').hidden=!value;$('#summary').textContent=`${windowName} / ${agent.name} · ${agent.label}${agent.unread?' · ★ 未读':''}　⌄`}
function select(w,a){save();windowName=w;agent=a;render()}
function chip(text,selected,style,action){const b=document.createElement('button');b.className=`${style} ${selected?'selected':''}`;b.setAttribute('aria-pressed',String(selected));b.innerHTML=text;b.onclick=action;return b}
function render(){
 $('#windows').replaceChildren(...Object.entries(hosts).map(([w,agents])=>{const count=agents.filter(a=>a.state==='working').length;return chip(`${w===windowName?'▣':'▢'} ${w}${count?` <small>${count} 工作中</small>`:''}${agents.some(a=>a.unread)?' <span class="unread" aria-label="有未读消息">★</span>':''}`,w===windowName,count?'working':'idle',()=>select(w,agents[0]))}));
 $('#agents').replaceChildren(...hosts[windowName].map(a=>chip(`${a===agent?'✓ ':''}${a.name} <small>${a.label}</small>${a.unread?' <span class="unread" aria-label="有未读消息">★</span>':''}`,a===agent,a.state,()=>select(windowName,a))));
 $('#provider').textContent=agent.provider;
 $('#mode').setAttribute('aria-pressed',String(mode==='terminal'));$('#mode').setAttribute('aria-label',mode==='chat'?'切换到终端':'返回对话');$('#mode').title=$('#mode').getAttribute('aria-label');
 $('#timeline').setAttribute('aria-label',mode==='chat'?'对话内容':'模拟终端');
 $('#timeline').innerHTML=mode==='chat'?[
 ['你','09:38','保持现在的窗口和 Agent 列表，只优化细节。'],
 [agent.name,'09:39','保留原来的操作顺序：选择窗口，再选择 Agent。工作状态与消息提醒分别显示。'],
 ['你','09:40','对话与终端仍然采用原来的切换方式。'],
 [agent.name,'09:41','这是界面示意，未执行任务。顶部保留终端入口，返回后保留对话草稿与阅读位置。'],
 ['你','09:42','不要增加搜索，也不要增加新的功能入口。'],
 [agent.name,'09:43','本版只收紧间距与文字层级。绿色代表工作中，橙色星标代表未读，蓝色轮廓标明选中。']
 ].map(([name,time,text])=>`<article class="bubble ${name==='你'?'user':''}"><div class="meta"><strong>${name}</strong><span>${time}</span></div><p>${text}</p></article>`).join(''):`<pre class="terminal"># 模拟终端 · ${windowName} / ${agent.name}\n# 静态示意，不执行命令\n\n$ ccb status\n${agent.name}    ${agent.label}\n\n${Array.from({length:28},(_,i)=>`[${String(i+1).padStart(2,'0')}] terminal output sample`).join('\n')}</pre>`;
 const saved=snapshots.get(key());$('textarea').value=saved?.draft||'';$('textarea').placeholder=mode==='chat'?`Message ${agent.name}`:`Terminal ${agent.name}（模拟）`;$('#timeline').scrollTop=saved?.scroll||0;fold(collapsed);
}
$('#mode').onclick=()=>{save();mode=mode==='chat'?'terminal':'chat';render()};
$('#collapse').onclick=()=>fold(true);$('#summary').onclick=()=>fold(false);
// Match the existing downward-user-scroll collapse intent, not a new threshold.
// Native timing/keyboard behavior remains owned by Flutter.
$('#timeline').addEventListener('wheel',e=>{if(e.deltaY>0)fold(true)},{passive:true});
let touchY=null;
$('#timeline').addEventListener('touchstart',e=>{touchY=e.touches[0].clientY},{passive:true});
$('#timeline').addEventListener('touchmove',e=>{if(touchY!==null&&e.touches[0].clientY<touchY)fold(true);touchY=e.touches[0].clientY},{passive:true});
let noticeTimer;
function notice(text){clearTimeout(noticeTimer);$('#notice').textContent=text;$('#notice').hidden=false;noticeTimer=setTimeout(()=>$('#notice').hidden=true,2200)}
document.querySelectorAll('.placeholder').forEach(b=>b.onclick=()=>notice(`${b.getAttribute('aria-label')}：保留原入口，此 Demo 不展开功能。`));
$('#send').onclick=()=>notice('仅供设计审查，消息未发送。');
render();
