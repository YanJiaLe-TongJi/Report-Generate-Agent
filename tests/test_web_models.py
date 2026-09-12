import base64, json, os, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
import model_catalog as catalog
import model_providers as providers

@pytest.fixture(autouse=True)
def offline_dns(monkeypatch):
    monkeypatch.setattr(providers.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('8.8.8.8',443))])


def p(provider='kimi',model='kimi-k3',vision=True,key='test-secret',url='https://api.moonshot.cn/v1'):
    return dict(provider=provider,model=model,vision=vision,api_key=key,base_url=url)

def test_current_official_directory():
    data=catalog.parse_kimi((Path(__file__).parent/'fixtures/kimi-models.md').read_text())
    assert [m['id'] for m in data['models']]==['kimi-k3','kimi-k2.7-code','kimi-k2.7-code-highspeed','kimi-k2.6']
    assert 'kimi-k2.5' in data['retired']
    assert all(m['vision'] for m in data['models'])

@pytest.mark.parametrize('content',['<html>blocked</html>','## 已下线模型\n| `kimi-k2.5` | 已下线 |'])
def test_bad_feed_retains_cache(tmp_path,monkeypatch,content):
    path=tmp_path/'catalog.json';path.write_text('previous');monkeypatch.setattr(catalog,'CACHE',path)
    import io
    monkeypatch.setattr(catalog,'urlopen',lambda *a,**k:io.BytesIO(content.encode()))
    with pytest.raises(ValueError):catalog.sync()
    assert path.read_text()=='previous'

def test_retired_rejected():
    with pytest.raises(ValueError):providers.profile(p(model='kimi-k2.5'))

def test_multimodal_reuses_same_config():
    text=p();assert providers.route_profiles(text)==(text,text)

def test_text_requires_vision():
    text=p('deepseek','deepseek-v4-pro',False)
    with pytest.raises(ValueError):providers.route_profiles(text)
    vision=p('qwen','qwen3-vl-plus');assert providers.route_profiles(text,vision)==(text,vision)

def test_saved_key_cannot_cross_provider_or_endpoint():
    saved=p()
    assert providers.merge_profile(p(key=''),saved)['api_key']=='test-secret'
    with pytest.raises(ValueError):providers.merge_profile(p(key='',url='https://other.example/v1'),saved)
    with pytest.raises(ValueError):providers.merge_profile(p(provider='custom',key=''),saved)

@pytest.mark.parametrize('url',['http://example.com/v1','https://user:pass@example.com/v1','https://example.com/v1?key=secret'])
def test_invalid_url(url):
    with pytest.raises(ValueError):providers.public_url(url)

def test_private_dns_blocked(monkeypatch):
    monkeypatch.setattr(providers.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError):providers.public_url('https://localhost/v1')

def test_k3_always_thinking_parameters():
    args=providers.call_parameters('kimi','kimi-k3',dict(temperature=.3,max_tokens=512,extra_body={'thinking':{'type':'disabled'}}))
    assert 'temperature' not in args
    assert args['extra_body']=={'reasoning_effort':'low'}
    assert args['max_tokens']>=16384

def test_mixed_client_credentials(monkeypatch):
    captured=[]
    monkeypatch.setattr(providers,'ProviderClient',lambda profile:captured.append(profile))
    config=dict(provider='deepseek',text_model='deepseek-v4-pro',api_key='writing',base_url='https://api.deepseek.com/v1',vision_provider='qwen',vision_model='qwen3-vl-plus',vision_api_key='seeing',vision_base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')
    providers.client_for(config);providers.client_for(config,True)
    assert captured[0]['api_key']=='writing' and captured[1]['api_key']=='seeing'
    assert captured[1]['provider']=='qwen'

@pytest.fixture(scope='module')
def web():
    with tempfile.TemporaryDirectory() as d:
        os.environ['SECRET_KEY']='test-session-only'
        os.environ['API_KEY_ENCRYPTION_KEY']=base64.b64encode(b'0'*32).decode()
        os.environ['DATABASE_URL']='sqlite:///'+d+'/test.db'
        os.environ['ADMIN_EMAIL']=''
        import app
        app.app.config.update(TESTING=True)
        with app.app.app_context():
            u=app.User(email='test@example.com',password_hash='unused');app.db.session.add(u);app.db.session.commit();uid=u.id
        client=app.app.test_client()
        with client.session_transaction() as session:session['_user_id']=str(uid);session['_fresh']=True
        yield app,client,uid

def test_settings_encrypted_and_roundtrip(web):
    app,c,uid=web
    response=c.post('/api/user/model-settings',json={'text':p()},headers={'Origin':'https://localhost'})
    assert response.status_code==200,response.json
    data=c.get('/api/user/model-settings').json
    assert data['text']['has_key'] and 'api_key' not in data['text']
    with app.app.app_context():
        row=app.db.session.get(app.UserModelSettings,uid)
        assert 'test-secret' not in row.ciphertext
    assert c.post('/api/user/model-settings',json={'text':p(key='')}).status_code==200
    html=c.get('/').get_data(as_text=True)
    assert 'test-secret' not in html and 'model-settings.js' in html

def test_untrusted_origin(web):
    _,c,_=web
    assert c.post('/api/user/model-settings',json={'text':p()},headers={'Origin':'https://evil.example'}).status_code==403

def test_catalog_public_settings_private(web):
    app,_,_=web
    anonymous=app.app.test_client()
    assert len(anonymous.get('/api/model-catalog').json['providers'])==6
    assert anonymous.get('/api/user/model-settings').status_code in (302,401)

def test_shared_vision_test_uses_saved_text_key(web,monkeypatch):
    app,c,_=web
    received=[]
    class Fake:
        def __init__(self,profile):
            received.append(profile)
            self.chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **k:SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='1234'))])))
        def close(self):pass
    monkeypatch.setattr(app,'ProviderClient',Fake)
    import secrets
    monkeypatch.setattr(secrets,'randbelow',lambda n:234)
    r=c.post('/api/test-model',json={'role':'vision','profile':p(key=''),'shared':True})
    assert r.status_code==200,r.json
    assert received[0]['api_key']=='test-secret'

@pytest.mark.parametrize('status',[401,403,404,429,None])
def test_provider_errors_never_expose_key(monkeypatch,status):
    class FakeSDK:
        def __init__(self,**kwargs):self.chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def create(self,**kwargs):
            exc=RuntimeError('secret-api-key in upstream response');exc.status_code=status;raise exc
        def close(self):pass
    monkeypatch.setattr(providers,'OpenAI',FakeSDK)
    client=providers.ProviderClient(p())
    with pytest.raises(RuntimeError) as result:client.chat.completions.create(model='kimi-k3',messages=[])
    assert 'secret-api-key' not in str(result.value)
    if status:assert str(status) in str(result.value)
    client.close()

def test_sdk_constructor_is_compatible():
    client=providers.ProviderClient(p());client.close()

def test_generate_requires_vision_before_start(web):
    import io
    _,c,_=web
    r=c.post('/api/generate',data={'materials':(io.BytesIO(b'image'),'guide.png'),'framework_mode':'1','model_settings':json.dumps({'text':p('deepseek','deepseek-v4-pro',False)})})
    assert r.status_code==400 and '视觉模型' in r.json['error']

def test_generate_routes_mixed_providers(web,monkeypatch):
    import io
    app,c,_=web
    monkeypatch.setattr(app.threading.Thread,'start',lambda self:None)
    text=p('deepseek','deepseek-v4-pro',False,key='write',url='https://api.deepseek.com/v1')
    vision=p('qwen','qwen3-vl-plus',True,key='see',url='https://dashscope.aliyuncs.com/compatible-mode/v1')
    r=c.post('/api/generate',data={'materials':(io.BytesIO(b'image'),'guide.png'),'framework_mode':'1','model_settings':json.dumps({'text':text,'vision':vision})})
    assert r.status_code==200,r.json
    task=app.tasks[r.json['task_id']]
    assert task['config']['provider']=='deepseek'
    assert task['config']['vision_provider']=='qwen'
    assert task['config']['vision_api_key']=='see'
