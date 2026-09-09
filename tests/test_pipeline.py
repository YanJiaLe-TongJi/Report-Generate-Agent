import queue
from types import SimpleNamespace
from pathlib import Path
import ai_generator
import harness
from constants import KNOWN_SECTIONS
from test_desktop import PROFILE,sample_task

def test_entire_generation_pipeline(tmp_path,monkeypatch):
    calls=[]
    def complete(**kw):
        calls.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='依据实验资料进行测量。电阻公式为 $R=U/I$。'))])
    client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))
    monkeypatch.setattr(ai_generator,'make_client',lambda p:client)
    task=sample_task(tmp_path);task.pop('section_contents')
    task['config'].update(system_prompt='根据真实数据生成报告',framework_mode=True,vision_profile=None,vision_model='')
    q=queue.Queue();harness.execute(task,q)
    while not q.empty():result=q.get()
    assert result['status']=='done',result.get('error')
    assert set(result['section_contents'])==set(KNOWN_SECTIONS)
    assert len(calls)>=6
    assert result['artifacts'][0].endswith('.docx')

def test_visual_client_is_separate(tmp_path,monkeypatch):
    text=SimpleNamespace();vision=SimpleNamespace();profiles=[];seen=[]
    def make(p):profiles.append(p);return vision if p['model']=='vision' else text
    monkeypatch.setattr(ai_generator,'make_client',make)
    monkeypatch.setattr(ai_generator,'extract_raw_data_from_image',lambda path,client,model:(seen.append(client) or '电压 | 电流\n1 | 2'))
    task=sample_task(tmp_path);task['config'].update(vision_profile=dict(PROFILE,model='vision'),vision_model='vision',raw_data_paths=['image.png'])
    ai_generator.run_raw_data_extraction(task['id'],task['config'],{task['id']:task})
    assert task['status']=='done'
    assert seen==[vision]
    assert task['result_mode']=='raw_data_extract'
