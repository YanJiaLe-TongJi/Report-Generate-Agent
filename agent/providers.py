"""Provider policy and credential access. No API keys enter task/config JSON."""
import hashlib
import io
import base64
from types import SimpleNamespace
from urllib.parse import urlparse
import keyring
from openai import OpenAI
from PIL import Image, ImageDraw

PRESETS = {
 'deepseek': {'name':'DeepSeek', 'base_url':'https://api.deepseek.com/v1', 'text_model':'deepseek-chat', 'vision_model':''},
 'kimi': {'name':'Kimi', 'base_url':'https://api.moonshot.cn/v1', 'text_model':'kimi-k2.5', 'vision_model':'kimi-k2.5'},
 'qwen': {'name':'Qwen', 'base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1', 'text_model':'qwen-plus', 'vision_model':'qwen-vl-plus'},
 'minimax': {'name':'MiniMax', 'base_url':'https://api.minimaxi.com/v1', 'text_model':'MiniMax-M2.5', 'vision_model':''},
 'glm': {'name':'GLM', 'base_url':'https://open.bigmodel.cn/api/paas/v4', 'text_model':'glm-4.7', 'vision_model':'glm-4.6v'},
 'custom': {'name':'自定义（OpenAI 兼容）', 'base_url':'', 'text_model':'', 'vision_model':''},
}

def validate(profile):
    if profile.get('provider') not in PRESETS: raise ValueError('请选择模型服务商')
    url = urlparse(profile.get('base_url', ''))
    if url.scheme not in ('https','http') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Base URL 必须是有效的 HTTP(S) 接口地址，不能包含凭据或查询参数')
    if not profile.get('model','').strip(): raise ValueError('请填写模型 ID')
    return {k: str(profile[k]).strip() for k in ('provider','base_url','model')}

def credential_id(profile):
    return hashlib.sha256((profile['provider']+'|'+profile['base_url']).encode()).hexdigest()

def secret(profile):
    try: value = keyring.get_password('PhysicsReport', credential_id(profile))
    except Exception: raise ValueError('无法访问系统凭据库，请解锁系统钥匙串/凭据管理器') from None
    if not value: raise ValueError('请在模型设置中保存 API Key')
    return value

def save_secret(profile, value):
    if value:
        try: keyring.set_password('PhysicsReport', credential_id(profile), value)
        except Exception: raise ValueError('系统凭据库保存失败，未将密钥写入磁盘') from None

def safe_error(exc):
    code = getattr(exc, 'status_code', None)
    if code in (401,403): return '模型认证失败，请检查 API Key 和账户权限'
    if code == 429: return '模型服务限流或额度不足（429），请稍后重试'
    if code == 400: return '模型拒绝请求（400），请检查模型能力、参数和资料长度'
    if code == 404: return '模型或接口不存在（404），请检查 Base URL 和模型 ID'
    return '模型请求失败，请检查网络、接口配置或稍后重试（'+type(exc).__name__+'）'

class Client:
    def __init__(self, profile, api_key=None):
        self.profile = validate(profile)
        self.raw = OpenAI(api_key=api_key or secret(profile), base_url=profile['base_url'], timeout=120, max_retries=2)
        self.chat = SimpleNamespace(completions=self)
    def create(self, **kwargs):
        model = kwargs.get('model', self.profile['model'])
        provider = self.profile['provider']
        if provider == 'kimi' and model == 'kimi-k2.5':
            kwargs['extra_body'] = {'thinking': {'type':'disabled'}}
            kwargs['temperature'] = 0.6
        elif provider == 'qwen' and (model.startswith('qwen3') or model in ('qwen-plus','qwen-turbo')):
            kwargs['extra_body'] = {'enable_thinking':False}
        elif provider == 'glm' and model.startswith(('glm-4.6','glm-4.7','glm-5')):
            kwargs['extra_body'] = {'thinking':{'type':'disabled'}}
        elif provider == 'minimax':
            kwargs['temperature'] = 1.0
        if provider == 'deepseek' and model == 'deepseek-chat':
            kwargs['max_tokens'] = min(kwargs.get('max_tokens',4096),8192)
        try:
            response = self.raw.chat.completions.create(**kwargs)
            if not response.choices or not response.choices[0].message.content:
                raise ValueError('empty response')
            if response.choices[0].finish_reason == 'length':
                raise ValueError('response exceeded token limit')
            return response
        except Exception as exc: raise RuntimeError(safe_error(exc)) from None

def make_client(profile): return Client(profile)

def test_connection(profile, vision=False, api_key=None):
    client = Client(profile, api_key)
    content = '请只回复 OK'
    expected = None
    if vision:
        # Test image understanding rather than merely accepting an image field.
        import secrets
        expected = ''.join(secrets.choice('23456789') for _ in range(4))
        img = Image.new('RGB',(180,70),'white')
        ImageDraw.Draw(img).text((15,15), expected, fill='black', font_size=32)
        buf=io.BytesIO(); img.save(buf,format='PNG')
        content=[{'type':'text','text':'读取图片中的四位数字，只输出数字'}, {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()}}]
    response=client.create(model=profile['model'],messages=[{'role':'user','content':content}],max_tokens=512)
    if expected and expected not in response.choices[0].message.content:
        raise ValueError('图片识别测试未通过，请选择支持视觉的模型')
    return True
