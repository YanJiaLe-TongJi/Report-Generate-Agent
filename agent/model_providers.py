"""Shared routing, capability checks and vendor parameters for all report calls."""
import ipaddress
import socket
import re
from types import SimpleNamespace
from urllib.parse import urlsplit
import httpx
from openai import OpenAI
from model_catalog import catalog, default_kimi

def public_url(url):
    parts=urlsplit(url)
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('接口地址必须为公网 HTTPS Base URL，不含用户名、密码或查询参数')
    try:addresses=socket.getaddrinfo(parts.hostname,parts.port or 443,type=socket.SOCK_STREAM)
    except OSError:raise ValueError('无法解析模型服务地址') from None
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('模型接口不能指向本机或内网地址')
    return url.rstrip('/')

def profile(data):
    if not isinstance(data,dict):raise ValueError('模型配置格式错误')
    provider=data.get('provider','kimi')
    presets=catalog()['providers']
    if provider not in presets:raise ValueError('未知模型服务商')
    model=str(data.get('model') or '').strip()
    if not model or len(model)>150:raise ValueError('请选择或填写模型 ID')
    known=next((m for m in presets[provider]['models'] if m['id']==model),None)
    if provider=='kimi' and (model in catalog()['retired'] or model.startswith('moonshot-v1') or model=='kimi2.5'):
        raise ValueError('该 Kimi 模型已下线，请刷新模型列表后选择 '+default_kimi())
    if provider=='kimi' and not known:raise ValueError('模型不在当前 Kimi 可用目录中，请刷新模型列表')
    return {'provider':provider,'model':model,'base_url':public_url(data.get('base_url') or presets[provider]['base_url']),
            'vision':known['vision'] if known else bool(data.get('vision')),'api_key':str(data.get('api_key') or '').strip()}

def merge_profile(data,saved=None):
    p=profile(data);saved=saved or {}
    # Never send a saved key to a different endpoint/provider.
    if not p['api_key'] and (p['provider'],p['base_url'])==(saved.get('provider'),saved.get('base_url')):
        p['api_key']=saved.get('api_key','')
    if not p['api_key']:raise ValueError('请填写该服务商的 API Key，或先保存模型设置')
    return p

def route_profiles(text,vision=None,needs_vision=True):
    if text['vision']:return text,text
    if not needs_vision:return text,vision
    if not vision or not vision['vision']:raise ValueError('当前写作模型不支持图片，请额外配置支持图片的视觉模型')
    return text,vision

def config_profiles(config):
    text={'provider':config.get('provider','kimi'),'model':config['text_model'],'api_key':config['api_key'],'base_url':config['base_url']}
    vision={'provider':config.get('vision_provider',text['provider']),'model':config.get('vision_model',text['model']),'api_key':config.get('vision_api_key') or text['api_key'],'base_url':config.get('vision_base_url') or text['base_url']}
    return text,vision

def call_parameters(provider,model,kwargs):
    args=dict(kwargs);extra=dict(args.pop('extra_body',None) or {})
    # All vendor logic stays here; generic callers choose content and output budget only.
    extra.pop('thinking',None)
    if provider=='kimi':
        if model.startswith('kimi-k3'):
            for field in ('temperature','top_p','presence_penalty','frequency_penalty','n'):args.pop(field,None)
            extra['reasoning_effort']='low'
            args['max_tokens']=max(args.get('max_tokens',0),16384)
        elif model.startswith(('kimi-k2.6','kimi-k2.7')):
            extra['thinking']={'type':'disabled'};args['temperature']=0.6
    elif provider=='qwen':extra['enable_thinking']=False
    elif provider=='glm':extra['thinking']={'type':'disabled'}
    elif provider=='deepseek':extra['thinking']={'type':'disabled'}
    elif provider=='minimax':
        extra['reasoning_split']=True
        if model=='MiniMax-M3':extra['thinking']={'type':'disabled'}
        args['temperature']=1.0
        args['max_tokens']=max(args.get('max_tokens',0),8192)
    if extra:args['extra_body']=extra
    return args

class ProviderClient:
    def __init__(self,p):
        self.profile=p
        self.sdk=OpenAI(api_key=p['api_key'],base_url=public_url(p['base_url']),timeout=180,max_retries=2,http_client=httpx.Client(follow_redirects=False,trust_env=False))
        self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
    def create(self,**kwargs):
        try:
            response=self.sdk.chat.completions.create(**call_parameters(self.profile['provider'],kwargs.get('model',self.profile['model']),kwargs))
        except Exception as exc:
            status=getattr(exc,'status_code',None)
            reason={401:'API Key 无效',403:'无权限或模型未开通',404:'模型不存在或已下线',429:'请求限流或额度不足'}.get(status,'模型连接失败或超时')
            raise RuntimeError(f'{self.profile["provider"]}：{reason}'+(f'（HTTP {status}）' if status else '')) from None
        if not response.choices or not (response.choices[0].message.content or '').strip():raise RuntimeError('模型未返回正文，请更换模型或增加输出额度')
        if response.choices[0].finish_reason=='length':raise RuntimeError('模型输出达到长度上限，报告未完成；请重试或更换模型')
        return response
    def close(self):self.sdk.close()

def client_for(config,vision=False):return ProviderClient(config_profiles(config)[1 if vision else 0])
