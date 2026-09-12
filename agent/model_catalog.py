"""Curated provider capabilities plus a daily, atomic Kimi documentation feed."""
import copy
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE='https://platform.kimi.com/docs/models.md'
CACHE=Path(os.environ.get('MODEL_CATALOG_PATH',Path(__file__).parent/'instance/model_catalog.json'))

def model(identifier,vision=False):return {'id':identifier,'vision':vision}
PROVIDERS={
 'kimi':{'name':'Kimi','base_url':'https://api.moonshot.cn/v1','models':[model('kimi-k3',True),model('kimi-k2.6',True)]},
 'deepseek':{'name':'DeepSeek','base_url':'https://api.deepseek.com/v1','models':[model('deepseek-v4-pro'),model('deepseek-v4-flash-vision-exp',True)]},
 'qwen':{'name':'Qwen 通义千问','base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1','models':[model('qwen3.7-plus',True),model('qwen-plus'),model('qwen3-vl-plus',True)]},
 'minimax':{'name':'MiniMax','base_url':'https://api.minimax.cn/v1','models':[model('MiniMax-M3',True),model('MiniMax-M2.7')]},
 'glm':{'name':'GLM 智谱','base_url':'https://open.bigmodel.cn/api/paas/v4','models':[model('glm-5.2'),model('glm-5v-turbo',True),model('glm-4.6v-flash',True)]},
 'custom':{'name':'自定义 OpenAI 兼容接口','base_url':'','models':[]},
}

def parse_kimi(markdown):
    active=[];retired=[];section='';seen=set()
    for line in markdown.splitlines():
        if line.startswith('#'):section=line.strip('# ').strip()
        if not line.lstrip().startswith('|'):continue
        cells=line.strip().strip('|').split('|')
        match=re.search(r'`((?:kimi|moonshot)-[A-Za-z0-9._-]+)`',cells[0])
        if not match:continue
        identifier=match.group(1)
        if '下线' in section or 'deprecated' in section.lower():retired.append(identifier);continue
        if identifier in seen:continue
        seen.add(identifier)
        description=' '.join(cells[1:])
        vision=bool('多模态' in section or '视觉' in description or 'multimodal' in section.lower())
        if '纯文本' in description:vision=False
        active.append(model(identifier,vision))
    if not active or len(active)>100:raise ValueError('官方模型目录结构不符合预期，保留上次有效列表')
    if set(i['id'] for i in active)&set(retired):raise ValueError('模型同时标为可用和下线，保留上次有效列表')
    return {'models':active,'retired':retired,'source':SOURCE,'updated_at':datetime.now(timezone.utc).isoformat()}

def sync():
    # Only a fixed official URL is fetched. No user credentials are sent.
    with urlopen(Request(SOURCE,headers={'User-Agent':'PhysicsReport-ModelCatalog/1.0','Cache-Control':'no-cache'}),timeout=25) as response:
        content=response.read(2*1024*1024+1)
    if len(content)>2*1024*1024:raise ValueError('模型目录过大')
    data=parse_kimi(content.decode('utf-8'))
    CACHE.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=CACHE.parent,delete=False) as stream:
            temporary=Path(stream.name);json.dump(data,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        temporary.replace(CACHE)
    finally:
        if temporary and temporary.exists():temporary.unlink()
    return data

def catalog():
    result={'providers':copy.deepcopy(PROVIDERS),'updated_at':None,'source':SOURCE,'retired':['kimi-k2.5'],'sync_interval_hours':24}
    try:
        data=json.loads(CACHE.read_text(encoding='utf-8'))
        if not data.get('models'):raise ValueError()
        result['providers']['kimi']['models']=data['models']
        result.update({k:data[k] for k in ('updated_at','retired') if k in data})
    except (OSError,ValueError,KeyError):pass
    return result

def default_kimi():return catalog()['providers']['kimi']['models'][0]['id']

if __name__=='__main__':
    import sys
    try:
        data=sync();print('Kimi catalog updated:',len(data['models']),'active models;',data['updated_at'])
    except Exception as exc:
        print('Kimi catalog refresh failed; previous cache retained:',type(exc).__name__,file=sys.stderr);sys.exit(1)
