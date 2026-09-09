"""Single-user loopback transport; report execution lives in harness.py."""
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlparse
from flask import Flask, request, jsonify, render_template, make_response, abort
from paths import DATA_DIR, RESOURCE_DIR, EDITION
from storage import read_json, write_json
from providers import PRESETS, validate, save_secret, secret, test_connection
from prompts import DEFAULT_SYSTEM_PROMPT
from constants import COVER_FIELDS
from harness import Harness

EXTENSIONS={'materials':{'.png','.jpg','.jpeg'},'raw_data':{'.png','.jpg','.jpeg'},'data':{'.xlsx','.xls','.csv'},'examples':{'.docx'}}

def create_app(token=None, harness=None):
    app=Flask(__name__); app.config['MAX_CONTENT_LENGTH']=200*1024*1024
    token=token or secrets.token_urlsafe(32)
    engine=harness or Harness();app.engine=engine;app.access_token=token
    def library():return read_json(DATA_DIR/'library.json',[])
    def settings():return read_json(DATA_DIR/'settings.json',{})
    def artifact(tid,name):
        t=engine.tasks.get(tid)
        if not t or name not in t.get('artifacts',[]) or Path(name).name!=name: raise ValueError('文件不存在')
        p=DATA_DIR/'outputs'/name
        if not p.is_file():raise ValueError('结果文件已被移除')
        return p
    app.artifact=artifact
    @app.before_request
    def protect():
        if request.host.split(':')[0] not in ('127.0.0.1','localhost'): abort(403)
        if request.path=='/' and secrets.compare_digest(request.args.get('launch',''),token):return None
        if not secrets.compare_digest(request.cookies.get('desktop_access',''),token):abort(403)
        origin=request.headers.get('Origin')
        if origin and origin!=request.host_url.rstrip('/'):abort(403)
        if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('X-Desktop-Request')!='1':abort(403)
    @app.after_request
    def headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        # pywebview builds native API wrappers with new Function; inline scripts stay blocked.
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response
    @app.errorhandler(ValueError)
    def invalid(exc):return jsonify(error=str(exc)),400
    @app.errorhandler(413)
    def too_large(exc):return jsonify(error='文件总大小不能超过 200MB'),413
    @app.get('/')
    def index():
        response=make_response(render_template('index.html'))
        response.set_cookie('desktop_access',token,httponly=True,samesite='Strict')
        return response
    @app.get('/api/state')
    def state():return jsonify(edition=EDITION,settings=settings(),presets=PRESETS,library=library(),tasks=engine.list(),default_prompt=DEFAULT_SYSTEM_PROMPT)
    @app.post('/api/settings')
    def save_settings():
        data=request.get_json();result={}
        for role in ('text','vision'):
            item=data.get(role)
            if not item:continue
            profile=validate(item);save_secret(profile,item.get('api_key','').strip());secret(profile)
            result[role]=profile
        if 'text' not in result:raise ValueError('请设置报告写作模型')
        write_json(DATA_DIR/'settings.json',result)
        return jsonify(ok=True)
    @app.post('/api/test-model')
    def test_model():
        data=request.get_json();profile=validate(data['profile'])
        test_connection(profile,vision=data.get('vision',False),api_key=data['profile'].get('api_key') or None)
        return jsonify(ok=True)
    @app.post('/api/library')
    def import_files():
        category=request.form.get('category')
        if category not in EXTENSIONS:raise ValueError('未知资料类型')
        items=library();added=[]
        folder=DATA_DIR/'library';folder.mkdir(exist_ok=True)
        for f in request.files.getlist('files'):
            name=Path(f.filename.replace('\\','/')).name;suffix=Path(name).suffix.lower()
            if suffix not in EXTENSIONS[category]:raise ValueError('文件类型不支持：'+name)
            identifier=uuid.uuid4().hex;p=folder/(identifier+suffix);f.save(p)
            added.append({'id':identifier,'name':name,'category':category,'path':str(p)})
        items.extend(added);write_json(DATA_DIR/'library.json',items)
        return jsonify(items=added)
    @app.post('/api/prompts')
    def save_prompt():
        data=request.get_json();name=data.get('name','').strip();content=data.get('content','').strip()
        if not name or not content:raise ValueError('请填写模板名称和提示词')
        items=library();items.append({'id':uuid.uuid4().hex,'name':name,'category':'prompt','content':content})
        write_json(DATA_DIR/'library.json',items);return jsonify(ok=True)
    @app.delete('/api/library/<identifier>')
    def remove_item(identifier):
        # Preserve files referenced by old reports, so later revisions remain reproducible.
        write_json(DATA_DIR/'library.json',[i for i in library() if i['id']!=identifier]);return jsonify(ok=True)
    def profiles(config):
        saved=settings()
        if not saved.get('text'):raise ValueError('请先保存模型设置')
        config.update(text_profile=saved['text'],vision_profile=saved.get('vision'),text_model=saved['text']['model'],vision_model=saved.get('vision',{}).get('model',''))
        secret(saved['text'])
        if (config.get('material_paths') or (config.get('raw_data_paths') and not config.get('data_paths'))) and not config.get('vision_profile'):
            raise ValueError('此任务需要图片识别，请先设置视觉模型')
        return config
    @app.post('/api/generate')
    def generate():
        data=request.get_json();selected=set(data.get('selected',[]));items=library()
        config={category+'_paths':[i['path'] for i in items if i['id'] in selected and i['category']==category] for category in EXTENSIONS}
        config['material_paths']=config.pop('materials_paths');config['example_paths']=config.pop('examples_paths')
        config.update(cover_info={k:str(data.get('cover_info',{}).get(k,'')) for k in COVER_FIELDS},system_prompt=data.get('system_prompt') or DEFAULT_SYSTEM_PROMPT,format_type=data.get('format_type','word'),framework_mode=bool(data.get('framework_mode')))
        if not config['data_paths'] and not config['raw_data_paths'] and not config['framework_mode']:raise ValueError('请选择数据文件、原始记录图片，或开启无数据框架模式')
        config['append_raw_data_image']=bool(config['data_paths'] and config['raw_data_paths'])
        config['use_raw_data_ocr']=bool(config['raw_data_paths'] and not config['data_paths'])
        return jsonify(task_id=engine.start(profiles(config)))
    @app.post('/api/confirm/<tid>')
    def confirm(tid):
        source=engine.tasks.get(tid)
        if not source or source['status']!='awaiting_confirmation':raise ValueError('任务无需数据确认')
        ids=set(request.get_json().get('selected',[]))
        paths=[i['path'] for i in library() if i['id'] in ids and i['category']=='data']
        if not paths:raise ValueError('请导入并选中已核对的 Excel/CSV 文件')
        config=dict(source['config']);config.update(data_paths=paths,use_raw_data_ocr=False,append_raw_data_image=True)
        return jsonify(task_id=engine.start(profiles(config)))
    @app.post('/api/revise/<tid>')
    def revise(tid):
        source=engine.tasks.get(tid)
        if not source:raise ValueError('任务不存在')
        data=request.get_json()
        return jsonify(task_id=engine.start(profiles(dict(source['config'])),source,data.get('instruction',''),data.get('targets')))
    @app.post('/api/stop')
    def stop():engine.stop();return jsonify(ok=True)
    return app
