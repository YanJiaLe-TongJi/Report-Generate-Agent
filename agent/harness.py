"""Report execution and persistence; independent of Flask and desktop UI."""
import json
import multiprocessing as mp
import shutil
import threading
import time
import uuid
from pathlib import Path
from paths import DATA_DIR, EDITION
from storage import read_json, write_json
from constants import KNOWN_SECTIONS
from providers import make_client, test_connection

TASKS = DATA_DIR / 'tasks'
TASKS.mkdir(exist_ok=True)

def render(task):
    config=task['config']; tid=task['id']; work=Path(config['task_dir'])
    output=DATA_DIR/'outputs'; output.mkdir(exist_ok=True)
    stem=f'实验报告_{tid}'
    raw=output/(stem+'.md')
    raw.write_text('\n\n'.join(f'## {k}\n\n{v}' for k,v in task['section_contents'].items()),encoding='utf-8')
    task['artifacts']=[raw.name]
    args=(config['cover_info'],task['section_contents'],str(work),config.get('raw_data_paths'),config.get('material_paths'),config.get('plot_paths'))
    if config['format_type']=='word':
        from word_backend import build_word_document_from_template
        path=build_word_document_from_template(*args,append_raw_data_image=config.get('append_raw_data_image',False))
        dest=output/(stem+'.docx'); shutil.copy2(path,dest)
        task['artifacts'].insert(0,dest.name)
    else:
        if EDITION!='full': raise ValueError('Word 轻量版不支持 PDF 编译')
        from latex_backend import build_latex_document,compile_latex
        tex=build_latex_document(*args,append_raw_data_image=config.get('append_raw_data_image',False))
        ok,log=compile_latex(tex,str(output/(stem+'.pdf')))
        logpath=output/(stem+'.log'); logpath.write_text(log,encoding='utf-8')
        archive=shutil.make_archive(str(output/(stem+'_源码')),'zip',Path(tex).parent)
        task['artifacts'] += [Path(archive).name,logpath.name]
        if not ok: raise ValueError('PDF 编译失败，已保留原文、源码资源包和编译日志')
        task['artifacts'].insert(0,stem+'.pdf')

def execute(task, queue):
    """Runs in a disposable process. Credentials are obtained inside adapters."""
    finished=threading.Event()
    def publish():
        while not finished.wait(.5): queue.put(json.loads(json.dumps(task)))
    watcher=threading.Thread(target=publish,daemon=True); watcher.start()
    try:
        config=task['config']; work=Path(config['task_dir']);work.mkdir(parents=True,exist_ok=True)
        if task.get('instruction'):
            client=make_client(config['text_profile'])
            targets=task.get('targets') or list(KNOWN_SECTIONS)
            subset={k:task['section_contents'].get(k,'') for k in targets}
            task['current_step']='正在按要求修订报告'
            response=client.chat.completions.create(model=config['text_model'],messages=[
                {'role':'system','content':'你是物理报告编辑。仅修订指定章节，不编造实验数据。返回严格 JSON 对象，键为给定章节名，值为章节正文字符串。'},
                {'role':'user','content':json.dumps(subset,ensure_ascii=False)+'\n修订要求：'+task['instruction']}],max_tokens=8192)
            text=response.choices[0].message.content
            start=text.find('{'); end=text.rfind('}')
            changed=json.loads(text[start:end+1])
            if set(changed)!=set(targets) or not all(isinstance(v,str) and v.strip() for v in changed.values()):
                raise ValueError('修订返回的章节不完整，请重试；原版本已保留')
            task['section_contents'].update(changed)
            render(task)
        else:
            from ai_generator import run_ai_generation,run_raw_data_extraction
            registry={task['id']:task}
            needs_vision=bool(config['material_paths'] or (config['raw_data_paths'] and not config['data_paths']))
            if needs_vision:
                task['current_step']='正在验证图片识别模型'
                test_connection(config['vision_profile'],vision=True)
            if config['raw_data_paths'] and not config['data_paths'] and not config.get('framework_mode'):
                run_raw_data_extraction(task['id'],config,registry)
                task['artifacts']=[Path(task[k]).name for k in ('download_url','raw_url') if task.get(k)]
                if task['status']=='error': raise ValueError(task.get('error','识别失败'))
                task['status']='awaiting_confirmation'
            else:
                sections=run_ai_generation(task['id'],config,registry,config['format_type'])
                if not sections or task['status']=='error': raise ValueError(task.get('error','生成失败'))
                task['section_contents']=sections
                render(task)
        if task['status']!='awaiting_confirmation': task['status']='done'
        task['current_step']='请核对表格，再导入确认后的数据继续生成' if task['status']=='awaiting_confirmation' else '完成'
    except Exception as exc:
        task['status']='error'; task['error']=str(exc)
    finally:
        finished.set(); watcher.join();queue.put(task)

class Harness:
    def __init__(self):
        self.lock=threading.RLock(); self.process=None; self.active=None
        self.tasks={}
        for path in TASKS.glob('*.json'):
            t=read_json(path,{})
            if t.get('status')=='processing':
                t['status']='interrupted';t['current_step']='上次任务已中断';write_json(path,t)
            if t.get('id'): self.tasks[t['id']]=t
    def persist(self,t):
        write_json(TASKS/(t['id']+'.json'),t)
    def list(self):
        with self.lock: return sorted(self.tasks.values(),key=lambda t:t['created'],reverse=True)
    def start(self, config, source=None, instruction='', targets=None):
        with self.lock:
            if self.active: raise ValueError('已有任务正在运行，请等待完成或停止当前任务')
            if config['format_type'] not in ('word','latex'): raise ValueError('不支持的输出格式')
            if config['format_type']=='latex' and EDITION!='full': raise ValueError('Word 轻量版不支持 PDF')
            tid=uuid.uuid4().hex;config=json.loads(json.dumps(config))
            config['task_dir']=str(DATA_DIR/'uploads'/tid)
            t={'id':tid,'created':time.time(),'status':'processing','steps':[],'current_step':'准备中','config':config,'artifacts':[]}
            if source:
                if not source.get('section_contents'): raise ValueError('该任务没有可修订的报告')
                if not instruction.strip(): raise ValueError('请填写修订要求')
                if targets and not set(targets)<=set(KNOWN_SECTIONS): raise ValueError('未知章节')
                t.update(parent_id=source['id'],section_contents=dict(source['section_contents']),instruction=instruction,targets=targets)
            self.tasks[tid]=t;self.persist(t);self.active=tid
            ctx=mp.get_context('spawn');q=ctx.Queue();p=ctx.Process(target=execute,args=(t,q))
            self.process=p;p.start()
            threading.Thread(target=self._monitor,args=(tid,p,q),daemon=True).start()
            return tid
    def _monitor(self,tid,p,q):
        import queue
        while True:
            try:
                t=q.get(timeout=.3)
                with self.lock:
                    if self.tasks[tid]['status']=='interrupted': break
                    self.tasks[tid]=t;self.persist(t)
                if t['status']!='processing': break
            except queue.Empty:
                if not p.is_alive():
                    with self.lock:
                        if self.tasks[tid]['status']=='processing':
                            self.tasks[tid].update(status='error',error='任务进程意外退出');self.persist(self.tasks[tid])
                    break
        p.join(timeout=3)
        with self.lock:
            if self.active==tid: self.active=None;self.process=None
        q.close()
    def stop(self):
        import psutil
        with self.lock:
            p=self.process;tid=self.active
            if not p or not tid: return
            self.tasks[tid].update(status='interrupted',current_step='任务已停止');self.persist(self.tasks[tid])
            try:
                parent=psutil.Process(p.pid);children=parent.children(recursive=True)
                for child in children: child.terminate()
                # multiprocessing must reap its own child; psutil.wait() would steal its exit status.
                p.terminate();p.join(timeout=3)
                if p.is_alive():p.kill();p.join(timeout=3)
                _,alive=psutil.wait_procs(children,timeout=3)
                for child in alive: child.kill()
            except psutil.NoSuchProcess: pass
            p.join(timeout=3);self.active=None;self.process=None
