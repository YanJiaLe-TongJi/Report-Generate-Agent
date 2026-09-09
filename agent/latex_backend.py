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

from paths import RESOURCE_DIR, DATA_DIR
BASE_DIR = RESOURCE_DIR
TEMPLATE_DIR = RESOURCE_DIR / "templates"
UPLOAD_FOLDER = DATA_DIR / "uploads"
OUTPUT_FOLDER = DATA_DIR / "outputs"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)



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
    runtime = RESOURCE_DIR / 'texlive'
    candidates = list(runtime.glob('bin/*/xelatex')) + list(runtime.glob('bin/*/xelatex.exe'))
    return str(candidates[0]) if candidates else None


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
                        f'{{\\small 原始数据记录单 {escape_latex(dest_name)}}}\n'
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
        return False, '完整版内置 LaTeX 环境缺失，请重新安装完整版'
    
    cmd = [
        xelatex,
        '-interaction=nonstopmode',
        '-halt-on-error',
        '-no-shell-escape',
        f'-output-directory={build_dir}',
        tex_path,
    ]
    
    full_log = ''
    for pass_num in range(2):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=120, cwd=build_dir,
                env={**os.environ, 'openin_any':'p', 'openout_any':'p', 'TEXMFVAR':str(DATA_DIR/'tex-cache'), 'TEXMFCONFIG':str(DATA_DIR/'tex-config')},
                encoding='utf-8', errors='replace',
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
