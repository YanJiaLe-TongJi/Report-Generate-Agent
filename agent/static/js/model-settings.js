/* Keys stay in password inputs and encrypted server storage, never browser storage. */
let modelCatalog, savedModels = {}, modelReady = false;
const mel = id => document.getElementById(id);
function modelPanel(role, title) {
  const box = mel(role === 'text' ? 'modelText' : 'modelVision');
  box.innerHTML = `<h3>${title}</h3><div class="row"><div><label for="${role}Provider">服务商</label><select id="${role}Provider"></select></div><div><label for="${role}Model">模型 ID（可修改）</label><input id="${role}Model" list="${role}Models"><datalist id="${role}Models"></datalist></div></div><div class="row"><div><label for="${role}Base">Base URL</label><input id="${role}Base"></div><div><label for="${role}Key">API Key</label><input type="password" autocomplete="new-password" id="${role}Key" placeholder="填写 API Key"></div></div><label><input type="checkbox" id="${role}Vision" style="width:auto"> 此模型支持图片（目录内模型自动判断，自定义模型请确认）</label><button type="button" class="btn btn-sm btn-outline" id="${role}Test">测试${role === 'vision' ? '图片识别' : '连接'}</button><span id="${role}Result" role="status"></span>`;
  const select = mel(role+'Provider');
  select.add(new Option('请选择服务商',''));
  for (const id of ['deepseek','kimi','qwen','minimax','glm','custom']) select.add(new Option(modelCatalog.providers[id].name,id));
  select.onchange = () => setModelProvider(role);
  mel(role+'Model').oninput = () => updateCapability(role);
  mel(role+'Vision').onchange = updateVisionPanel;
  mel(role+'Test').onclick = () => testModel(role);
}
function setModelProvider(role, saved) {
  const id=mel(role+'Provider').value, p=modelCatalog.providers[id];
  mel(role+'Models').replaceChildren();
  mel(role+'Key').value='';
  mel(role+'Key').placeholder=saved?.has_key ? '已保存；留空沿用' : '填写 API Key';
  mel(role+'Base').value=saved?.base_url || p?.base_url || '';
  for (const m of p?.models || []) mel(role+'Models').append(new Option(m.id+(m.vision?' · 支持图片':' · 纯文本'),m.id));
  let model=saved?.model || p?.models[0]?.id || '';
  if(id==='kimi' && !p.models.some(m=>m.id===model)) model=p.models[0].id;
  mel(role+'Model').value=model;
  mel(role+'Vision').checked=!!saved?.vision;
  updateCapability(role);
}
function updateCapability(role) {
  const p=modelCatalog.providers[mel(role+'Provider').value];
  const known=p?.models.find(m=>m.id===mel(role+'Model').value.trim());
  mel(role+'Vision').disabled=!!known;
  if(known) mel(role+'Vision').checked=known.vision;
  updateVisionPanel();
}
function updateVisionPanel() {
  if(!mel('textVision') || !mel('visionProvider')) return;
  const shared=mel('textVision').checked;
  const selected=!!mel('textProvider').value;
  mel('modelVision').hidden=shared || !selected;
  mel('visionHint').textContent=!selected?'请先选择写作模型。':shared?'当前模型支持图片，写作和识图共用同一配置。':'当前写作模型不支持图片：处理资料图片或识别原始记录时，请额外配置视觉模型。';
}
function readModel(role, required=true) {
  const provider=mel(role+'Provider').value;
  if(!provider && !required) return null;
  if(!provider) throw Error('请选择'+(role==='text'?'写作':'视觉')+'服务商');
  const p={provider, model:mel(role+'Model').value.trim(),base_url:mel(role+'Base').value.trim(),api_key:mel(role+'Key').value.trim(),vision:mel(role+'Vision').checked};
  if(!p.model || !p.base_url) throw Error('请填写模型 ID 和 Base URL');
  const saved=savedModels[role];
  if(!p.api_key && !(saved?.has_key && saved.provider===provider && saved.base_url.replace(/\/$/,'')===p.base_url.replace(/\/$/,''))) throw Error('请填写此服务商的 API Key');
  return p;
}
function collectModelSettings() {
  if(!modelReady) throw Error('模型设置尚未加载完成，请稍后再试');
  const text=readModel('text');
  const vision=text.vision?null:readModel('vision',false);
  return {text,...(vision?{vision}: {})};
}
async function modelRequest(url,body) {
  const r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});
  if(!r.headers.get('content-type')?.includes('application/json')) throw Error('登录可能已过期，请刷新后重试');
  const d=await r.json();if(!r.ok || d.error) throw Error(d.error || '请求失败');return d;
}
async function saveModelSettings() {
  try {
    await modelRequest('/api/user/model-settings',collectModelSettings());
    savedModels=await modelRequest('/api/user/model-settings');
    for(const role of ['text','vision']) {mel(role+'Key').value='';mel(role+'Key').placeholder=savedModels[role]?.has_key?'已保存；留空沿用':'填写 API Key';}
    alert('模型配置已加密保存到账户');
  } catch(e) {alert(e.message);}
}
async function testModel(role) {
  const button=mel(role+'Test'), status=mel(role+'Result');
  try {
    const profile=readModel(role);button.disabled=true;status.textContent=' 测试中…';
    await modelRequest('/api/test-model',{role,profile});
    if(role==='text' && profile.vision) await modelRequest('/api/test-model',{role:'vision',profile:{...profile,api_key:profile.api_key},shared:true});
    status.textContent=' 测试成功'+(profile.vision?'（含图片识别）':'');
  } catch(e) {status.textContent=' '+e.message;} finally {button.disabled=false;}
}
(async()=>{
  try {
    [modelCatalog,savedModels]=await Promise.all([modelRequest('/api/model-catalog'),modelRequest('/api/user/model-settings')]);
    modelPanel('text','写作模型');modelPanel('vision','视觉模型');
    for(const role of ['text','vision']) {
      if(savedModels[role]) mel(role+'Provider').value=savedModels[role].provider;
      setModelProvider(role,savedModels[role]);
    }
    mel('catalogStatus').textContent='Kimi 可用模型每 24 小时同步官方目录。'+(modelCatalog.updated_at?'最近同步：'+new Date(modelCatalog.updated_at).toLocaleString():'暂用内置目录，等待首次同步。');
    modelReady=true;updateVisionPanel();
  } catch(e) {mel('catalogStatus').textContent='模型配置加载失败：'+e.message;}
})();
