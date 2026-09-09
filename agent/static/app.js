'use strict';
history.replaceState({},'', '/');
const $=id=>document.getElementById(id);
let state,selected=new Set(),polling=false;
function notice(message){$('notice').textContent=message;}
async function api(path,body,method='POST'){
 const options={method,headers:{'X-Desktop-Request':'1'}};
 if(body instanceof FormData)options.body=body;
 else if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
 const response=await fetch(path,options);
 let data;try{data=await response.json();}catch{throw Error('本地服务暂不可用，请重新打开 App');}
 if(!response.ok)throw Error(data.error||'操作失败');return data;
}
function node(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;}
function button(text,action){const el=node('button',text);el.onclick=()=>run(action,el);return el;}
async function run(fn,el){if(el)el.disabled=true;try{await fn();}catch(e){notice(e.message);}finally{if(el)el.disabled=false;}}
const categories={materials:'实验资料',data:'数据表格',raw_data:'原始记录',examples:'参考样例',prompt:'提示词模板'};
for(const b of document.querySelectorAll('nav button'))b.onclick=()=>{document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!==b.dataset.page);document.querySelectorAll('nav button').forEach(n=>n.classList.toggle('active',n===b));};
function renderLibrary(){
 $('selection').replaceChildren();$('library-list').replaceChildren();
 for(const item of state.library){
  if(item.category!=='prompt'){
   const row=node('div',undefined,'file-row'),label=node('label'),check=document.createElement('input');check.type='checkbox';check.checked=selected.has(item.id);check.onchange=()=>check.checked?selected.add(item.id):selected.delete(item.id);label.append(check,document.createTextNode(item.name));row.append(label,node('span',categories[item.category],'muted'));$('selection').append(row);
  }
  const row=node('div',undefined,'file-row');row.append(node('span',item.name),node('small',categories[item.category]),button('移出列表',async()=>{await api('/api/library/'+item.id,undefined,'DELETE');selected.delete(item.id);await refresh();}));$('library-list').append(row);
 }
 if(!state.library.length){$('selection').append(node('p','尚未导入文件。'));$('library-list').append(node('p','本地资料库为空。'));}
 const choice=$('prompt-choice'),value=choice.value;choice.replaceChildren(new Option('内置提示词',''));for(const p of state.library.filter(i=>i.category==='prompt'))choice.add(new Option(p.name,p.id));choice.value=value;
}
function profileForm(role,title){
 const card=node('div',undefined,'card');card.append(node('h2',title));
 const fields=[['provider','服务商','select'],['base_url','Base URL','input'],['model','模型 ID','input'],['api_key','API Key（留空保留已保存密钥）','input']];
 const grid=node('div',undefined,'grid');
 for(const [key,label,tag] of fields){const wrapper=node('label',label),el=document.createElement(tag);el.id=role+'-'+key;if(key==='provider'){el.add(new Option('请选择服务商',''));for(const [id,p] of Object.entries(state.presets))el.add(new Option(p.name,id));}else if(key==='api_key'){el.type='password';el.autocomplete='off';}el.value=state.settings[role]?.[key]||'';wrapper.append(el);grid.append(wrapper);}
 card.append(grid,button('测试'+(role==='vision'?'图片识别':'写作连接'),async()=>{await api('/api/test-model',{profile:readProfile(role),vision:role==='vision'});notice('测试通过');}));
 $('profiles').append(card);
 $(role+'-provider').onchange=()=>{const p=state.presets[$(role+'-provider').value];$(role+'-base_url').value=p?.base_url||'';$(role+'-model').value=p?.[role==='text'?'text_model':'vision_model']||'';$(role+'-api_key').value='';};
}
function readProfile(role){if(!$(role+'-provider').value)return null;return Object.fromEntries(['provider','base_url','model','api_key'].map(key=>[key,$(role+'-'+key).value.trim()]));}
const statuses={processing:'进行中',done:'已完成',awaiting_confirmation:'等待数据确认',error:'失败',interrupted:'已中断'};
let taskSignature='';
function renderTasks(){
 // Preserve text selections and revision input while polling unchanged state.
 const signature=JSON.stringify(state.tasks);if(signature===taskSignature)return;taskSignature=signature;
 const drafts={};document.querySelectorAll('[data-revision]').forEach(el=>drafts[el.dataset.revision]=el.value);
 $('tasks').replaceChildren();
 if(!state.tasks.length){$('tasks').append(node('p','生成的报告和修订版本会出现在这里。'));return;}
 for(const task of state.tasks){
  const card=node('article',undefined,'task');card.append(node('h3',(task.config.cover_info.experiment_name||'未命名实验')+' · '+statuses[task.status]),node('small',new Date(task.created*1000).toLocaleString()+(task.parent_id?' · 修订版本':''),'muted'));
  card.append(node('p',task.current_step));if(task.error)card.append(node('p',task.error,'error'));
  if(task.steps.length){const d=node('details');d.append(node('summary','生成步骤'),node('pre',task.steps.join('\n')));card.append(d);}
  const links=node('div',undefined,'toolbar');for(const name of task.artifacts||[])links.append(button('保存 '+name,async()=>{if(!window.pywebview)throw Error('请在桌面 App 中保存文件');const result=await window.pywebview.api.save_artifact(task.id,name);if(result.error)throw Error(result.error);if(result.ok)notice('文件已保存');}));card.append(links);
  if(task.status==='awaiting_confirmation')card.append(button('使用选中数据继续',async()=>{await api('/api/confirm/'+task.id,{selected:[...selected]});notice('已开始生成报告');await refresh();}));
  if(task.section_contents&&task.status!=='processing'){
   const input=document.createElement('textarea');input.placeholder='例如：补充误差传播公式，保留原始数据';input.rows=2;input.dataset.revision=task.id;input.value=drafts[task.id]||'';card.append(input,button('生成修订版本',async()=>{await api('/api/revise/'+task.id,{instruction:input.value});notice('已开始修订，旧版本保留');await refresh();}));
  }
  $('tasks').append(card);
 }
}
async function refresh(initial=false){state=await api('/api/state',undefined,'GET');renderLibrary();renderTasks();$('generate-button').disabled=state.tasks.some(t=>t.status==='processing');if(initial){$('edition').textContent=state.edition==='full'?'完整版 · Word + PDF':'Word 轻量版';if(state.edition==='full')$('format').add(new Option('PDF · LaTeX 排版','latex'));$('prompt').value=state.default_prompt;profileForm('text','报告写作');profileForm('vision','图片识别（有图片资料时必填）');if(!state.settings.text)notice('欢迎使用。请先打开模型设置，选择服务商并保存 API Key。');}}
$('import').onclick=()=>run(async()=>{const fd=new FormData();fd.append('category',$('import-category').value);for(const f of $('files').files)fd.append('files',f);if(!$('files').files.length)throw Error('请先选择文件');const data=await api('/api/library',fd);data.items.forEach(i=>selected.add(i.id));$('files').value='';await refresh();notice('文件已导入并选中');},$('import'));
$('save-settings').onclick=()=>run(async()=>{await api('/api/settings',{text:readProfile('text'),vision:readProfile('vision')});$('text-api_key').value='';$('vision-api_key').value='';notice('模型设置已保存');},$('save-settings'));
$('generate-button').onclick=()=>run(async()=>{const cover_info=Object.fromEntries(['experiment_name','student_name','student_id','group_number','experiment_date'].map(k=>[k,$(k).value]));await api('/api/generate',{selected:[...selected],cover_info,format_type:$('format').value,system_prompt:$('prompt').value,framework_mode:$('framework').checked});notice('任务已开始');await refresh();},$('generate-button'));
$('stop').onclick=()=>run(async()=>{await api('/api/stop',{});await refresh();notice('当前任务已停止');});
$('save-prompt').onclick=()=>run(async()=>{await api('/api/prompts',{name:$('prompt-name').value,content:$('prompt-content').value});await refresh();notice('模板已保存');});
$('prompt-choice').onchange=()=>{$('prompt').value=state.library.find(i=>i.id===$('prompt-choice').value)?.content||state.default_prompt;};
run(()=>refresh(true));setInterval(async()=>{if(polling)return;polling=true;try{await refresh();}catch(e){notice(e.message);}finally{polling=false;}},2000);
