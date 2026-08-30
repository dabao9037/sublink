const $ = (s) => document.querySelector(s);
const editor = $('#editor'), nameInput = $('#name'), nodesInput = $('#nodes'), editId = $('#editId');
const errorBox = $('#error'), successBox = $('#success'), submitBtn = $('#submitBtn'), cancelBtn = $('#cancelBtn');
let subscriptions = [];

function nodeLines(){return nodesInput.value.split(/\r?\n/).map(x=>x.trim()).filter(x=>x && !x.startsWith('#'));}
function flash(el,msg){el.textContent=msg;el.hidden=false;setTimeout(()=>el.hidden=true,7000)}
function escapeHtml(value){return String(value).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]))}
async function api(url, options={}){
  const response=await fetch(url,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});
  if(response.status===204)return null;
  const data=await response.json().catch(()=>({detail:`请求失败 (${response.status})`}));
  if(!response.ok)throw new Error(data.detail||'请求失败');
  return data;
}
async function copy(text,button){
  const old=button.textContent;
  try{
    if(navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else throw new Error('clipboard fallback');
  }catch(_){
    const input=document.createElement('textarea');input.value=text;input.style.position='fixed';input.style.opacity='0';document.body.appendChild(input);input.select();document.execCommand('copy');input.remove();
  }
  button.textContent='已复制';setTimeout(()=>button.textContent=old,1300)
}
function showResult(result){
  $('#resultUniversal').value=result.links.universal;
  $('#openUniversal').href=result.links.universal;
  $('#resultQr').src=result.links.qrcode;
  $('#resultPanel').hidden=false;
  requestAnimationFrame(()=>$('#resultPanel').scrollIntoView({behavior:'smooth',block:'center'}));
}
function render(){
  $('#subCount').textContent=subscriptions.length;
  $('#nodeCount').textContent=subscriptions.reduce((n,s)=>n+s.node_count,0);
  $('#empty').hidden=subscriptions.length>0;
  $('#list').innerHTML=subscriptions.map(s=>`<article class="sub-card">
    <div class="sub-name"><h3>${escapeHtml(s.name)}</h3><p>更新于 ${new Date(s.updated_at).toLocaleString('zh-CN')}</p></div>
    <div class="node-badge"><strong>${s.node_count}</strong><p>NODES</p></div>
    <div class="links">
      <div class="link-row"><label>订阅链接</label><input readonly value="${escapeHtml(s.links.universal)}"><button class="copy" data-copy="${escapeHtml(s.links.universal)}">复制</button></div>
    </div>
    <div class="card-qr"><img src="${escapeHtml(s.links.qrcode)}" alt="${escapeHtml(s.name)}订阅二维码" loading="lazy"><span>扫码导入</span></div>
    <div class="card-actions"><button class="small-btn" data-edit="${s.id}">编辑</button><button class="small-btn danger" data-delete="${s.id}">删除</button></div>
  </article>`).join('');
}
async function load(){try{subscriptions=await api('/api/subscriptions');render()}catch(e){flash(errorBox,e.message)}}
function resetEditor(){editId.value='';editor.reset();$('#modeText').textContent='新建订阅';submitBtn.querySelector('span').textContent='生成订阅';cancelBtn.hidden=true;$('#lineCount').textContent='0'}
async function editSub(id){try{const s=await api(`/api/subscriptions/${id}`);editId.value=id;nameInput.value=s.name;nodesInput.value=s.nodes;$('#modeText').textContent='编辑订阅';submitBtn.querySelector('span').textContent='保存修改';cancelBtn.hidden=false;$('#lineCount').textContent=nodeLines().length;editor.scrollIntoView({behavior:'smooth',block:'center'})}catch(e){flash(errorBox,e.message)}}
async function deleteSub(id){if(!confirm('确定删除这个订阅吗？原订阅地址会立即失效。'))return;try{await api(`/api/subscriptions/${id}`,{method:'DELETE'});await load()}catch(e){flash(errorBox,e.message)}}
nodesInput.addEventListener('input',()=>$('#lineCount').textContent=nodeLines().length);
editor.addEventListener('submit',async(e)=>{e.preventDefault();errorBox.hidden=true;successBox.hidden=true;submitBtn.disabled=true;try{const payload={name:nameInput.value.trim(),nodes:nodesInput.value};const id=editId.value;const result=await api(id?`/api/subscriptions/${id}`:'/api/subscriptions',{method:id?'PUT':'POST',body:JSON.stringify(payload)});flash(successBox,id?'订阅已更新，原地址保持不变。':'订阅已生成，链接已显示在页面上方。');resetEditor();await load();showResult(result)}catch(e){flash(errorBox,e.message)}finally{submitBtn.disabled=false}});
$('#list').addEventListener('click',(e)=>{const copyBtn=e.target.closest('[data-copy]'),editBtn=e.target.closest('[data-edit]'),deleteBtn=e.target.closest('[data-delete]');if(copyBtn)copy(copyBtn.dataset.copy,copyBtn);if(editBtn)editSub(editBtn.dataset.edit);if(deleteBtn)deleteSub(deleteBtn.dataset.delete)});
cancelBtn.addEventListener('click',resetEditor);$('#refreshBtn').addEventListener('click',load);
$('#resultPanel').addEventListener('click',(e)=>{const button=e.target.closest('[data-result-copy]');if(button)copy($('#resultUniversal').value,button)});
$('#closeResult').addEventListener('click',()=>$('#resultPanel').hidden=true);
load();
