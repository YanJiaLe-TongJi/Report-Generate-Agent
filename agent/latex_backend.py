#!/usr/bin/env python3
"""
LaTeX 报告生成后端
"""

import os
import shutil
import subprocess
from pathlib import Path

from ai_generator import run_ai_generation
from constants import KNOWN_SECTIONS

# 路径定义
BASE_DIR = Path(__file__).parent
TEMPLATE_DIR = BASE_DIR / 'templates'
OUTPUT_FOLDER = BASE_DIR / 'outputs'
OUTPUT_FOLDER.mkdir(exist_ok=True)


def escape_latex(text):
    """Escape special LaTeX characters in plain text."""
    replacements = {
        '&': r'\&',
        '%': r'\%',
        '#': r'\#',
        '_': r'\_',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def find_xelatex():
    """优先使用 PATH 中的 xelatex；否则在 TEXLIVE_ROOT（默认 D:\texlive）下查找。"""
    exe = shutil.which('xelatex')
    if exe:
        return exe
    root = os.environ.get('TEXLIVE_ROOT', r'D:\texlive')
    root_path = Path(root)
    if not root_path.exists():
        return None
    bin_dirs = list(root_path.glob('*/bin/windows'))
    if not bin_dirs:
        bin_dirs = [root_path / 'bin' / 'windows'] if (root_path / 'bin' / 'windows').exists() else []
    for b in sorted(bin_dirs, reverse=True):
        exe_path = b / 'xelatex.exe'
        if exe_path.exists():
            return str(exe_path)
    return None


def build_latex_document(cover_info, section_contents, task_dir, raw_data_paths=None, material_paths=None, plot_paths=None, append_raw_data_image=False):
    """构建 LaTeX 文档"""
    template_path = TEMPLATE_DIR / 'main.tex'
    tex_content = template_path.read_text(encoding='utf-8')

    cover_map = {
        '%%COVER_EXPERIMENT_NAME%%': cover_info.get('experiment_name', ''),
        '%%COVER_STUDENT_NAME%%': cover_info.get('student_name', ''),
        '%%COVER_STUDENT_ID%%': cover_info.get('student_id', ''),
        '%%COVER_GROUP_NUMBER%%': cover_info.get('group_number', ''),
        '%%COVER_EXPERIMENT_DATE%%': cover_info.get('experiment_date', ''),
    }

    for placeholder, value in cover_map.items():
        escaped = escape_latex(value or '')
        tex_content = tex_content.replace(placeholder, escaped)

    for sec_name in KNOWN_SECTIONS:
        placeholder = f'%%SECTION_{sec_name}%%'
        content = section_contents.get(sec_name, '') or ''
        tex_content = tex_content.replace(placeholder, content)

    build_dir = Path(task_dir) / 'latex_build'
    build_dir.mkdir(exist_ok=True)

    img_src = TEMPLATE_DIR / 'img.png'
    logo_src = TEMPLATE_DIR / 'logo.jpg'
    if img_src.exists():
        shutil.copy2(img_src, build_dir / 'img.png')
    elif logo_src.exists():
        shutil.copy2(logo_src, build_dir / 'img.jpg')
        tex_content = tex_content.replace('img.png', 'img.jpg')

    # 复制系统资料中的图片到 build 目录，并替换占位符
    # 为避免中文/空格/特殊字符文件名导致 LaTeX 找不到文件，统一重命名为安全文件名。
    if material_paths:
        for idx, img_path in enumerate(material_paths):
            if Path(img_path).exists():
                ext = Path(img_path).suffix.lower()
                if ext in ['.jpg', '.jpeg', '.png']:
                    dest_name = f'material_{idx}{ext}'
                    dest_path = build_dir / dest_name
                    try:
                        shutil.copy2(img_path, dest_path)
                        # 替换 tex 中的占位符为安全文件名
                        tex_content = tex_content.replace(f'%%MATERIAL_IMAGE_{idx}%%', dest_name)
                    except Exception as e:
                        print(f"[WARNING] 复制图片失败 {img_path}: {e}")

    # 处理原始数据记录单图片（按开关决定是否附在文末）
    if append_raw_data_image and raw_data_paths:
        # 兼容单张图片路径字符串或列表
        if isinstance(raw_data_paths, str):
            raw_data_paths = [raw_data_paths]

        valid_raw_data = []
        for idx, raw_data_path in enumerate(raw_data_paths):
            if raw_data_path and Path(raw_data_path).exists():
                raw_data_ext = Path(raw_data_path).suffix.lower()
                if raw_data_ext in ['.jpg', '.jpeg', '.png']:
                    dest_name = f'raw_data_{idx}{raw_data_ext}'
                    try:
                        shutil.copy2(raw_data_path, build_dir / dest_name)
                        valid_raw_data.append(dest_name)
                    except Exception as e:
                        print(f"[WARNING] 复制原始数据记录单失败 {raw_data_path}: {e}")

        if valid_raw_data:
            # 构建原始数据记录单附录
            appendix_parts = ['\n\\clearpage\n\\section*{原始数据记录单}\n']

            for dest_name in valid_raw_data:
                # 优先替换占位符（兼容旧模板）
                placeholder = '%%RAW_DATA_IMAGE%%'
                if placeholder in tex_content:
                    tex_content = tex_content.replace(placeholder, dest_name, 1)  # 只替换一次
                else:
                    # 在文末追加图片
                    appendix_parts.append(
                        '\\noindent\\begin{center}\n'
                        f'\\includegraphics[width=0.9\\textwidth,height=0.78\\textheight,keepaspectratio]{{{dest_name}}}\n'
                        '\\\\[0.5em]\n'
                        f'{{\\small 原始数据记录单 {dest_name}}}\n'
                        '\\end{center}\n\\vspace{1em}\n'
                    )

            # 如果没有使用占位符，则在文末追加所有图片
            if '%%RAW_DATA_IMAGE%%' not in tex_content:
                appendix = '\n'.join(appendix_parts)
                tex_content = tex_content.replace('\\end{document}', appendix + '\\end{document}')

    # 复制 matplotlib 生成的数据图到 build 目录，并替换占位符
    if plot_paths:
        for idx, plot_path in enumerate(plot_paths):
            pp = Path(plot_path)
            if not pp.exists():
                continue
            ext = pp.suffix.lower()
            if ext not in ['.png', '.jpg', '.jpeg']:
                continue
            dest_name = f'plot_{idx}{ext}'
            try:
                shutil.copy2(pp, build_dir / dest_name)
                tex_content = tex_content.replace(f'%%PLOT_IMAGE_{idx}%%', dest_name)
            except Exception as e:
                print(f"[WARNING] 复制数据图失败 {plot_path}: {e}")
    
    tex_path = build_dir / 'report.tex'
    tex_path.write_text(tex_content, encoding='utf-8')
    return str(tex_path)


def compile_latex(tex_path, output_pdf_path):
    """编译 LaTeX 到 PDF"""
    build_dir = str(Path(tex_path).parent)
    xelatex = find_xelatex()
    if not xelatex:
        return False, '未找到 xelatex。请将 TeX Live 加入 PATH'
    
    cmd = [
        xelatex,
        '-interaction=nonstopmode',
        '-halt-on-error',
        f'-output-directory={build_dir}',
        tex_path,
    ]
    
    full_log = ''
    for pass_num in range(2):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=120, cwd=build_dir,
            )
            full_log += f'\n=== Pass {pass_num + 1} ===\n'
            stdout = result.stdout or ''
            stderr = result.stderr or ''
            full_log += stdout[-3000:] if len(stdout) > 3000 else stdout
            if result.returncode != 0:
                full_log += '\n--- STDERR ---\n' + stderr[-2000:]
                return False, full_log
        except FileNotFoundError:
            return False, full_log + '\nxelatex 执行失败'
        except subprocess.TimeoutExpired:
            return False, '编译超时（>120秒）'
    
    pdf_path = Path(tex_path).with_suffix('.pdf')
    if pdf_path.exists():
        shutil.copy2(pdf_path, output_pdf_path)
        return True, full_log
    return False, full_log + '\n编译完成但未找到 PDF'


def run_latex_generation(task_id, config, tasks_dict):
    """
    运行 LaTeX 完整生成流程
    tasks_dict: 从主 app 传入的任务状态字典
    """
    def _update(task_id, step):
        if task_id in tasks_dict:
            tasks_dict[task_id]['steps'].append(step)
            tasks_dict[task_id]['current_step'] = step
    
    # 运行 AI 生成
    section_contents = run_ai_generation(task_id, config, tasks_dict, format_type='latex')
    
    if not section_contents:
        return False
    
    # 构建 LaTeX 文档
    _update(task_id, '正在构建 LaTeX 文档...')
    tex_path = build_latex_document(
        config['cover_info'], section_contents, config['task_dir'],
        config.get('raw_data_paths'),
        config.get('material_paths'),
        config.get('plot_paths'),
        config.get('append_raw_data_image', False)
    )
    
    # 编译 PDF
    _update(task_id, '正在编译 PDF...')
    out_name = f'实验报告_{task_id[:8]}.pdf'
    out_path = str(OUTPUT_FOLDER / out_name)
    success, compile_log = compile_latex(tex_path, out_path)
    
    if not success:
        _update(task_id, '编译失败，保存源码...')
        tex_fallback_name = f'实验报告_{task_id[:8]}.tex'
        shutil.copy2(tex_path, OUTPUT_FOLDER / tex_fallback_name)

        log_tail = (compile_log or '')[-1200:]
        tasks_dict[task_id]['status'] = 'error'
        tasks_dict[task_id]['error'] = (
            'LaTeX 编译失败，已保存 .tex 源文件供手动编译。'
            + (f'\n\n编译日志（末尾）:\n{log_tail}' if log_tail else '')
        )
        tasks_dict[task_id]['download_url'] = f'/api/download/{tex_fallback_name}'
        return False
    
    # 保存原始内容
    raw_name = f'报告原文_{task_id[:8]}.md'
    raw_path = OUTPUT_FOLDER / raw_name
    raw_parts = []
    for sn in KNOWN_SECTIONS:
        raw_parts.append(f'## {sn}\n\n{section_contents.get(sn, "(空)")}\n')
    raw_path.write_text('\n'.join(raw_parts), encoding='utf-8')
    
    # 保存源码
    tex_save_name = f'报告源码_{task_id[:8]}.tex'
    shutil.copy2(tex_path, OUTPUT_FOLDER / tex_save_name)
    
    tasks_dict[task_id]['status'] = 'done'
    tasks_dict[task_id]['download_url'] = f'/api/download/{out_name}'
    tasks_dict[task_id]['raw_url'] = f'/api/download/{raw_name}'
    tasks_dict[task_id]['tex_url'] = f'/api/download/{tex_save_name}'
    tasks_dict[task_id]['section_contents'] = section_contents
    _update(task_id, '报告生成完成！')
    
    return True
