#!/usr/bin/env python3
"""
Word 报告生成后端 - 基于模板填充
"""

import shutil
import re
import traceback
from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import parse_xml
from lxml import etree
from latex2mathml.converter import convert as latex_to_mathml

from constants import KNOWN_SECTIONS

from paths import RESOURCE_DIR, DATA_DIR
BASE_DIR = RESOURCE_DIR
TEMPLATE_DIR = RESOURCE_DIR / "templates"
UPLOAD_FOLDER = DATA_DIR / "uploads"
OUTPUT_FOLDER = DATA_DIR / "outputs"
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)

# OMML 命名空间
NSMAP = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'm': 'http://schemas.openxmlformats.org/officeDocument/2006/math',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}


def set_chinese_font(run, font_name='宋体', font_size=10.5, bold=False):
    """设置中文字体"""
    font = run.font
    font.name = font_name
    font.size = Pt(font_size)
    font.bold = bold
    run._element.rPr.rFonts.set(qn('w:eastAsia'), font_name)


def create_omml_formula(formula_text):
    """将 LaTeX 公式转换为 OMML 对象（Word 可编辑公式）。"""
    try:
        ns_m = NSMAP['m']

        def _m(tag):
            return f'{{{ns_m}}}{tag}'

        def _local_name(tag):
            return tag.split('}', 1)[-1] if '}' in tag else tag

        def _append_text_run(parent, text):
            text = (text or '').strip()
            if not text:
                return
            r = etree.SubElement(parent, _m('r'))
            t = etree.SubElement(r, _m('t'))
            t.text = text

        def _children_to_omml(parent, nodes):
            for child in nodes:
                _node_to_omml(parent, child)

        def _wrap_in_container(parent, tag_name, nodes):
            c = etree.SubElement(parent, _m(tag_name))
            _children_to_omml(c, nodes)
            return c

        def _node_to_omml(parent, node):
            lname = _local_name(node.tag)
            if lname in ('math', 'mrow', 'mstyle', 'semantics', 'annotation', 'annotation-xml'):
                _children_to_omml(parent, list(node))
                return

            if lname in ('mi', 'mn', 'mo', 'mtext'):
                _append_text_run(parent, ''.join(node.itertext()))
                return

            children = list(node)
            if lname == 'msup' and len(children) >= 2:
                ssup = etree.SubElement(parent, _m('sSup'))
                _wrap_in_container(ssup, 'e', [children[0]])
                _wrap_in_container(ssup, 'sup', [children[1]])
                return
            if lname == 'msub' and len(children) >= 2:
                ssub = etree.SubElement(parent, _m('sSub'))
                _wrap_in_container(ssub, 'e', [children[0]])
                _wrap_in_container(ssub, 'sub', [children[1]])
                return
            if lname == 'msubsup' and len(children) >= 3:
                ssubsup = etree.SubElement(parent, _m('sSubSup'))
                _wrap_in_container(ssubsup, 'e', [children[0]])
                _wrap_in_container(ssubsup, 'sub', [children[1]])
                _wrap_in_container(ssubsup, 'sup', [children[2]])
                return
            if lname == 'mfrac' and len(children) >= 2:
                frac = etree.SubElement(parent, _m('f'))
                _wrap_in_container(frac, 'num', [children[0]])
                _wrap_in_container(frac, 'den', [children[1]])
                return
            if lname == 'msqrt':
                rad = etree.SubElement(parent, _m('rad'))
                rad_pr = etree.SubElement(rad, _m('radPr'))
                etree.SubElement(rad_pr, _m('degHide'), attrib={_m('val'): '1'})
                etree.SubElement(rad, _m('deg'))
                _wrap_in_container(rad, 'e', children)
                return
            if lname == 'mroot' and len(children) >= 2:
                rad = etree.SubElement(parent, _m('rad'))
                _wrap_in_container(rad, 'deg', [children[1]])
                _wrap_in_container(rad, 'e', [children[0]])
                return
            if lname == 'mfenced':
                open_ch = node.get('open', '(')
                close_ch = node.get('close', ')')
                _append_text_run(parent, open_ch)
                _children_to_omml(parent, children)
                _append_text_run(parent, close_ch)
                return

            # 未覆盖标签降级为拼接文本，避免整段丢失
            text = ''.join(node.itertext()).strip()
            if text:
                _append_text_run(parent, text)
            else:
                _children_to_omml(parent, children)

        formula = (formula_text or '').strip()
        if formula.startswith(r'\(') and formula.endswith(r'\)'):
            formula = formula[2:-2].strip()
        elif formula.startswith(r'\[') and formula.endswith(r'\]'):
            formula = formula[2:-2].strip()
        elif formula.startswith('$$') and formula.endswith('$$'):
            formula = formula[2:-2].strip()
        elif formula.startswith('$') and formula.endswith('$'):
            formula = formula[1:-1].strip()

        mathml = latex_to_mathml(formula)
        math_root = etree.fromstring(mathml.encode('utf-8'))
        omath = etree.Element(_m('oMath'), nsmap={'m': ns_m})
        _node_to_omml(omath, math_root)
        return parse_xml(etree.tostring(omath, encoding='unicode'))
    except Exception as e:
        print(f"[WARNING] 公式 LaTeX->OMML 失败: {e}, 使用文本降级")
        return None


def add_formula_to_paragraph(para, formula_text):
    """向段落添加公式"""
    try:
        # 添加公式作为 Office Math 对象
        math_element = create_omml_formula(formula_text)
        if math_element is not None:
            para._element.append(math_element)
            return True
    except Exception as e:
        print(f"[WARNING] 添加公式失败: {e}")
    
    # 失败时添加纯文本
    run = para.add_run(f" [{formula_text}] ")
    run.font.italic = True
    return False


def add_inline_text_with_formulas(para, text):
    """在现有段落中插入文本与公式（$...$ 等），公式转 OMML。"""
    parts = parse_formula_in_text(text or '')
    for part_type, content in parts:
        if part_type == 'text':
            if content:
                run = para.add_run(content)
                set_chinese_font(run, '宋体', 10.5)
        elif part_type == 'formula':
            add_formula_to_paragraph(para, content)


def parse_formula_in_text(text):
    """
    从文本中解析公式标记
    支持格式: $...$、$$...$$、\(...\)、\[...\]
    """
    pattern = r'(\$\$(.+?)\$\$)|(\$(.+?)\$)|(\\\((.+?)\\\))|(\\\[(.+?)\\\])'
    
    parts = []
    last_end = 0
    
    for match in re.finditer(pattern, text, flags=re.DOTALL):
        # 添加公式前的文本
        if match.start() > last_end:
            parts.append(('text', text[last_end:match.start()]))
        
        # 添加公式
        formula = ''
        for g in match.groups()[1::2]:
            if g:
                formula = g.strip()
                break
        if formula:
            parts.append(('formula', formula))
        
        last_end = match.end()
    
    # 添加剩余文本
    if last_end < len(text):
        parts.append(('text', text[last_end:]))
    
    return parts


def add_formatted_paragraph_with_formula(doc, text):
    """添加包含公式的段落"""
    para = doc.add_paragraph()
    
    parts = parse_formula_in_text(text)
    
    for part_type, content in parts:
        if part_type == 'text':
            # 普通文本
            if content:
                # 处理加粗标记 **text**
                text_parts = content.split('**')
                for idx, text_part in enumerate(text_parts):
                    if text_part:
                        run = para.add_run(text_part)
                        if idx % 2 == 1:  # 奇数索引是加粗部分
                            run.font.bold = True
                            set_chinese_font(run, '黑体', 10.5)
                        else:
                            set_chinese_font(run, '宋体', 10.5)
        elif part_type == 'formula':
            # 公式优先使用 OMML 对象插入；失败时会自动回退为文本
            add_formula_to_paragraph(para, content)
    
    # 设置段落格式
    para.alignment = WD_PARAGRAPH_ALIGNMENT.JUSTIFY
    para.paragraph_format.space_before = Pt(0)
    para.paragraph_format.space_after = Pt(6)
    para.paragraph_format.line_spacing = 1.5
    para.paragraph_format.first_line_indent = Inches(0.5)
    
    return para


def add_section_heading(doc, title_text):
    """添加章节标题（不依赖模板内置 Heading 1 样式）。"""
    heading = doc.add_paragraph()
    heading.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT
    run = heading.add_run(title_text)
    set_chinese_font(run, '黑体', 14, bold=True)
    heading.paragraph_format.keep_with_next = True
    heading.paragraph_format.space_before = Pt(12)
    heading.paragraph_format.space_after = Pt(6)
    heading.paragraph_format.line_spacing = 1.5
    heading.paragraph_format.first_line_indent = Inches(0)
    return heading


def _apply_table_borders_xml(table):
    """Fallback: set thin black borders via raw XML when no style available."""
    try:
        ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        border_xml = (
            '<w:tblBorders xmlns:w="' + ns + '">'
            '<w:top w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '<w:left w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '<w:right w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
            '</w:tblBorders>'
        )
        tbl_pr = table._tbl.tblPr
        if tbl_pr is None:
            tbl_pr = parse_xml('<w:tblPr xmlns:w="' + ns + '"/>')
            table._tbl.insert(0, tbl_pr)
        tbl_pr.append(parse_xml(border_xml))
    except Exception:
        pass


def safe_set_table_style(table, preferred_styles=None):
    """Safely apply a table style and always ensure visible borders."""
    styles_to_try = preferred_styles or ('Table Grid', 'TableGrid')
    applied = False
    for style_name in styles_to_try:
        try:
            table.style = style_name
            applied = True
            break
        except (KeyError, ValueError):
            continue
        except Exception:
            continue
    # 无论样式是否成功，都强制附加边框，避免模板样式导致“看起来像纯文本”
    _apply_table_borders_xml(table)
    return applied


def create_table_from_data(doc, table_data):
    """从数据创建标准 Word 表格"""
    if not table_data or len(table_data) < 1:
        return None
    
    num_rows = len(table_data)
    num_cols = max(len(row) for row in table_data)
    
    table = doc.add_table(rows=num_rows, cols=num_cols)
    safe_set_table_style(table)
    
    for i, row_data in enumerate(table_data):
        row = table.rows[i]
        for j, cell_text in enumerate(row_data):
            if j < len(row.cells):
                cell = row.cells[j]
                cell.text = str(cell_text)
                # 设置单元格字体
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        set_chinese_font(run, '宋体', 10)
    
    return table


def detect_and_create_table(doc, line):
    """检测行是否是表格并创建"""
    # 检查是否是表格格式（包含 | 或制表符）
    if '|' in line:
        parts = [p.strip() for p in line.split('|') if p.strip()]
        if len(parts) >= 2:
            return None  # 单行表格暂不处理
    return None


def _parse_tabular_body_to_rows(body):
    """解析 tabular/tabularx/longtable 的正文为二维数组。"""
    body = (body or '').strip()
    if not body:
        return None

    rows = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 去掉注释
        line = re.sub(r'(?<!\\)%.*$', '', line).strip()
        if not line:
            continue
        # 跳过常见 LaTeX 表格控制命令
        if re.fullmatch(r'\\(toprule|midrule|bottomrule|hline)\s*', line):
            continue
        if re.fullmatch(r'\\(c|)line\{[^}]*\}\s*', line):
            continue
        if line.startswith(r'\caption') or line.startswith(r'\label') or line.startswith(r'\centering'):
            continue
        if line.startswith(r'\begin{') or line.startswith(r'\end{'):
            continue

        # 行尾分隔符处理
        line = line.rstrip()
        if line.endswith(r'\\'):
            line = line[:-2].rstrip()
        elif line.endswith('\\'):
            line = line[:-1].rstrip()
        if not line:
            continue

        # 先展开 \multicolumn / \multirow 的文本内容
        line = re.sub(r'\\multicolumn\{\d+\}\{[^}]*\}\{([^}]*)\}', r'\1', line)
        line = re.sub(r'\\multirow\{\d+\}\{[^}]*\}\{([^}]*)\}', r'\1', line)

        cols = [c.strip() for c in re.split(r'(?<!\\)&', line)]
        cols = [c.replace(r'\&', '&').replace(r'\%', '%') for c in cols]
        if len(cols) >= 2:
            rows.append(cols)

    return rows or None


def extract_latex_table(lines, start_idx):
    """
    从行列表提取 LaTeX 表格（支持 table + tabular/tabularx/longtable）。
    返回 (rows, next_index)。
    """
    env_pat = r'(tabularx?|longtable)'
    line = lines[start_idx].strip()

    def _extract_begin_env_name(text):
        m = re.search(r'\\begin\{(tabularx?|longtable)\}', text)
        return m.group(1) if m else None

    # 情况A：当前行直接是 tabular/tabularx/longtable 开始
    if re.search(rf'\\begin\{{{env_pat}\}}', line):
        env_name = _extract_begin_env_name(line)
        if not env_name:
            return None, start_idx
        j = start_idx
        block = []
        while j < len(lines):
            block.append(lines[j])
            if re.search(rf'\\end\{{{env_name}\}}', lines[j]):
                break
            j += 1
        if j >= len(lines):
            return None, start_idx
        body = '\n'.join(block)
        body = re.sub(rf'\\begin\{{{env_name}\}}(?:\[[^\]]*\])?(?:\{{[^}}]*\}}){{1,2}}', '', body, count=1)
        body = re.sub(rf'\\end\{{{env_name}\}}', '', body, count=1)
        rows = _parse_tabular_body_to_rows(body)
        return (rows, j + 1) if rows else (None, start_idx)

    # 情况B：当前行是 table 外层环境，内部包含 tabular*
    if r'\begin{table' in line:
        j = start_idx
        block = []
        while j < len(lines):
            block.append(lines[j])
            if r'\end{table' in lines[j]:
                break
            j += 1
        if j >= len(lines):
            return None, start_idx
        block_text = '\n'.join(block)
        begin_match = re.search(r'\\begin\{(tabularx?|longtable)\}(?:\[[^\]]*\])?(?:\{[^}]*\}){1,2}', block_text)
        if not begin_match:
            return None, j + 1
        env_name = begin_match.group(1)
        search_from = begin_match.end()
        end_match = re.search(rf'\\end\{{{env_name}\}}', block_text[search_from:])
        if not end_match:
            return None, j + 1
        body = block_text[search_from:search_from + end_match.start()]
        rows = _parse_tabular_body_to_rows(body)
        return (rows, j + 1) if rows else (None, j + 1)

    return None, start_idx


def parse_table_row(line):
    """Parse one table row from pipe or tab separated text."""
    if not line:
        return None

    if '|' in line:
        cells = [p.strip() for p in line.split('|') if p.strip()]
        return cells if len(cells) >= 2 else None

    if '\t' in line:
        cells = [p.strip() for p in line.split('\t')]
        return cells if len(cells) >= 2 else None

    return None


def is_markdown_separator_row(cells):
    """Check markdown table separator row like |---|:---:|."""
    if not cells:
        return False
    for c in cells:
        token = c.replace(':', '').replace('-', '').strip()
        if token:
            return False
    return True


def add_word_table(doc, table_lines):
    """向文档插入原生 Word 表格。"""
    if not table_lines:
        return None
    num_rows = len(table_lines)
    num_cols = max(len(row) for row in table_lines)

    table = doc.add_table(rows=num_rows, cols=num_cols)
    safe_set_table_style(table)
    table.autofit = False

    # 按内容长度分配列宽，减少“逐字换行”问题
    section = doc.sections[-1]
    page_width_inch = (section.page_width - section.left_margin - section.right_margin) / 914400
    min_col_width = 0.55
    weights = []
    for col_idx in range(num_cols):
        max_len = 4
        for row in table_lines:
            if col_idx < len(row):
                txt = str(row[col_idx]).strip()
                plain = re.sub(r'[$\\{}_^]', '', txt)
                max_len = max(max_len, min(len(plain), 28))
        weights.append(max_len)
    total_weight = sum(weights) if weights else 1
    raw_widths = [max(min_col_width, page_width_inch * w / total_weight) for w in weights]
    sum_width = sum(raw_widths)
    if sum_width > page_width_inch and sum_width > 0:
        scale = page_width_inch / sum_width
        col_widths = [w * scale for w in raw_widths]
    else:
        col_widths = raw_widths

    for row_idx, row_data in enumerate(table_lines):
        row = table.rows[row_idx]
        for col_idx, cell_text in enumerate(row_data):
            if col_idx < len(row.cells):
                cell = row.cells[col_idx]
                width = Inches(col_widths[col_idx]) if col_idx < len(col_widths) else Inches(min_col_width)
                row.cells[col_idx].width = width
                cell.text = ''
                p = cell.paragraphs[0] if cell.paragraphs else cell.add_paragraph()
                add_inline_text_with_formulas(p, str(cell_text))
                p.paragraph_format.first_line_indent = Inches(0)
                p.paragraph_format.line_spacing = 1.15
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        set_chinese_font(run, '宋体', 10)
    # 给所有列设置宽度，增强不同 Word 版本的一致性
    for idx, col in enumerate(table.columns):
        if idx < len(col_widths):
            col.width = Inches(col_widths[idx])
    return table


def fill_template_with_content(doc, cover_info, section_contents, raw_data_path=None):
    """
    填充模板文档
    查找占位符如 %%SECTION_实验名称%% 并替换为实际内容
    支持多行内容填充
    """
    # 首先处理封面占位符
    cover_map = {
        '%%COVER_EXPERIMENT_NAME%%': cover_info.get('experiment_name', ''),
        '%%COVER_STUDENT_NAME%%': cover_info.get('student_name', ''),
        '%%COVER_STUDENT_ID%%': cover_info.get('student_id', ''),
        '%%COVER_GROUP_NUMBER%%': cover_info.get('group_number', ''),
        '%%COVER_TEAM_NUMBER%%': cover_info.get('group_number', ''),
        '%%COVER_EXPERIMENT_DATE%%': cover_info.get('experiment_date', ''),
        '%%COVER_EXPERIMENT_DATA%%': cover_info.get('experiment_date', ''),
    }
    
    # 处理章节占位符
    section_map = {}
    for sec_name in KNOWN_SECTIONS:
        placeholder = f'%%SECTION_{sec_name}%%'
        content = section_contents.get(sec_name, '') or ''
        section_map[placeholder] = content
    
    # 合并所有占位符
    all_placeholders = {**cover_map, **section_map}
    
    # 限定封面处理范围：只处理首个正文章节标题之前的段落
    first_section_idx = len(doc.paragraphs)
    for idx, para in enumerate(doc.paragraphs):
        if para.text.strip() in KNOWN_SECTIONS:
            first_section_idx = idx
            break
    
    # 收集需要删除的段落（用于处理多行内容）
    paragraphs_to_remove = []
    
    # 遍历文档中的所有段落，替换占位符
    # 使用 list() 创建副本避免修改时影响迭代
    for i, para in enumerate(list(doc.paragraphs)):
        full_text = ''.join(run.text for run in para.runs)

        for placeholder, content in all_placeholders.items():
            if placeholder in full_text:
                # 清空段落现有内容
                for run in para.runs:
                    run.text = ''
                
                # 填充新内容
                if '\n' in content:
                    # 多行内容：在当前段落后插入新段落
                    lines = content.split('\n')
                    if lines:
                        # 填充第一行到当前段落
                        para.add_run(lines[0])
                        # 获取当前段落的父元素和索引
                        p_element = para._element
                        parent = p_element.getparent()
                        idx = parent.index(p_element)
                        # 剩余行在当前段落后面插入
                        for line in lines[1:]:
                            new_para = doc.add_paragraph(line)
                            new_para.style = para.style
                            # 移动新段落到正确位置
                            parent.insert(idx + 1, new_para._element)
                            idx += 1
                else:
                    # 单行内容直接填充
                    para.add_run(content)
                
                break
    
    # 处理表格中的占位符
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                cell_text = ''.join(run.text for para in cell.paragraphs for run in para.runs)
                
                for placeholder, content in all_placeholders.items():
                    if placeholder in cell_text:
                        # 清空单元格
                        for para in cell.paragraphs:
                            for run in para.runs:
                                run.text = ''
                        # 填充内容（表格中只取第一行）
                        first_line = content.split('\n')[0] if content else ''
                        if cell.paragraphs:
                            cell.paragraphs[0].add_run(first_line)
                        break

    # 兜底：模板可能没有封面占位符，仅有“学生姓名/学号/组号/实验日期”等标签行
    label_rules = {
        '学生姓名': ('student_name', '学生姓名'),
        '学号': ('student_id', '学　　号'),
        '组号': ('group_number', '组    号'),
        '实验日期': ('experiment_date', '实验日期'),
    }

    def _normalize_label(text):
        return re.sub(r'[\s　]+', '', (text or '').strip())

    # 使用 list() 创建副本避免修改时影响迭代
    for idx, para in enumerate(list(doc.paragraphs)):
        if idx >= first_section_idx:
            break
        normalized = _normalize_label(para.text)
        if normalized not in label_rules:
            continue

        field_key, display_label = label_rules[normalized]
        value = (cover_info.get(field_key, '') or '').strip()

        for run in para.runs:
            run.text = ''
        label_run = para.add_run(f'{display_label}    ')
        set_chinese_font(label_run, '黑体', 12, bold=True)
        if value:
            value_run = para.add_run(value)
            set_chinese_font(value_run, '宋体', 12)


def fill_cover_info_in_template(doc, cover_info):
    """
    在模板中填充封面信息
    查找并替换封面相关的占位符和标签
    """
    if not cover_info:
        return

    # 定义封面占位符映射（移除空值检查，允许空值覆盖占位符）
    cover_placeholders = {
        '%%COVER_EXPERIMENT_NAME%%': cover_info.get('experiment_name', ''),
        '%%COVER_STUDENT_NAME%%': cover_info.get('student_name', ''),
        '%%COVER_STUDENT_ID%%': cover_info.get('student_id', ''),
        '%%COVER_GROUP_NUMBER%%': cover_info.get('group_number', ''),
        '%%COVER_TEAM_NUMBER%%': cover_info.get('group_number', ''),
        '%%COVER_EXPERIMENT_DATE%%': cover_info.get('experiment_date', ''),
        '%%COVER_EXPERIMENT_DATA%%': cover_info.get('experiment_date', ''),
    }

    # 标签规则（用于没有占位符的情况）
    label_rules = {
        '实验名称': ('experiment_name', '实验名称'),
        '学生姓名': ('student_name', '学生姓名'),
        '学号': ('student_id', '学　　号'),
        '组号': ('group_number', '组    号'),
        '实验日期': ('experiment_date', '实验日期'),
    }

    def _normalize_label(text):
        return re.sub(r'[\s　]+', '', (text or '').strip())

    # 查找第一个章节标题的位置（封面信息在此之前）
    first_section_idx = len(doc.paragraphs)
    for idx, para in enumerate(doc.paragraphs):
        if para.text.strip() in KNOWN_SECTIONS:
            first_section_idx = idx
            break

    def _replace_placeholder_in_runs(runs, placeholder, value):
        """仅替换占位符文本，尽量保留原有 run 样式（如下划线横线、字体）。"""
        replaced = False
        for run in runs:
            if placeholder in (run.text or ''):
                run.text = (run.text or '').replace(placeholder, value or '')
                replaced = True
        return replaced

    def _replace_placeholder_in_paragraph(para, placeholder, value):
        """优先按 run 替换；占位符跨 run 时仅替换命中区间，保留其余 run 样式。"""
        full_text = ''.join(run.text for run in para.runs)
        if placeholder not in full_text:
            return False
        if _replace_placeholder_in_runs(para.runs, placeholder, value):
            return True
        # 占位符被拆分到多个 run：只改占位符覆盖的 run，避免吞掉后续下划线 run
        runs = list(para.runs)
        if not runs:
            para.add_run(full_text.replace(placeholder, value or ''))
            return True

        start = full_text.find(placeholder)
        end = start + len(placeholder)

        spans = []
        cursor = 0
        for idx, run in enumerate(runs):
            text = run.text or ''
            spans.append((idx, cursor, cursor + len(text)))
            cursor += len(text)

        start_run_idx = None
        end_run_idx = None
        for idx, s, e in spans:
            if start_run_idx is None and s <= start < e:
                start_run_idx = idx
            if s < end <= e:
                end_run_idx = idx
                break

        if start_run_idx is None or end_run_idx is None:
            # 理论不应发生；保底不清空其他 run
            runs[0].text = full_text.replace(placeholder, value or '')
            return True

        start_run = runs[start_run_idx]
        end_run = runs[end_run_idx]
        start_run_abs = spans[start_run_idx][1]
        end_run_abs = spans[end_run_idx][1]

        prefix = (start_run.text or '')[: start - start_run_abs]
        suffix = (end_run.text or '')[end - end_run_abs :]

        if start_run_idx == end_run_idx:
            start_run.text = prefix + (value or '') + suffix
            return True

        start_run.text = prefix + (value or '')
        for i in range(start_run_idx + 1, end_run_idx):
            runs[i].text = ''
        end_run.text = suffix
        return True

    # 遍历封面区域的段落，替换占位符或标签（不清空原 run，避免破坏横线/字体）
    for idx, para in enumerate(list(doc.paragraphs)):
        if idx >= first_section_idx:
            break

        full_text = ''.join(run.text for run in para.runs)

        # 1) 优先替换占位符
        placeholder_found = False
        for placeholder, value in cover_placeholders.items():
            if placeholder in full_text:
                placeholder_found = _replace_placeholder_in_paragraph(para, placeholder, value)
                break

        # 2) 无占位符时，识别标签并仅追加值，不改动现有装饰
        if not placeholder_found:
            normalized = _normalize_label(full_text)
            if normalized in label_rules:
                field_key, _display_label = label_rules[normalized]
                value = (cover_info.get(field_key) or '').strip()
                if value and value not in full_text:
                    value_run = para.add_run(f' {value}')
                    set_chinese_font(value_run, '宋体', 12)

    # 处理表格中的封面信息
    for table in doc.tables:
        for row_idx, row in enumerate(table.rows):
            if row_idx > 3:  # 只检查前几行
                break
            for cell in row.cells:
                cell_text = ''.join(run.text for para in cell.paragraphs for run in para.runs)

                # 1) 替换占位符（保持单元格原格式）
                placeholder_found = False
                for placeholder, value in cover_placeholders.items():
                    if placeholder in cell_text:
                        for para in cell.paragraphs:
                            if _replace_placeholder_in_paragraph(para, placeholder, value):
                                placeholder_found = True
                        break

                # 2) 标签模式：仅追加值，避免清空单元格后格式丢失
                if not placeholder_found:
                    normalized = _normalize_label(cell_text)
                    if normalized in label_rules and cell.paragraphs:
                        field_key, _display_label = label_rules[normalized]
                        value = (cover_info.get(field_key) or '').strip()
                        if value and value not in cell_text:
                            value_run = cell.paragraphs[0].add_run(f' {value}')
                            set_chinese_font(value_run, '宋体', 12)


def strip_template_section_skeleton(doc):
    """
    删除模板中从首个章节标题开始的“空章节骨架”，
    保留封面/前置页，避免生成结果看起来仍像原模板。
    """
    first_section_idx = None
    for idx, para in enumerate(doc.paragraphs):
        if para.text.strip() in KNOWN_SECTIONS:
            first_section_idx = idx
            break

    if first_section_idx is None:
        return False

    to_remove = list(doc.paragraphs[first_section_idx:])
    for para in to_remove:
        p = para._element
        parent = p.getparent()
        if parent is not None:
            parent.remove(p)
    return True


def build_word_document_from_template(
    cover_info,
    section_contents,
    task_dir,
    raw_data_paths=None,
    material_paths=None,
    plot_paths=None,
    append_raw_data_image=False,
    progress_cb=None,
):
    """
    基于模板构建 Word 文档
    如果模板存在，在模板基础上追加内容；否则创建新文档
    """
    template_path = TEMPLATE_DIR / 'main.docx'
    
    if template_path.exists():
        # 使用模板作为基础
        doc = Document(str(template_path))
        
        # 回填封面信息（即使使用模板也要填充用户填写的封面信息）
        fill_cover_info_in_template(doc, cover_info)
        
        had_template_sections = strip_template_section_skeleton(doc)
        # 模板不含章节骨架时，回退为追加新页写入
        if not had_template_sections:
            doc.add_page_break()
        
        # 添加 AI 生成的各章节内容
        for sec_name in KNOWN_SECTIONS:
            content = section_contents.get(sec_name, '').strip()
            if not content:
                continue
            if progress_cb:
                progress_cb(f'正在写入章节：{sec_name}...')
            
            # 章节标题
            add_section_heading(doc, sec_name)
            
            # 内容段落
            lines = content.split('\n')
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue
                
                # 检查是否是图片标记 [IMAGE:X]
                img_match = re.match(r'\[IMAGE:(\d+)\]', line)
                if img_match:
                    img_idx = int(img_match.group(1))
                    if material_paths and img_idx < len(material_paths):
                        img_path = material_paths[img_idx]
                        if Path(img_path).exists():
                            try:
                                doc.add_picture(str(img_path), width=Inches(5))
                                last_para = doc.paragraphs[-1]
                                last_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                            except Exception as e:
                                print(f"[WARNING] 插入图片失败 {img_path}: {e}")
                    i += 1
                    continue

                # 检查是否是数据图标记 [PLOT:X]
                plot_match = re.match(r'\[PLOT:(\d+)\]', line)
                if plot_match:
                    plot_idx = int(plot_match.group(1))
                    if plot_paths and plot_idx < len(plot_paths):
                        plot_path = plot_paths[plot_idx]
                        if Path(plot_path).exists():
                            try:
                                doc.add_picture(str(plot_path), width=Inches(5.5))
                                last_para = doc.paragraphs[-1]
                                last_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                            except Exception as e:
                                print(f"[WARNING] 插入数据图失败 {plot_path}: {e}")
                    i += 1
                    continue
                
                # 检查 LaTeX 表格（table + tabular/tabularx/longtable）
                latex_table, next_i = extract_latex_table(lines, i)
                if latex_table:
                    add_word_table(doc, latex_table)
                    i = next_i
                    continue

                # 检查是否是表格（支持 | 或制表符）
                first_row = parse_table_row(line)
                if first_row:
                    table_lines = []
                    j = i
                    while j < len(lines):
                        parts = parse_table_row(lines[j].strip())
                        if not parts:
                            break
                        if not is_markdown_separator_row(parts):
                            table_lines.append(parts)
                        j += 1
                    
                    if len(table_lines) > 0:
                        add_word_table(doc, table_lines)
                        i = j
                        continue
                
                # 添加带公式的段落
                add_formatted_paragraph_with_formula(doc, line)
                i += 1
        
        # 处理原始数据记录单（按开关决定是否在文档末尾添加）
        if append_raw_data_image and raw_data_paths:
            # 兼容单张图片路径字符串或列表
            if isinstance(raw_data_paths, str):
                raw_data_paths = [raw_data_paths]

            valid_paths = [p for p in raw_data_paths if p and Path(p).exists()]
            if valid_paths:
                try:
                    doc.add_page_break()
                    add_section_heading(doc, '原始数据记录单')
                    for idx, raw_data_path in enumerate(valid_paths):
                        try:
                            doc.add_picture(str(raw_data_path), width=Inches(5.5))
                            last_para = doc.paragraphs[-1]
                            last_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                            # 多张图片时添加间距
                            if idx < len(valid_paths) - 1:
                                doc.add_paragraph()
                        except Exception as e:
                            print(f"[WARNING] 插入原始数据记录单图片失败 {raw_data_path}: {e}")
                except Exception as e:
                    print(f"[WARNING] 处理原始数据记录单失败: {e}")
    else:
        # 创建新文档（原有逻辑）
        doc = Document()
        
        # 设置默认中文字体
        style = doc.styles['Normal']
        style.font.name = '宋体'
        style._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        
        # ========== 封面 ==========
        logo_path = TEMPLATE_DIR / 'logo.jpg'
        if logo_path.exists():
            doc.add_picture(str(logo_path), width=Inches(2))
            last_paragraph = doc.paragraphs[-1]
            last_paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        
        # 标题
        title = doc.add_paragraph()
        title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        run = title.add_run('物理实验报告')
        set_chinese_font(run, '黑体', 22, bold=True)
        
        doc.add_paragraph()
        
        # 封面信息
        cover_items = [
            ('实验名称', cover_info.get('experiment_name', '')),
            ('学生姓名', cover_info.get('student_name', '')),
            ('学　　号', cover_info.get('student_id', '')),
            ('组　　号', cover_info.get('group_number', '')),
            ('实验日期', cover_info.get('experiment_date', '')),
        ]
        
        for label, value in cover_items:
            para = doc.add_paragraph()
            para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
            
            label_run = para.add_run(f'{label}：')
            set_chinese_font(label_run, '黑体', 12, bold=True)
            
            if value:
                value_run = para.add_run(value)
                set_chinese_font(value_run, '宋体', 12)
            else:
                blank_run = para.add_run('　　　　　　　　　　　　')
                set_chinese_font(blank_run, '宋体', 12)
        
        doc.add_page_break()
        
        # ========== 正文 ==========
        for sec_name in KNOWN_SECTIONS:
            content = section_contents.get(sec_name, '').strip()
            if not content:
                continue
            if progress_cb:
                progress_cb(f'正在写入章节：{sec_name}...')
            
            # 章节标题
            add_section_heading(doc, sec_name)
            
            # 内容段落
            lines = content.split('\n')
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1
                    continue
                
                # 检查是否是图片标记 [IMAGE:X]
                img_match = re.match(r'\[IMAGE:(\d+)\]', line)
                if img_match:
                    img_idx = int(img_match.group(1))
                    if material_paths and img_idx < len(material_paths):
                        img_path = material_paths[img_idx]
                        if Path(img_path).exists():
                            try:
                                doc.add_picture(str(img_path), width=Inches(5))
                                last_para = doc.paragraphs[-1]
                                last_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                            except Exception as e:
                                print(f"[WARNING] 插入图片失败 {img_path}: {e}")
                    i += 1
                    continue

                # 检查是否是数据图标记 [PLOT:X]
                plot_match = re.match(r'\[PLOT:(\d+)\]', line)
                if plot_match:
                    plot_idx = int(plot_match.group(1))
                    if plot_paths and plot_idx < len(plot_paths):
                        plot_path = plot_paths[plot_idx]
                        if Path(plot_path).exists():
                            try:
                                doc.add_picture(str(plot_path), width=Inches(5.5))
                                last_para = doc.paragraphs[-1]
                                last_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                            except Exception as e:
                                print(f"[WARNING] 插入数据图失败 {plot_path}: {e}")
                    i += 1
                    continue
                
                # 检查 LaTeX 表格（table + tabular/tabularx/longtable）
                latex_table, next_i = extract_latex_table(lines, i)
                if latex_table:
                    add_word_table(doc, latex_table)
                    i = next_i
                    continue

                # 检查是否是表格（支持 | 或制表符）
                first_row = parse_table_row(line)
                if first_row:
                    table_lines = []
                    # 收集当前及后续的表格行
                    j = i
                    while j < len(lines):
                        parts = parse_table_row(lines[j].strip())
                        if not parts:
                            break
                        if not is_markdown_separator_row(parts):
                            table_lines.append(parts)
                        j += 1
                    
                    if len(table_lines) > 0:
                        add_word_table(doc, table_lines)
                        i = j
                        continue
                
                # 添加带公式的段落
                add_formatted_paragraph_with_formula(doc, line)
                i += 1
        
        # 在文档末尾添加原始数据记录单（按开关决定）
        if append_raw_data_image and raw_data_paths:
            # 兼容单张图片路径字符串或列表
            if isinstance(raw_data_paths, str):
                raw_data_paths = [raw_data_paths]

            valid_paths = [p for p in raw_data_paths if p and Path(p).exists()]
            if valid_paths:
                try:
                    doc.add_page_break()
                    add_section_heading(doc, '原始数据记录单')
                    for idx, raw_data_path in enumerate(valid_paths):
                        try:
                            doc.add_picture(str(raw_data_path), width=Inches(5.5))
                            last_para = doc.paragraphs[-1]
                            last_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
                            # 多张图片时添加间距
                            if idx < len(valid_paths) - 1:
                                doc.add_paragraph()
                        except Exception as e:
                            print(f"[WARNING] 插入原始数据记录单图片失败 {raw_data_path}: {e}")
                except Exception as e:
                    print(f"[WARNING] 处理原始数据记录单失败: {e}")
    
    # 保存文档
    if progress_cb:
        progress_cb('正在写入 Word 文件到磁盘...')
    word_path = Path(task_dir) / 'report.docx'
    # Remove stale template sample headers and fixed page counts, retaining drawings.
    for section in doc.sections:
        for header in (section.header, section.first_page_header, section.even_page_header):
            for text in header._element.iter(qn('w:t')):
                if (text.text or '').strip() not in ('装', '订', '线'):
                    text.text = ''
        for footer in (section.footer, section.first_page_footer, section.even_page_footer):
            for paragraph in footer.paragraphs:
                paragraph.clear()
    doc.save(str(word_path))
    return str(word_path)
