#!/usr/bin/env python3
"""
AI 生成模块 - 处理与 Kimi API 的交互
"""

import time
import csv
import shutil
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from openpyxl import load_workbook
from docx import Document
from pathlib import Path

from constants import KNOWN_SECTIONS
from plot_utils import generate_data_plots

# 路径定义
BASE_DIR = Path(__file__).parent
UPLOAD_FOLDER = BASE_DIR / 'uploads'
OUTPUT_FOLDER = BASE_DIR / 'outputs'
UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)


def _update(task_id, step, tasks_dict):
    """更新任务进度"""
    if task_id in tasks_dict:
        tasks_dict[task_id]['steps'].append(step)
        tasks_dict[task_id]['current_step'] = step


def parse_excel_data(excel_paths):
    """解析 Excel 数据文件，返回 (all_data, diagnostics)"""
    all_data = {}
    def _load_workbook_robust(file_path, data_only):
        """兼容无后缀但实际为 xlsx 的文件。"""
        try:
            return load_workbook(file_path, data_only=data_only)
        except Exception as e:
            msg = str(e).lower()
            p = Path(file_path)
            if 'does not support file format' in msg and p.suffix.lower() == '':
                tmp_path = None
                try:
                    with open(file_path, 'rb') as rf:
                        sig = rf.read(4)
                    if sig.startswith(b'PK'):
                        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tf:
                            tmp_path = tf.name
                        shutil.copy2(file_path, tmp_path)
                        return load_workbook(tmp_path, data_only=data_only)
                finally:
                    if tmp_path:
                        Path(tmp_path).unlink(missing_ok=True)
            raise

    diagnostics = {
        'parsed_files': [],
        'failed_files': [],
    }

    def _normalize_rows(rows):
        normalized = []
        for row in rows:
            cells = []
            for c in row:
                if c is None:
                    cells.append('')
                elif isinstance(c, float):
                    cells.append(f'{c:.6g}')
                else:
                    cells.append(str(c))
            normalized.append(cells)
        return [r for r in normalized if any((x or '').strip() for x in r)]

    for path in excel_paths:
        p = Path(path)
        if not p.exists():
            continue
        file_data = {}
        try:
            if p.suffix.lower() == '.csv':
                rows = None
                for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
                    try:
                        with open(path, 'r', encoding=enc, newline='') as f:
                            rows = list(csv.reader(f))
                        break
                    except Exception:
                        rows = None
                if rows is None:
                    raise ValueError('CSV 编码无法识别')
                rows = _normalize_rows(rows)
                if rows:
                    file_data['CSV'] = rows
            elif p.suffix.lower() == '.xls':
                # 兼容旧版 .xls（依赖 xlrd；若环境中缺失会自动跳过）
                import xlrd  # type: ignore
                book = xlrd.open_workbook(path)
                for sn in book.sheet_names():
                    sh = book.sheet_by_name(sn)
                    rows = [sh.row_values(r) for r in range(sh.nrows)]
                    rows = _normalize_rows(rows)
                    if rows:
                        file_data[sn] = rows
            else:
                # 先读 data_only=True（拿数值结果）；若读不到有效数据，回退到 data_only=False
                wb = _load_workbook_robust(path, data_only=True)
                for sn in wb.sheetnames:
                    ws = wb[sn]
                    rows = [row for row in ws.iter_rows(values_only=True)]
                    rows = _normalize_rows(rows)
                    if rows:
                        file_data[sn] = rows

                if not file_data:
                    wb_formula = _load_workbook_robust(path, data_only=False)
                    for sn in wb_formula.sheetnames:
                        ws = wb_formula[sn]
                        rows = [row for row in ws.iter_rows(values_only=True)]
                        rows = _normalize_rows(rows)
                        if rows:
                            file_data[sn] = rows
        except Exception as e:
            diagnostics['failed_files'].append({'file': p.name, 'reason': str(e)})
            continue
        if file_data:
            all_data[p.name] = file_data
            diagnostics['parsed_files'].append({
                'file': p.name,
                'sheet_count': len(file_data),
            })
        else:
            diagnostics['failed_files'].append({'file': p.name, 'reason': '未读到非空数据行'})
    return all_data, diagnostics


def format_excel_for_prompt(excel_data):
    """格式化 Excel 数据用于提示词"""
    if not excel_data:
        return ""
    parts = []
    for fn, sheets in excel_data.items():
        parts.append(f"\n=== 数据文件: {fn} ===")
        for sn, rows in sheets.items():
            parts.append(f"--- 工作表: {sn} ---")
            for row in rows:
                parts.append(" | ".join(row))
    return "\n".join(parts)


def _is_section_header(para, names=None):
    """判断段落是否为章节标题"""
    names = names or KNOWN_SECTIONS
    text = para.text.strip()
    if text not in names:
        return False
    return any(r.bold for r in para.runs if r.bold is not None)


def _is_binding_decoration(para):
    """判断是否为装订线装饰"""
    return para.text.strip() in ('┊', '装', '订', '线')


def extract_example_sections(docx_path):
    """从参考样例中提取章节内容"""
    doc = Document(docx_path)
    sections = {}
    current = None
    lines = []
    
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text or _is_binding_decoration(para):
            continue
        if _is_section_header(para):
            if current:
                sections[current] = '\n'.join(lines).strip()
            current = text
            lines = []
        elif current:
            lines.append(text)
    
    if current:
        sections[current] = '\n'.join(lines).strip()
    return sections


def extract_single_image(path, client, model):
    """使用 Vision API 提取图片内容，并判断所属章节"""
    from utils import encode_image_to_base64, resize_image_if_needed, get_mime_type
    
    b64, mime = resize_image_if_needed(path)
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": (
                    "请分析这张实验资料图片，并按以下格式输出：\n\n"
                    "【图片类型】：选择最符合的一项：实验原理图 / 实验装置图 / 实验仪器图 / "
                    "实验内容步骤图 / 数据表格图 / 其他\n"
                    "【页面形态】：局部图（仅装置或表格局部）/ 整页文档截图（包含大段正文）\n"
                    "【所属章节】：选择最符合的一项：实验原理 / 实验仪器 / 实验内容 / 数据记录处理 / 其他\n"
                    "【图片描述】：用文字详细描述图中内容（包括各部件名称、连接方式、结构布局等）\n"
                    "【图中文字】：提取图片中的所有文字内容，保持原文\n\n"
                    "注意：\n"
                    "- 实验原理图（公式、理论示意图）→ 属于 实验原理\n"
                    "- 实验装置图（整体装置结构）→ 属于 实验原理 或 实验仪器\n"
                    "- 仪器细节图（单个仪器的外观、参数）→ 属于 实验仪器\n"
                    "- 实验步骤截图（操作界面、过程图）→ 属于 实验内容\n"
                    "- 数据记录表格 → 属于 数据记录处理\n"
                    "- 若画面主要是教材/讲义/实验指导书整页正文，页面形态应标注为“整页文档截图”"
                )}
            ]
        }],
    )
    return resp.choices[0].message.content or ''


def extract_raw_data_from_image(path, client, model):
    """从原始数据记录单图片提取数据表格"""
    from utils import encode_image_to_base64, resize_image_if_needed, get_mime_type
    
    b64, mime = resize_image_if_needed(path)
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": (
                    "这是一张物理实验的原始数据记录单图片。请提取其中的所有数据表格内容。\n\n"
                    "要求：\n"
                    "1. 完整提取所有表格数据，包括表头、行数据\n"
                    "2. 保持数据的原始格式和单位\n"
                    "3. 以文本表格形式输出，用 | 分隔列，用换行分隔行\n"
                    "4. 如果有多个表格，用分隔线 === 分隔\n"
                    "5. 提取所有数字数据，包括原始测量值、计算结果等\n\n"
                    "请只输出提取的数据表格内容，不要添加解释或分析。"
                )}
            ]
        }],
    )
    return resp.choices[0].message.content or ''


def _generate_section(client, model, system_prompt, section_name,
                      materials_text, data_text, example_text, extra_hint='',
                      format_type='latex', material_paths=None, plot_infos=None,
                      allowed_material_indices=None, allow_material_images=True):
    """生成单个章节内容"""
    # 构建图片信息说明
    image_info = ''
    has_material_filter = allowed_material_indices is not None
    allowed_set = set(allowed_material_indices or [])
    if allow_material_images and material_paths and len(material_paths) > 0:
        image_info = '\n\n=== 本章节可用图片 ===\n'
        for i, path in enumerate(material_paths):
            # 显式传入空列表表示“本章节不允许任何资料图”
            if has_material_filter and i not in allowed_set:
                continue
            filename = Path(path).name
            if format_type == 'latex':
                image_info += f'图片 {i}: %%MATERIAL_IMAGE_{i}%% (原文件名: {filename})\n'
            else:
                image_info += f'图片 {i}: [IMAGE:{i}] (原文件名: {filename})\n'
        if image_info != '\n\n=== 本章节可用图片 ===\n':
            image_info += '=== 图片列表结束 ===\n'
            image_info += '注意：以上图片仅在本章节可用，如果某张图片与当前章节内容无关，请不要插入。\n'
        else:
            image_info = ''
    # 构建可用数据图说明
    plot_info = ''
    if plot_infos:
        plot_info = '\n\n=== 本章节可用数据图（由系统根据实验数据自动绘制） ===\n'
        for plot in plot_infos:
            idx = plot.get('index')
            title = plot.get('title', '')
            x_label = plot.get('x_label', '')
            y_label = plot.get('y_label', '')
            chart_type = plot.get('chart_type', 'unknown')
            has_error_bar = plot.get('has_error_bar', False)
            fit_equation = plot.get('fit_equation') or ''
            caption = plot.get('caption') or ''
            if format_type == 'latex':
                plot_info += (
                    f'数据图 {idx}: %%PLOT_IMAGE_{idx}%% '
                    f'(标题: {title}; 图类型: {chart_type}; X轴: {x_label}; Y轴: {y_label}; '
                    f'误差棒: {"是" if has_error_bar else "否"}; '
                    f'拟合: {fit_equation if fit_equation else "无"}; 推荐图注: {caption})\n'
                )
            else:
                plot_info += (
                    f'数据图 {idx}: [PLOT:{idx}] '
                    f'(标题: {title}; 图类型: {chart_type}; X轴: {x_label}; Y轴: {y_label}; '
                    f'误差棒: {"是" if has_error_bar else "否"}; '
                    f'拟合: {fit_equation if fit_equation else "无"}; 推荐图注: {caption})\n'
                )
        plot_info += '=== 数据图列表结束 ===\n'
        plot_info += '注意：仅当当前章节明确需要数据图支撑结论时再插入，不需要时不要强行插图。\n'
    example_block = ''
    if example_text:
        example_block = (
            f"\n\n=== 以下是一份参考样例中「{section_name}」部分的内容，"
            f"请参考其写作风格和详细程度 ===\n{example_text}\n=== 样例结束 ===\n"
        )
    
    # 根据格式类型调整提示词
    if format_type == 'latex':
        format_constraints = """
重要约束（你的输出将直接嵌入 LaTeX 文档，必须是合法 LaTeX）：
- 严格根据上面提供的实验资料内容撰写，如无必要请直接摘抄原文
- 如果资料中出现“思考题/问答题/讨论题”，必须完整作答并写入「思考题」章节
- 数学公式使用标准 LaTeX：行内用 \\(...\\)，独立公式用 \\[...\\] 或 equation 环境
- 表格用 LaTeX table/tabular 环境，配合 booktabs 宏包（\\toprule \\midrule \\bottomrule）
- 列表用 \\begin{{itemize}} 或 \\begin{{enumerate}}
- 加粗用 \\textbf{{}}
- 绝对不要使用任何 Markdown 语法（禁止 **加粗**、禁止 |表格|、禁止 # 标题、禁止 $公式$）
- 如果表格列数较多可能超宽，使用 \\small 或 \\footnotesize 缩小字体，或使用 p{3cm} 等固定列宽让内容自动换行
- 如果提供的实验资料中包含图片，请根据图片内容决定是否插入
- 只有当图片确实与当前章节「{section_name}」内容直接相关时才插入
- 若同章节同时有“整页文档截图”和“局部图”，只能插入局部图，禁止插入整页文档截图
- 禁止插入包含大段正文文字的页面截图（教材页/讲义页/整页扫描）
- 如果数据图列表已提供，且当前章节存在“曲线趋势分析、线性拟合、对比展示”等需求，可插入数据图
- 如果提供的数据图元信息里包含“推荐图注/拟合方程/误差棒”，请在正文分析里优先使用这些信息
- 插入图片时使用以下 LaTeX 代码格式（使用 %%MATERIAL_IMAGE_X%% 作为占位符，X为图片编号）：
  \\begin{figure}[htbp]
  \\centering
  \\includegraphics[width=0.6\\textwidth]{%%MATERIAL_IMAGE_X%%}
  \\caption{图片标题}
  \\end{figure}
- 插入数据图时同样使用 figure 环境，将占位符改为 %%PLOT_IMAGE_X%%（X 为数据图编号）
- 文字内容中引用图片时请使用 "如图所示" 的形式
- 若当前章节为「数据记录处理」，仅保留必要的原始数据表、核心公式与最终结果（含单位和有效数字）
- 禁止展开冗长代入步骤与逐步中间计算，除非某个关键结果无法理解时才给出一行简要说明
- 禁止输出任何模板占位符（例如：____、待补充、TBD、XXX），缺失数据时请明确写出“缺少哪些原始数据，无法继续计算”
- 禁止输出 \\section、\\subsection 等章节命令；只输出本章节正文
- 直接输出「{section_name}」的正文内容，不要输出 \\section 标题，不要加多余说明"""
    else:  # word
        format_constraints = """
重要约束（你的输出将直接用于生成 Word 文档）：
- 严格根据上面提供的实验资料内容撰写，如无必要请直接摘抄原文
- 如果资料中出现“思考题/问答题/讨论题”，必须完整作答并写入「思考题」章节
- 数学公式必须使用 LaTeX 语法并用 $...$（行内）或 $$...$$（独立）包裹，例如 $E=mc^2$、$\\frac{a+b}{c+d}$、$$\\int_0^1 x^2\\,dx$$
- 公式中请使用标准 LaTeX 命令（\\frac \\sqrt \\sum \\alpha 等），系统会自动转换为 Word 可编辑公式（OMML）
- 表格请优先使用 LaTeX tabular 环境，例如：
  \\begin{tabular}{ccc}
  列1 & 列2 & 列3 \\\\
  数据1 & 数据2 & 数据3 \\\\
  \\end{tabular}
- 若使用 LaTeX 表格，不要混用 Markdown 表格语法
- 使用纯文本格式，不要使用 Markdown 语法（不要用 **加粗**、不要用 # 标题）
- 如果提供的实验资料中包含图片，请根据图片内容决定是否插入
- 只有当图片确实与当前章节「{section_name}」内容直接相关时才插入
- 若同章节同时有“整页文档截图”和“局部图”，只能插入局部图，禁止插入整页文档截图
- 禁止插入包含大段正文文字的页面截图（教材页/讲义页/整页扫描）
- 在需要插入图片的位置使用标记 [IMAGE:X]，其中 X 是图片编号
- 如果数据图列表已提供，且当前章节确实需要图示趋势，请使用标记 [PLOT:X] 插入数据图（X 为数据图编号）
- 如果提供的数据图元信息里包含“推荐图注/拟合方程/误差棒”，请在文字分析中优先引用这些信息
- 文字内容中引用图片时请使用 "如图所示" 的形式
- 若当前章节为「数据记录处理」，仅保留必要的原始数据表、核心公式与最终结果（含单位和有效数字）
- 禁止展开冗长代入步骤与逐步中间计算，除非某个关键结果无法理解时才给出一行简要说明
- 禁止输出任何模板占位符（例如：____、待补充、TBD、XXX），缺失数据时请明确写出“缺少哪些原始数据，无法继续计算”
- 禁止输出任何新章节标题（如“##”“第X节”）
- 直接输出「{section_name}」的正文内容
- 保持段落清晰，适当换行"""
    
    user_msg = f"""请为物理实验报告的「{section_name}」部分撰写内容。

=== 实验资料 ===
{materials_text}
{image_info}
{plot_info}

{f'=== 实验数据 ==={chr(10)}{data_text}' if data_text else ''}
{example_block}
{extra_hint}
{format_constraints}"""

    if data_text:
        user_msg += (
            "\n\n【数据可用性约束】\n"
            "系统已提供结构化实验数据（Excel/CSV 或其等效提取结果）。"
            "禁止写“未提供数据/缺少数据无法计算”等结论；"
            "必须直接基于已提供数据完成计算与分析。"
        )
    else:
        user_msg += (
            "\n\n【数据可用性约束】\n"
            "当前未提供可用于计算的结构化实验数据。"
            "若必须给出数值结论，请明确指出缺失字段。"
        )
    
    extra_body = None
    model_name = str(model or '').lower()
    if model_name.startswith('kimi'):
        # 通过 extra_body 透传厂商扩展参数，避免 SDK 关键字参数报错
        extra_body = {'thinking': {'type': 'disabled'}}
    section_max_tokens = 8192 if section_name == '数据记录处理' else 4096
    
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.6,
        max_tokens=section_max_tokens,
        extra_body=extra_body,
    )
    raw = resp.choices[0].message.content
    return (raw or '').strip()


def _generate_one_section(args):
    """Helper for thread pool"""
    client, model, system_prompt, sec_name, materials_text, data_text, example_text, extra, format_type, material_paths, plot_infos, allowed_material_indices, allow_material_images = args
    try:
        content = _generate_section(
            client, model, system_prompt, sec_name,
            materials_text, data_text, example_text, extra, format_type,
            material_paths, plot_infos, allowed_material_indices, allow_material_images
        )
        return (sec_name, content, None)
    except Exception as e:
        return (sec_name, '', str(e))


def _deduplicate_images(section_contents, format_type):
    """
    去除重复插入的图片标记
    每张图片只保留第一次出现的位置
    """
    import re
    
    # 追踪已经使用过的图片
    used_images = set()
    
    # 按优先级排序章节（实验原理、实验仪器优先，实验内容和实验目的次之）
    priority_order = ['实验原理', '实验仪器', '实验内容', '实验目的', '讨论与分析', '实验结论', '实验名称', '数据记录处理']
    
    # 对章节进行排序
    sorted_sections = sorted(
        section_contents.items(),
        key=lambda x: priority_order.index(x[0]) if x[0] in priority_order else 999
    )
    
    result = {}
    
    for sec_name, content in sorted_sections:
        if not content:
            result[sec_name] = content
            continue
            
        if format_type == 'latex':
            # LaTeX 格式：匹配 %%MATERIAL_IMAGE_X%%
            pattern = r'\\includegraphics\[[^\]]*\]\{%%MATERIAL_IMAGE_(\d+)%%\}'
            
            def replace_latex_img(match):
                img_idx = match.group(1)
                key = f'latex_{img_idx}'
                if key in used_images:
                    # 已使用过，删除此图片插入
                    return ''
                used_images.add(key)
                return match.group(0)
            
            content = re.sub(pattern, replace_latex_img, content)
            # 同样处理数据图占位符
            plot_pattern = r'\\includegraphics\[[^\]]*\]\{%%PLOT_IMAGE_(\d+)%%\}'

            def replace_latex_plot(match):
                plot_idx = match.group(1)
                key = f'latex_plot_{plot_idx}'
                if key in used_images:
                    return ''
                used_images.add(key)
                return match.group(0)

            content = re.sub(plot_pattern, replace_latex_plot, content)
            
            # 清理所有没有 includegraphics 的 figure 环境（包括有 caption 的）
            # 匹配 figure 环境，但只删除那些内部没有 \includegraphics 的
            def remove_empty_figures(match):
                figure_content = match.group(0)
                # 如果 figure 环境内没有 includegraphics，则删除整个环境
                if '\\includegraphics' not in figure_content:
                    return ''
                return figure_content
            
            # 使用正则匹配整个 figure 环境
            content = re.sub(
                r'\\begin\{figure\}(?:\[[^\]]*\])?\s*(.*?)\\end\{figure\}',
                remove_empty_figures,
                content,
                flags=re.DOTALL
            )
        else:
            # Word 格式：匹配 [IMAGE:X]
            pattern = r'\[IMAGE:(\d+)\]'
            
            def replace_word_img(match):
                img_idx = match.group(1)
                key = f'word_{img_idx}'
                if key in used_images:
                    # 已使用过，删除此图片标记
                    return ''
                used_images.add(key)
                return match.group(0)
            
            content = re.sub(pattern, replace_word_img, content)
            # Word 格式：匹配 [PLOT:X]
            plot_pattern = r'\[PLOT:(\d+)\]'

            def replace_word_plot(match):
                plot_idx = match.group(1)
                key = f'word_plot_{plot_idx}'
                if key in used_images:
                    return ''
                used_images.add(key)
                return match.group(0)

            content = re.sub(plot_pattern, replace_word_plot, content)
        
        result[sec_name] = content
    
    return result


def _parse_image_section_mapping(image_contents):
    """
    解析图片内容，提取每张图片应该属于哪个章节
    返回字典：{章节名: [图片索引列表]}
    """
    import re
    
    section_mapping = {
        '实验原理': [],
        '实验仪器': [],
        '实验内容': [],
        '数据记录处理': [],
        '其他': []
    }
    
    for idx, ic in enumerate(image_contents):
        content = ic.get('content', '')
        # 代码层硬过滤：整页文档截图一律不参与正文插图分配
        page_shape = ''
        shape_match = re.search(r'【页面形态】[:：]\s*([^\n\r]+)', content)
        if shape_match:
            page_shape = shape_match.group(1).strip()
        if ('整页文档截图' in page_shape) or ('教材' in content and '整页' in content):
            section_mapping['其他'].append(idx)
            continue
        # 尝试从内容中提取【所属章节】
        match = re.search(r'【所属章节】[:：]\s*(\S+)', content)
        if match:
            section = match.group(1).strip()
            # 映射到标准章节名
            if '原理' in section:
                section_mapping['实验原理'].append(idx)
            elif '仪器' in section:
                section_mapping['实验仪器'].append(idx)
            elif '内容' in section or '步骤' in section:
                section_mapping['实验内容'].append(idx)
            elif '数据' in section:
                section_mapping['数据记录处理'].append(idx)
            else:
                section_mapping['其他'].append(idx)
        else:
            # 无法识别，默认归到实验原理
            section_mapping['实验原理'].append(idx)
    
    return section_mapping


def _sanitize_section_content(content):
    """清理模型误输出的跨章节标题和模板占位符。"""
    import re
    if not content:
        return content

    def _count_unescaped_dollar(text):
        count = 0
        escaped = False
        for ch in text:
            if escaped:
                escaped = False
                continue
            if ch == '\\':
                escaped = True
                continue
            if ch == '$':
                count += 1
        return count

    def _drop_last_unescaped_dollar(text):
        chars = list(text)
        escaped = False
        last_idx = -1
        for i, ch in enumerate(chars):
            if escaped:
                escaped = False
                continue
            if ch == '\\':
                escaped = True
                continue
            if ch == '$':
                last_idx = i
        if last_idx >= 0:
            del chars[last_idx]
        return ''.join(chars)

    def _balance_latex_environments(text):
        """
        兜底修复：若模型输出截断导致环境未闭合（如 tabular/table/figure），自动补齐 \end{...}。
        仅处理最常见环境，避免过度修正。
        """
        begin_pat = re.compile(r'\\begin\{([a-zA-Z*]+)\}')
        end_pat = re.compile(r'\\end\{([a-zA-Z*]+)\}')
        closable = {
            'tabular', 'table', 'figure', 'itemize', 'enumerate', 'equation',
            'align', 'align*', 'center', 'longtable', 'tabularx'
        }
        stack = []
        for ln in text.splitlines():
            for m in begin_pat.finditer(ln):
                env = m.group(1)
                if env in closable:
                    stack.append(env)
            for m in end_pat.finditer(ln):
                env = m.group(1)
                if env in closable:
                    for i in range(len(stack) - 1, -1, -1):
                        if stack[i] == env:
                            del stack[i]
                            break
        if not stack:
            return text
        tail = '\n'.join(f'\\end{{{env}}}' for env in reversed(stack))
        return text.rstrip() + '\n' + tail + '\n'

    lines = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if re.match(r'^\s*\\section\*?\{.*\}\s*$', line):
            continue
        if re.match(r'^\s*\\subsection\*?\{.*\}\s*$', line):
            continue
        if re.match(r'^\s*#{1,6}\s+', line):
            continue
        lines.append(raw_line)

    cleaned = '\n'.join(lines)
    cleaned = re.sub(r'_{3,}', '（数据缺失）', cleaned)
    # 兜底：若模型输出被截断导致 $ 未配对，删除最后一个未转义的 $
    if _count_unescaped_dollar(cleaned) % 2 == 1:
        cleaned = _drop_last_unescaped_dollar(cleaned)
    cleaned = _balance_latex_environments(cleaned)
    return cleaned.strip()


def run_ai_generation(task_id, config, tasks_dict, format_type='latex'):
    """
    运行 AI 生成流程
    format_type: 'latex' 或 'word'
    tasks_dict: 任务状态字典
    """
    try:
        _update(task_id, '正在初始化 API...', tasks_dict)

        api_key = config['api_key']
        client = OpenAI(api_key=api_key, base_url=config['base_url'])

        # Phase 0: 资料识别
        image_contents = []
        if config['material_paths']:
            total = len(config['material_paths'])
            for i, path in enumerate(config['material_paths']):
                _update(task_id, f'正在识别资料图片 ({i + 1}/{total})...', tasks_dict)
                content = extract_single_image(path, client, config['vision_model'])
                image_contents.append({'page': i + 1, 'path': path, 'content': content})
                if i < total - 1:
                    time.sleep(0.5)

        # 解析每张图片应该属于哪个章节
        _update(task_id, '正在分析图片所属章节...', tasks_dict)
        image_section_mapping = _parse_image_section_mapping(image_contents)
        
        # 为每个章节构建专属的材料文本（只包含分配给该章节的图片）
        def build_section_materials(sec_name, all_materials_text, mapping, img_contents):
            """构建只包含分配给该章节的图片的材料文本"""
            # 获取分配给该章节的图片索引
            assigned_indices = mapping.get(sec_name, [])
            
            if not assigned_indices:
                # 如果没有分配图片，不给该章节任何图片材料，避免误插整页图
                return ''
            
            # 构建只包含分配图片的材料文本
            section_parts = []
            for idx in assigned_indices:
                if idx < len(img_contents):
                    ic = img_contents[idx]
                    section_parts.append(f"=== 资料第{ic['page']}页 ===\n{ic['content']}")
            
            return "\n\n".join(section_parts)

        materials_text = "\n\n".join(
            f"=== 资料第{ic['page']}页 ===\n{ic['content']}" for ic in image_contents
        )

        _update(task_id, '正在解析实验数据...', tasks_dict)
        data_paths = config.get('data_paths') or []
        excel_data, parse_diag = parse_excel_data(data_paths) if data_paths else ({}, {'parsed_files': [], 'failed_files': []})
        data_text = format_excel_for_prompt(excel_data)
        if data_paths:
            parsed_count = len(parse_diag.get('parsed_files', []))
            failed_count = len(parse_diag.get('failed_files', []))
            _update(task_id, f'实验数据文件 {len(data_paths)} 个：成功解析 {parsed_count} 个，失败 {failed_count} 个', tasks_dict)
        if data_paths and not data_text:
            file_names = ', '.join(Path(p).name for p in data_paths[:5])
            _update(
                task_id,
                f'⚠ 未从数据文件解析到可用表格（{file_names}），请优先使用 .xlsx/.csv 或检查工作表内容是否为空',
                tasks_dict
            )
            if parse_diag.get('failed_files'):
                first = parse_diag['failed_files'][0]
                _update(task_id, f"⚠ 示例失败原因：{first.get('file')} -> {first.get('reason', '未知错误')[:120]}", tasks_dict)
        elif data_paths:
            _update(task_id, f'✓ 已解析 {len(excel_data)} 个数据文件', tasks_dict)
        _update(task_id, '正在使用 matplotlib 生成候选数据图...', tasks_dict)
        plot_infos, plot_diag = generate_data_plots(
            config.get('data_paths') or [], config.get('task_dir'), with_diagnostics=True
        )
        for fs in (plot_diag.get('file_stats') or []):
            if fs.get('status') in ('no_numeric', 'no_plot', 'empty', 'error', 'missing'):
                _update(
                    task_id,
                    f"图表诊断：{fs.get('file', '未知文件')} -> {fs.get('reason', fs.get('status', '未生成图表'))}",
                    tasks_dict
                )
        _update(task_id, f"图表生成结果：{len(plot_infos)} 张候选图", tasks_dict)
        config['plot_paths'] = [p['path'] for p in plot_infos]
        config['plot_infos'] = plot_infos

        # 仅在“没有上传结构化数据文件”时，才从原始数据记录单识别数据
        raw_data_path = config.get('raw_data_path')
        use_raw_data_ocr = bool(config.get('use_raw_data_ocr'))
        if raw_data_path and data_paths:
            _update(task_id, '已上传 Excel/CSV 与原始数据记录单：按规则仅使用 Excel/CSV 计算，原始数据单仅用于文末附图', tasks_dict)
        elif raw_data_path and not data_paths:
            _update(task_id, '仅上传原始数据记录单：将尝试 OCR 提取数据用于计算', tasks_dict)
        if use_raw_data_ocr and not data_text and raw_data_path:
            _update(task_id, '正在从原始数据记录单提取数据...', tasks_dict)
            try:
                raw_data_content = extract_raw_data_from_image(raw_data_path, client, config['vision_model'])
                data_text = raw_data_content
                _update(task_id, '✓ 已从原始数据记录单提取数据', tasks_dict)
            except Exception as e:
                _update(task_id, f'⚠ 解析原始数据记录单失败: {str(e)[:50]}', tasks_dict)

        example_sections = {}
        if config.get('example_paths'):
            _update(task_id, '正在解析参考样例...', tasks_dict)
            for ep in config['example_paths']:
                try:
                    ex = extract_example_sections(ep)
                    for k, v in ex.items():
                        if k not in example_sections:
                            example_sections[k] = v
                        else:
                            example_sections[k] += '\n\n---\n\n' + v
                except Exception:
                    pass

        section_contents = {}
        text_model = config['text_model']
        sys_prompt = config['system_prompt']

        # Phase 1: 独立章节并行生成
        phase1_sections = ['实验名称', '实验目的', '实验原理', '实验内容', '实验仪器']
        _update(task_id, f'Phase 1: 并行生成 {len(phase1_sections)} 个独立章节...', tasks_dict)

        # 获取材料路径用于图片插入
        material_paths = config.get('material_paths', [])

        phase1_tasks = []
        for sec_name in phase1_sections:
            extra = ''
            if sec_name == '实验名称':
                extra = '只需输出实验的名称文本，一行即可。'
            example_text = example_sections.get(sec_name, '')
            # 构建只包含分配给该章节的图片的材料文本
            section_materials = build_section_materials(sec_name, materials_text, image_section_mapping, image_contents)
            section_img_indices = image_section_mapping.get(sec_name, [])
            phase1_tasks.append((
                client, text_model, sys_prompt, sec_name,
                section_materials, data_text, example_text, extra, format_type, material_paths, plot_infos,
                section_img_indices, True
            ))

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(_generate_one_section, t): t[3] for t in phase1_tasks}
            for future in as_completed(futures):
                sec_name, content, err = future.result()
                if err:
                    _update(task_id, f'「{sec_name}」生成失败: {err}', tasks_dict)
                    section_contents[sec_name] = f'生成失败: {err}'
                else:
                    _update(task_id, f'「{sec_name}」生成完成', tasks_dict)
                    section_contents[sec_name] = _sanitize_section_content(content)

        # Phase 2: 数据处理
        _update(task_id, 'Phase 2: 生成数据记录处理...', tasks_dict)
        data_sec = '数据记录处理'

        # 检查是否有原始数据记录单
        raw_data_hint = ''
        if raw_data_path:
            raw_data_hint = (
                '\n\n【重要】用户上传了原始数据记录单图片。'
                '不要在正文插入整页原图，也不要输出任何原始数据图片占位符。'
            )
            if not use_raw_data_ocr:
                raw_data_hint += '本次以已上传的 Excel/CSV 数据文件为准，禁止再识别该图片中的手写数据。'

        if format_type == 'latex':
            data_extra = (
                '这是最关键的部分。请根据实验数据进行完整的计算分析。\n'
                '要求：\n'
                '1. 不要展开详细计算过程；仅保留核心公式和最终结果\n'
                '2. 数据表格必须用 LaTeX table/tabular 环境\n'
                '3. 公式使用标准 LaTeX 格式\n'
                '4. 绝对不要出现 Markdown 语法\n'
                '5. 确保 LaTeX 代码可以编译通过\n'
                '6. 禁止输出任何模板占位符（如____/TBD/待补充）'
                + raw_data_hint
            )
        else:
            data_extra = (
                '这是最关键的部分。请根据实验数据进行完整的计算分析。\n'
                '要求：\n'
                '1. 不要展开详细计算过程；仅保留核心公式和最终结果\n'
                '2. 数据表格用简单文本格式（空格或制表符对齐）\n'
                '3. 公式用简单文本表示，如 E = mc^2\n'
                '4. 使用纯文本格式，不要用 Markdown\n'
                '5. 保持清晰可读\n'
                '6. 禁止输出任何模板占位符（如____/TBD/待补充）'
                + raw_data_hint
            )
        # 数据处理章节只使用分配给它或原始数据相关的图片
        data_section_materials = build_section_materials(data_sec, materials_text, image_section_mapping, image_contents)
        data_example = example_sections.get(data_sec, '')
        data_content = _generate_section(
            client, text_model, sys_prompt, data_sec,
            data_section_materials, data_text, data_example, data_extra,
            format_type, material_paths, plot_infos,
            image_section_mapping.get(data_sec, []), False
        )
        section_contents[data_sec] = _sanitize_section_content(data_content)
        _update(task_id, f'「{data_sec}」生成完成（长度: {len(data_content)} 字符）', tasks_dict)

        # Phase 3: 思考/结论类章节（不需要图片）
        phase3_sections = ['思考题', '讨论与分析', '实验结论']
        _update(task_id, f'Phase 3: 并行生成 {len(phase3_sections)} 个思考/结论章节...', tasks_dict)

        data_summary = section_contents.get('数据记录处理', '')[:2000]

        phase3_tasks = []
        for sec_name in phase3_sections:
            if sec_name == '思考题':
                extra = (
                    f'请优先从实验资料中提取并完整回答“思考题/问答题/讨论题”。\n'
                    f'若资料中出现多道题，需逐题作答并给出推理过程。\n'
                    f'可参考以下数据处理摘要补充论据：\n'
                    f'=== 数据处理摘要 ===\n{data_summary}\n'
                    f'=== 摘要结束 ===\n'
                    f'注意：本章节不需要插入任何图片。'
                )
            else:
                extra = (
                    f'请基于以下数据处理结果进行分析和总结：\n'
                    f'=== 数据处理摘要 ===\n{data_summary}\n'
                    f'=== 摘要结束 ===\n'
                    f'在此基础上撰写「{sec_name}」，提炼关键发现和结论。\n'
                    f'注意：本章节不需要插入任何图片。'
                )
            example_text = example_sections.get(sec_name, '')
            # 结论章节不传递任何图片
            phase3_tasks.append((
                client, text_model, sys_prompt, sec_name,
                '', data_text, example_text, extra, format_type, [], plot_infos, [], False
            ))

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(_generate_one_section, t): t[3] for t in phase3_tasks}
            for future in as_completed(futures):
                sec_name, content, err = future.result()
                if err:
                    _update(task_id, f'「{sec_name}」生成失败: {err}', tasks_dict)
                    section_contents[sec_name] = f'生成失败: {err}'
                else:
                    _update(task_id, f'「{sec_name}」生成完成', tasks_dict)
                    section_contents[sec_name] = _sanitize_section_content(content)

        # 后处理：去除重复插入的图片标记
        _update(task_id, '正在处理图片重复问题...', tasks_dict)
        section_contents = _deduplicate_images(section_contents, format_type)

        return section_contents

    except Exception as e:
        _update(task_id, f'生成过程出错: {str(e)}', tasks_dict)
        if task_id in tasks_dict:
            tasks_dict[task_id]['status'] = 'error'
            tasks_dict[task_id]['error'] = f'{type(e).__name__}: {str(e)}'
        traceback.print_exc()
        return None
