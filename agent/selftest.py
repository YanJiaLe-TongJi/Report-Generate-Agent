"""Offline packaged-runtime smoke test. Never calls a model or credential store."""
import json
from pathlib import Path
from paths import DATA_DIR, EDITION
from harness import render
from storage import write_json

def run():
    import time
    from PIL import Image,ImageDraw
    from word_backend import build_word_document_from_template
    folder=DATA_DIR/'smoke';folder.mkdir(parents=True,exist_ok=True)
    image=folder/'原始记录.png';im=Image.new('RGB',(600,220),'white');ImageDraw.Draw(im).text((30,40),'U (V): 1.0  2.0  3.0\nI (mA): 10.0 20.0 30.0',fill='black',font_size=28);im.save(image)
    sections={
      '实验名称':'伏安法测量电阻（演示数据）',
      '实验目的':'学习伏安法测量电阻的方法，理解电压与电流之间的关系。',
      '实验原理':r'欧姆定律为 \(R=U/I\)。当温度保持不变时，电压与电流成正比。',
      '实验内容':'连接电路，逐步改变电源电压，记录电压和电流。以下数值仅用于软件测试。',
      '实验仪器':'直流电源、电压表、电流表、电阻及导线。',
      '数据记录处理':r'''\begin{tabular}{ccc}
\toprule
序号 & 电压（V） & 电流（mA） \\
\midrule
1 & 1.0 & 10.0 \\
2 & 2.0 & 20.0 \\
3 & 3.0 & 30.0 \\
\bottomrule
\end{tabular}

由第一组演示数据可得 \(R=1.0/0.010=100\,\Omega\)。其余两组计算结果相同。''',
      '思考题':'温度变化可能改变电阻，测量过程中应控制电流，避免明显发热。',
      '讨论与分析':r'不确定度传播满足 \(u_R=R\sqrt{(u_U/U)^2+(u_I/I)^2}\)。未提供仪表精度，因此不报告数值不确定度。',
      '实验结论':r'演示数据对应电阻为 \(100\,\Omega\)，此结果仅用于验证软件输出。',
    }
    task={'id':'offline-smoke-word','created':time.time(),'status':'done','steps':[],'current_step':'离线打包验证','section_contents':sections,'config':{'task_dir':str(folder),'cover_info':{'experiment_name':'伏安法测量电阻','student_name':'演示学生','student_id':'TEST001','group_number':'1','experiment_date':'2026-09-10'},'format_type':'word','raw_data_paths':[str(image)],'material_paths':[],'append_raw_data_image':True}}
    render(task);write_json(DATA_DIR/'tasks'/ (task['id']+'.json'),task)
    if EDITION=='full':
        task=dict(task,id='offline-smoke-pdf',config=dict(task['config'],format_type='latex'))
        render(task);write_json(DATA_DIR/'tasks'/(task['id']+'.json'),task)
    print(json.dumps({'ok':True,'edition':EDITION,'outputs':str(DATA_DIR/'outputs')},ensure_ascii=False))
