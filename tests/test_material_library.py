import io
import zipfile
from PIL import Image
import pytest
from material_library import import_pack
from storage import read_json

def pack(files):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    stream.seek(0)
    return stream

def picture():
    stream=io.BytesIO();Image.new('RGB',(20,20)).save(stream,format='PNG');return stream.getvalue()

def test_import_group_duplicate_and_restart(tmp_path):
    files={'实验 甲/第1页.png':picture()}
    result=import_pack(pack(files),tmp_path)
    assert result[0]['experiment']=='实验 甲'
    assert len(read_json(tmp_path/'library.json',[]))==1
    assert import_pack(pack(files),tmp_path)==[]

def test_invalid_pack_atomic(tmp_path):
    with pytest.raises(ValueError):import_pack(pack({'实验/a.png':picture(),'实验/b.png':b'broken'}),tmp_path)
    assert not (tmp_path/'library.json').exists()
    assert not list((tmp_path/'library').glob('*'))

@pytest.mark.parametrize('path',['../outside.png','/root.png','a\\b.png'])
def test_unsafe_paths(tmp_path,path):
    with pytest.raises(ValueError):import_pack(pack({path:picture()}),tmp_path)

def test_export_web_database(tmp_path):
    import sqlite3,sys,json
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
    from export_material_library import export
    uploads=tmp_path/'uploads';uploads.mkdir();(uploads/'a.png').write_bytes(picture())
    db=tmp_path/'web.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE system_materials(experiment_name,material_paths,example_path,is_active)')
        c.execute('INSERT INTO system_materials VALUES(?,?,?,1)',('单摆',json.dumps(['uploads/a.png']),None))
    output=tmp_path/'资料.zip';export(db,uploads,output)
    assert import_pack(output,tmp_path)[0]['experiment']=='单摆'

def test_pack_route_preview_and_select(tmp_path,monkeypatch):
    import app as server
    monkeypatch.setattr(server,'DATA_DIR',tmp_path)
    class Engine:
        tasks={}
        def list(self):return []
        def start(self,config):self.config=config;return 'test'
    engine=Engine();application=server.create_app('token',engine);client=application.test_client()
    assert client.post('/api/library/import-pack').status_code==403
    client.get('/?launch=token')
    response=client.post('/api/library/import-pack',data={'pack':(pack({'实验 甲/a.png':picture()}),'实验.zip')},headers={'X-Desktop-Request':'1'})
    assert response.status_code==200
    item=response.json['items'][0]
    assert client.get('/api/library/'+item['id']+'/preview').data==picture()
    monkeypatch.setattr(server,'secret',lambda profile:'secret')
    from storage import write_json
    write_json(tmp_path/'settings.json',{'text':{'model':'mock'},'vision':{'model':'vision'}})
    response=client.post('/api/generate',json={'selected':[item['id']],'framework_mode':True},headers={'X-Desktop-Request':'1'})
    assert response.status_code==200
    assert engine.config['material_paths']==[item['path']]
