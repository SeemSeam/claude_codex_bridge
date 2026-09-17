const data={main:[['demo','空闲'],['agent1','工作中']],review:[['reviewer','工作中'],['agent3','异常']]};
const remembered={main:'demo',review:'reviewer'},drafts={};
let win='main',selected='demo',mode='对话',collapsed=false;
const q=s=>document.querySelector(s);
const original=q('.content').innerHTML;
const style=document.createElement('style');style.textContent=`button{cursor:pointer;font:inherit}button:focus-visible{outline:3px solid #72acd2}.baseline img{width:100%;border-radius:8px}.baseline a{font-size:12px}.sheet-cover{position:absolute;inset:0;background:#1236;display:flex;align-items:flex-end;z-index:3}.sheet{background:white;padding:18px;width:100%;max-height:75%;overflow:auto;border-radius:18px 18px 0 0}.sheet button{display:block;width:100%;text-align:left;margin:7px 0}.sheet input{width:100%;padding:10px}.phone{position:relative}.agent.error{background:#ffebed;color:#a33}.compact-target{padding:9px 13px;background:white;font-weight:650}.terminal{background:#14232d;color:#b9dfc7;white-space:pre-wrap;font:13px/1.8 monospace;height:100%;margin:0;padding:12px;border-radius:8px}.content{min-height:0}.sub{font-size:12px}.composer,.top,.modes,.handle,.windows,.agents{flex-shrink:0}`;document.head.append(style);
q('.intro').insertAdjacentHTML('beforeend','<div class="note baseline"><b>本次虚拟机实际截图</b><a href="vm-baseline.png" target="_blank"><img src="vm-baseline.png" alt="emulator-5554 中当前 CCB Mobile 对话界面，点击查看原图">查看完整原图 ↗</a><p>新截图中的窗口已显示工作文案，并非仍只有数字。本方案重点是切换与层级，不重复修复已完成的状态配色。</p></div>');
function save(){drafts[win+'/'+selected+'/'+mode]=q('textarea').value}
function chooseWindow(w){save();win=w;selected=remembered[w];paint()}
function chooseAgent(w,a){save();win=w;selected=a;remembered[w]=a;q('.sheet-cover')?.remove();paint()}
function sheet(){const cover=document.createElement('div');cover.className='sheet-cover';cover.innerHTML='<div class=\"sheet\" role=\"dialog\" aria-label=\"全部 Agent\"><div class=\"row\"><b class=\"grow\">全部 Agent</b><button class=\"button\" style=\"width:auto\" onclick=\"this.closest(\'.sheet-cover\').remove()\">关闭</button></div><div class=\"results\"></div></div>';q('.phone').append(cover);cover.querySelector('.results').innerHTML=Object.entries(data).map(([w,as])=>`<h4>${w}</h4>${as.map(([a,s])=>`<button class=\"button\" onclick=\"chooseAgent('${w}','${a}')\">${selected===a?'✓ ':''}${a} · ${s}${a==='reviewer'?' · 未读 2':''}</button>`).join('')}`).join('');cover.onclick=e=>{if(e.target===cover)cover.remove()}}
function paint(){
q('.top .sub').textContent=`${win} / ${selected} · 模拟项目`;
q('.windows').innerHTML=Object.entries(data).map(([w,as])=>{const count=as.filter(x=>x[1]==='工作中').length;return `<button class="window ${w===win?'selected':''} ${count?'busy':''}" onclick="chooseWindow('${w}')">${w}<small>${count?count+' 工作中':'空闲'}${w==='review'?' · 未读 2':''}</small></button>`}).join('');
q('.agents').innerHTML=data[win].map(([a,s])=>`<button class="agent ${s==='工作中'?'busy':s==='异常'?'error':''} ${a===selected?'selected':''}" onclick="chooseAgent('${win}','${a}')">${a===selected?'✓ ':''}${a} · ${s}${a==='reviewer'?'<span class="unread"> · 2 未读</span>':''}</button>`).join('')+'<button class="agent" onclick="sheet()">全部 Agent ▾</button>';
q('.windows').style.display=q('.agents').style.display=collapsed?'none':'flex';
q('.compact-target')?.remove();if(collapsed)q('.handle').insertAdjacentHTML('beforebegin',`<div class="compact-target">${win} / ${selected} · ${data[win].find(x=>x[0]===selected)[1]}</div>`);
q('.handle button').textContent=collapsed?'⌄ 展开窗口与 Agent':'⌃ 收起';
q('.content').innerHTML=mode==='对话'?original:`<pre class="terminal"># ccb-v2 / ${win} / ${selected}\n# 只读模拟画面\n\n$ ccb status\n${data[win].map(x=>x.join('  ')).join('\n')}\n\n终端模式保留原切换方式。\n此原型不执行指令。</pre>`;
if(mode==='对话')q('.bubble:not(.user) .sub').textContent=`${selected} · ccb-v2 / ${win}`;
q('.composer>.sub').textContent=`${mode==='对话'?'发送给':'终端输入 →'} ${selected} · ccb-v2 / ${win}`;q('textarea').value=drafts[win+'/'+selected+'/'+mode]||'';
document.querySelectorAll('.modes button').forEach((b,i)=>b.classList.toggle('on',(i===0)===(mode==='对话')));
}
q('.handle button').onclick=()=>{save();collapsed=!collapsed;paint()};document.querySelectorAll('.modes button').forEach((b,i)=>b.onclick=()=>{save();mode=i===0?'对话':'终端';paint()});
q('.composer .primary').onclick=()=>alert('原型演示：未发送到服务器');q('.composer .button').onclick=()=>alert('附件入口示意，不读取文件');q('.top .button').onclick=()=>location.href='index.html';q('.top .button:last-child').onclick=()=>sheet();
// Scroll down to read folds the switchers; scrolling back up restores them.
let lastScroll=0;q('.content').addEventListener('scroll',()=>{const now=q('.content').scrollTop;if(now>lastScroll+30&&!collapsed){save();collapsed=true;paint()}lastScroll=now});
q('.compare li').textContent='窗口名称、工作数与未读数保留为两行紧凑标签；下排仅显示当前窗口 Agent。';
q('.intro .note p').textContent='实际截图已具备窗口和 Agent 工作文案。仍需改善大量 Agent 的寻找、窗口间上次选择记忆，以及折叠后的目标确认。';
const compactStyle=document.createElement('style');
compactStyle.textContent=`
.modes,.handle,.compact-target{display:none!important}
.top{padding:5px 10px}.top .title{font-size:17px}.top .sub{font-size:11px}
.top .button{border:0;padding:6px;min-width:40px;min-height:44px;background:transparent}
.windows,.agents{padding:2px 10px;gap:6px;min-height:40px;scrollbar-width:none}
.agents{padding-bottom:5px}.window,.agent{min-height:34px;padding:5px 8px;border-radius:7px;font-size:12px;white-space:nowrap}
.window small{display:inline;margin-left:5px;font-size:10px}.windows{border-bottom:0}
.composer{padding:6px 10px}.composer>.sub{font-size:10px;margin-bottom:3px}
.compose-line{display:flex;gap:6px;align-items:flex-end}.compose-line textarea{flex:1;min-width:0;height:44px;font:13px/1.5 system-ui}
.compose-line .button{min-width:40px;min-height:44px;padding:6px}.composer .between{display:none}
.target-expand{text-align:left;max-width:100%;border:0;background:transparent;padding:0;color:var(--muted);font-size:11px;min-height:24px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.content{padding:8px 12px}.bubble{margin:8px 0;padding:11px}.content details{display:none}
.terminal-action[aria-pressed=true]{color:var(--brand);background:#e4f0fa}.top .row{gap:3px}
`;
document.head.append(compactStyle);
const topRow=q('.top .row');
const terminalButton=document.createElement('button');terminalButton.className='button terminal-action';terminalButton.innerHTML='▣';terminalButton.title='切换到终端';terminalButton.setAttribute('aria-label','切换到终端');terminalButton.onclick=()=>{save();mode=mode==='对话'?'终端':'对话';if(mode==='终端')collapsed=true;paint()};topRow.insertBefore(terminalButton,topRow.lastElementChild);
const composeLine=document.createElement('div');composeLine.className='compose-line';q('.composer').append(composeLine);
const attachment=q('.composer .between .button'),send=q('.composer .primary');attachment.textContent='＋';attachment.title='添加附件';send.textContent='↑';send.title='发送消息（模拟）';composeLine.append(attachment,q('textarea'),send);
const previousPaint=paint;
paint=function(){
 const scroll=q('.content').scrollTop;
 previousPaint();
 const state=data[win].find(x=>x[0]===selected)[1];
 q('.top .sub').innerHTML=`<button class="target-expand" title="${collapsed?'展开':'收起'}窗口与 Agent" aria-expanded="${!collapsed}">${win} / ${selected} · ${state} ${collapsed?'⌄':'⌃'}</button>`;
 q('.target-expand').onclick=()=>{save();collapsed=!collapsed;paint()};
 terminalButton.setAttribute('aria-pressed',String(mode==='终端'));terminalButton.title=mode==='对话'?'切换到终端':'返回对话';terminalButton.setAttribute('aria-label',terminalButton.title);terminalButton.textContent=mode==='对话'?'▣':'☷';
 q('textarea').placeholder=mode==='对话'?'输入任务或补充说明…':'终端模拟输入，不执行';
 send.title=mode==='对话'?'发送消息（模拟）':'终端 Enter（模拟）';
 q('.content').scrollTop=scroll;
};
q('.intro h1').innerHTML='对话优先，<br>控制区退后';
q('.intro>p').textContent='恢复原版的紧凑操作逻辑：无整行模式切换，终端入口在顶部；窗口与 Agent 仅按需展开。';
q('.compare').innerHTML='<h2>这一版的收敛</h2><ul><li>取消整行对话／终端分段控件，改为顶部 44px 点击区域的小按钮。</li><li>窗口与 Agent 保留原两排，但标签单行、无额外收起手柄。</li><li>点标题下的目标摘要展开／收起；阅读向下滚动时自动收起，不因轻微反向滚动反复展开。</li><li>输入区改为一行布局，给消息释放空间。不常驻终端预览。</li><li>折叠只减少控制区，不隐藏当前窗口、Agent 和状态。</li></ul><div class="note"><b>模拟器中可试</b><p>点击目标摘要展开列表；切换窗口和 Agent；点击顶部 ▣ 进入终端，再点 ☷ 返回对话。草稿按目标、模式分别保留。</p><p>34px 标签是密度提案，正式 Flutter 应保留合理命中区域；不是本轮原生触控验收。</p></div>';
collapsed=true;paint();
