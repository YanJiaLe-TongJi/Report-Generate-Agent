#!/usr/bin/env python3
"""数据绘图工具：自动生成更智能的候选数据图（PNG）。"""

import csv
import math
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams
from openpyxl import load_workbook

# 中文字体与负号显示兼容（按环境已安装字体回退）
_FONT_CANDIDATES = [
    'Noto Sans CJK SC', 'Noto Sans CJK JP', 'Noto Sans CJK TC',
    'Microsoft YaHei', 'SimHei', 'WenQuanYi Zen Hei', 'Arial Unicode MS'
]
_available_fonts = {f.name for f in font_manager.fontManager.ttflist}
_selected_fonts = [f for f in _FONT_CANDIDATES if f in _available_fonts]
if not _selected_fonts:
    _selected_fonts = ['DejaVu Sans']
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = _selected_fonts + ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def _apply_chinese_font_runtime():
    """
    在绘图前强制绑定可用中文字体，避免其他模块覆盖 rcParams 后中文再次乱码。
    """
    available = {f.name for f in font_manager.fontManager.ttflist}
    selected = [f for f in _FONT_CANDIDATES if f in available]
    if not selected:
        selected = ['DejaVu Sans']
    rcParams['font.family'] = ['sans-serif']
    rcParams['font.sans-serif'] = selected + ['DejaVu Sans']
    rcParams['axes.unicode_minus'] = False


def _to_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    txt = str(value).strip()
    if not txt:
        return None
    txt = txt.replace(',', '')
    try:
        val = float(txt)
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    except ValueError:
        return None


def _parse_label_unit(name):
    text = (name or '').strip()
    if not text:
        return '未命名列', ''
    m = re.search(r'^(.*?)\s*\(([^()]+)\)\s*$', text)
    if m:
        return m.group(1).strip() or text, m.group(2).strip()
    m = re.search(r'^(.*?)\s*（([^（）]+)）\s*$', text)
    if m:
        return m.group(1).strip() or text, m.group(2).strip()
    if '/' in text and len(text.split('/')) == 2:
        lhs, rhs = text.split('/')
        return lhs.strip() or text, rhs.strip()
    return text, ''


def _pretty_axis(name):
    label, unit = _parse_label_unit(name)
    return f'{label} ({unit})' if unit else label


def _header_key(name):
    label, _ = _parse_label_unit(name)
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', label.lower())


def _is_error_col(name):
    low = (name or '').lower()
    keys = ['err', 'error', 'sigma', 'std', 'unc', '±', '误差', '不确定度', '标准差']
    return any(k in low for k in keys)


def _extract_table_from_sheet(sheet_rows):
    if not sheet_rows:
        return [], []

    def _non_empty_cells(row):
        return sum(1 for c in row if (c is not None and str(c).strip()))

    # 自动定位表头行：在前 30 行里找“非空单元格较多”的行，避免把“表题/说明”误判为表头
    search_rows = sheet_rows[:30]
    best_idx = -1
    best_score = -1
    for idx, row in enumerate(search_rows):
        score = _non_empty_cells(row)
        if score >= 2 and score > best_score:
            best_idx = idx
            best_score = score

    if best_idx < 0:
        # 回退：第一行作为表头（旧行为）
        best_idx = 0

    raw_headers = sheet_rows[best_idx]
    headers = [str(c).strip() if c is not None else '' for c in raw_headers]
    headers = [h if h else f'列{i + 1}' for i, h in enumerate(headers)]

    data_rows = []
    for row in sheet_rows[best_idx + 1:]:
        vals = [None] * len(headers)
        for idx in range(min(len(row), len(headers))):
            vals[idx] = row[idx]
        # 遇到连续大量空白尾行前保留有效行；这里仅跳过全空行
        if any(str(v).strip() if v is not None else False for v in vals):
            data_rows.append(vals)
    return headers, data_rows


def _collect_numeric_series(headers, data_rows):
    numeric_cols = []
    for col_idx, header in enumerate(headers):
        series = []
        for row in data_rows:
            raw = row[col_idx] if col_idx < len(row) else None
            series.append(_to_float(raw))
        valid_count = sum(1 for v in series if v is not None)
        if valid_count >= 2:
            numeric_cols.append((col_idx, header or f'列{col_idx + 1}', series))
    return numeric_cols


def _is_integer_like(v):
    return abs(v - round(v)) < 1e-10


def _linear_fit(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx <= 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    y_hat = [slope * x + intercept for x in xs]
    sst = sum((y - mean_y) ** 2 for y in ys)
    if sst <= 0:
        r2 = None
    else:
        sse = sum((y - yh) ** 2 for y, yh in zip(ys, y_hat))
        r2 = max(0.0, 1.0 - sse / sst)
    return slope, intercept, r2


def _choose_chart_type(xs, ys):
    n = len(xs)
    if n < 2:
        return None
    unique_x = len(set(xs))
    monotonic = all(xs[i] <= xs[i + 1] for i in range(n - 1)) or all(xs[i] >= xs[i + 1] for i in range(n - 1))
    integer_ratio = sum(1 for x in xs if _is_integer_like(x)) / n
    # 类别数量较少且接近整数时更适合柱状图
    if unique_x <= 12 and integer_ratio > 0.9 and unique_x <= n * 0.7:
        return 'bar'
    if monotonic and unique_x >= max(3, int(n * 0.6)):
        return 'line'
    return 'scatter'


def _align_points(x_series, y_series, x_err_series=None, y_err_series=None):
    pts = []
    for idx, (xv, yv) in enumerate(zip(x_series, y_series)):
        if xv is None or yv is None:
            continue
        xe = x_err_series[idx] if x_err_series and idx < len(x_err_series) else None
        ye = y_err_series[idx] if y_err_series and idx < len(y_err_series) else None
        pts.append((xv, yv, xe, ye))
    return pts


def _detect_error_mapping(headers, numeric_cols, x_col_idx, y_col_idx):
    key_to_col = {idx: name for idx, name, _ in numeric_cols}
    x_name = key_to_col.get(x_col_idx, '')
    y_name = key_to_col.get(y_col_idx, '')
    x_key = _header_key(x_name)
    y_key = _header_key(y_name)
    x_err_idx = None
    y_err_idx = None
    for idx, name, _ in numeric_cols:
        if idx in (x_col_idx, y_col_idx):
            continue
        if not _is_error_col(name):
            continue
        name_key = _header_key(name)
        if x_key and x_key in name_key and x_err_idx is None:
            x_err_idx = idx
        if y_key and y_key in name_key and y_err_idx is None:
            y_err_idx = idx
    return x_err_idx, y_err_idx


def _plot_xy(config):
    _apply_chinese_font_runtime()
    xs = config['xs']
    ys = config['ys']
    if len(xs) < 2:
        return None

    chart_type = config['chart_type']
    x_err = config.get('x_err')
    y_err = config.get('y_err')
    has_error = any(v is not None for v in (x_err or [])) or any(v is not None for v in (y_err or []))

    fig = plt.figure(figsize=(7.2, 4.8), dpi=150)
    ax = fig.add_subplot(111)
    ax.grid(True, linestyle='--', alpha=0.35)

    if chart_type == 'bar':
        grouped = defaultdict(list)
        for x, y in zip(xs, ys):
            grouped[x].append(y)
        x_vals = sorted(grouped.keys())
        y_vals = [sum(grouped[x]) / len(grouped[x]) for x in x_vals]
        ax.bar(x_vals, y_vals, width=0.65, alpha=0.85, color='#3b82f6')
    elif has_error:
        ax.errorbar(
            xs, ys,
            xerr=x_err if x_err and any(v is not None for v in x_err) else None,
            yerr=y_err if y_err and any(v is not None for v in y_err) else None,
            fmt='o-', markersize=3.5, linewidth=1.2, capsize=3, color='#2563eb'
        )
    elif chart_type == 'line':
        ax.plot(xs, ys, marker='o', linewidth=1.6, markersize=3.8, color='#2563eb')
    else:
        ax.scatter(xs, ys, s=18, alpha=0.9, color='#2563eb')

    fit_meta = None
    if chart_type != 'bar':
        fit = _linear_fit(xs, ys)
        if fit:
            slope, intercept, r2 = fit
            x_min, x_max = min(xs), max(xs)
            fit_x = [x_min, x_max]
            fit_y = [slope * x + intercept for x in fit_x]
            ax.plot(fit_x, fit_y, '--', linewidth=1.2, color='#dc2626')
            eq = f'y = {slope:.4g}x + {intercept:.4g}'
            if r2 is not None:
                eq += f', R^2={r2:.4f}'
            fit_meta = {'equation': eq, 'slope': slope, 'intercept': intercept, 'r2': r2}

    ax.set_title(config['title'], fontsize=10)
    ax.set_xlabel(config['x_label'], fontsize=9)
    ax.set_ylabel(config['y_label'], fontsize=9)
    fig.tight_layout()
    fig.savefig(config['out_path'], format='png')
    plt.close(fig)

    return {
        'chart_type': chart_type,
        'has_error_bar': has_error,
        'fit': fit_meta,
    }


def _read_workbook_tables(file_path):
    def _load_workbook_robust(path, data_only):
        try:
            return load_workbook(path, data_only=data_only)
        except Exception as e:
            msg = str(e).lower()
            p = Path(path)
            if 'does not support file format' in msg and p.suffix.lower() == '':
                tmp_path = None
                try:
                    with open(path, 'rb') as rf:
                        sig = rf.read(4)
                    if sig.startswith(b'PK'):
                        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tf:
                            tmp_path = tf.name
                        shutil.copy2(path, tmp_path)
                        return load_workbook(tmp_path, data_only=data_only)
                finally:
                    if tmp_path:
                        Path(tmp_path).unlink(missing_ok=True)
            raise

    wb = _load_workbook_robust(file_path, data_only=True)
    tables = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if not rows:
            continue
        headers, data_rows = _extract_table_from_sheet(rows)
        if headers:
            tables.append((sheet_name, headers, data_rows))
    # 某些 xlsx 仅有公式且无缓存值，data_only=True 会读空，回退读取公式文本
    if not tables:
        wb_formula = _load_workbook_robust(file_path, data_only=False)
        for sheet_name in wb_formula.sheetnames:
            ws = wb_formula[sheet_name]
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            if not rows:
                continue
            headers, data_rows = _extract_table_from_sheet(rows)
            if headers:
                tables.append((sheet_name, headers, data_rows))
    return tables


def _read_xls_tables(file_path):
    import xlrd  # type: ignore
    book = xlrd.open_workbook(file_path)
    tables = []
    for sheet_name in book.sheet_names():
        sh = book.sheet_by_name(sheet_name)
        rows = [sh.row_values(r) for r in range(sh.nrows)]
        if not rows:
            continue
        headers, data_rows = _extract_table_from_sheet(rows)
        if headers:
            tables.append((sheet_name, headers, data_rows))
    return tables


def _read_csv_table(file_path):
    rows = None
    for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            with open(file_path, 'r', encoding=enc, newline='') as f:
                rows = list(csv.reader(f))
            break
        except Exception:
            rows = None
    if rows is None:
        return []
    if not rows:
        return []
    headers, data_rows = _extract_table_from_sheet(rows)
    if not headers:
        return []
    return [('CSV', headers, data_rows)]


def _build_caption(meta):
    style_name = {'line': '折线图', 'scatter': '散点图', 'bar': '柱状图'}.get(meta['chart_type'], '数据图')
    cap = f"{style_name}：{meta['y_label']} 随 {meta['x_label']} 变化"
    if meta.get('has_error_bar'):
        cap += '（含误差棒）'
    if meta.get('fit_equation'):
        cap += f"；线性拟合 {meta['fit_equation']}"
    return cap


def generate_data_plots(data_paths, task_dir, max_plots=8, with_diagnostics=False):
    """从数据文件自动生成候选图，支持拟合、误差棒、图类型自适应。"""
    if not data_paths:
        return ([], {'file_stats': [], 'summary': '未上传数据文件'}) if with_diagnostics else []

    plot_dir = Path(task_dir) / 'plots'
    plot_dir.mkdir(parents=True, exist_ok=True)
    plots = []
    diagnostics = {'file_stats': []}

    for data_path in data_paths:
        fp = Path(data_path)
        if not fp.exists():
            diagnostics['file_stats'].append({'file': fp.name, 'status': 'missing', 'reason': '文件不存在', 'tables': 0, 'numeric_tables': 0, 'plots': 0})
            continue
        suffix = fp.suffix.lower()
        file_stat = {'file': fp.name, 'status': 'ok', 'reason': '', 'tables': 0, 'numeric_tables': 0, 'plots': 0}
        try:
            if suffix == '.csv':
                tables = _read_csv_table(str(fp))
            elif suffix == '.xls':
                tables = _read_xls_tables(str(fp))
            else:
                tables = _read_workbook_tables(str(fp))
        except Exception as e:
            file_stat['status'] = 'error'
            file_stat['reason'] = str(e)
            diagnostics['file_stats'].append(file_stat)
            continue
        file_stat['tables'] = len(tables)
        if not tables:
            file_stat['status'] = 'empty'
            file_stat['reason'] = '未找到可用工作表或表头'
            diagnostics['file_stats'].append(file_stat)
            continue

        for sheet_name, headers, data_rows in tables:
            numeric_cols = _collect_numeric_series(headers, data_rows)
            if not numeric_cols:
                continue
            file_stat['numeric_tables'] += 1

            per_table = 0
            per_table_limit = 3

            # 单列：样本序号 vs y
            if len(numeric_cols) == 1:
                _, y_name_raw, y_series = numeric_cols[0]
                x_series = list(range(1, len(y_series) + 1))
                pts = _align_points(x_series, y_series)
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                if len(xs) < 2:
                    continue
                x_label = '样本序号'
                y_label = _pretty_axis(y_name_raw)
                title = f'{fp.name} - {sheet_name}: {y_label}'
                out_path = plot_dir / f'plot_{len(plots)}.png'
                meta = _plot_xy({
                    'xs': xs, 'ys': ys,
                    'x_err': None, 'y_err': None,
                    'chart_type': 'line',
                    'x_label': x_label, 'y_label': y_label,
                    'title': title, 'out_path': out_path,
                })
                if meta:
                    fit_equation = meta['fit']['equation'] if meta.get('fit') else None
                    item = {
                        'index': len(plots),
                        'path': str(out_path),
                        'title': title,
                        'x_label': x_label,
                        'y_label': y_label,
                        'chart_type': meta['chart_type'],
                        'has_error_bar': meta['has_error_bar'],
                        'fit_equation': fit_equation,
                        'r2': meta['fit'].get('r2') if meta.get('fit') else None,
                    }
                    item['caption'] = _build_caption(item)
                    plots.append(item)
                    file_stat['plots'] += 1
                if len(plots) >= max_plots:
                    diagnostics['file_stats'].append(file_stat)
                    if with_diagnostics:
                        diagnostics['summary'] = f'已生成 {len(plots)} 张图（达到上限）'
                        return plots, diagnostics
                    return plots
                continue

            # 多列：默认第一列 x，后续列作为 y，自动选择图类型，支持误差棒与拟合
            x_col_idx, x_name_raw, x_series = numeric_cols[0]
            for y_col_idx, y_name_raw, y_series in numeric_cols[1:]:
                if per_table >= per_table_limit:
                    break
                x_err_idx, y_err_idx = _detect_error_mapping(headers, numeric_cols, x_col_idx, y_col_idx)
                x_err_series = None
                y_err_series = None
                if x_err_idx is not None:
                    x_err_series = next((s for idx, _, s in numeric_cols if idx == x_err_idx), None)
                if y_err_idx is not None:
                    y_err_series = next((s for idx, _, s in numeric_cols if idx == y_err_idx), None)

                pts = _align_points(x_series, y_series, x_err_series, y_err_series)
                if len(pts) < 2:
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                xe = [p[2] for p in pts] if x_err_series else None
                ye = [p[3] for p in pts] if y_err_series else None

                chart_type = _choose_chart_type(xs, ys)
                if not chart_type:
                    continue

                x_label = _pretty_axis(x_name_raw)
                y_label = _pretty_axis(y_name_raw)
                title = f'{fp.name} - {sheet_name}: {y_label} vs {x_label}'
                out_path = plot_dir / f'plot_{len(plots)}.png'

                meta = _plot_xy({
                    'xs': xs, 'ys': ys,
                    'x_err': xe, 'y_err': ye,
                    'chart_type': chart_type,
                    'x_label': x_label, 'y_label': y_label,
                    'title': title, 'out_path': out_path,
                })
                if not meta:
                    continue

                fit_equation = meta['fit']['equation'] if meta.get('fit') else None
                item = {
                    'index': len(plots),
                    'path': str(out_path),
                    'title': title,
                    'x_label': x_label,
                    'y_label': y_label,
                    'chart_type': meta['chart_type'],
                    'has_error_bar': meta['has_error_bar'],
                    'fit_equation': fit_equation,
                    'r2': meta['fit'].get('r2') if meta.get('fit') else None,
                }
                item['caption'] = _build_caption(item)
                plots.append(item)
                per_table += 1
                file_stat['plots'] += 1

                if len(plots) >= max_plots:
                    diagnostics['file_stats'].append(file_stat)
                    if with_diagnostics:
                        diagnostics['summary'] = f'已生成 {len(plots)} 张图（达到上限）'
                        return plots, diagnostics
                    return plots

        if file_stat['plots'] == 0:
            if file_stat['numeric_tables'] == 0:
                file_stat['status'] = 'no_numeric'
                file_stat['reason'] = '有表格但缺少至少两列可解析数值列'
            else:
                file_stat['status'] = 'no_plot'
                file_stat['reason'] = '已识别数值列，但未形成可绘制 x-y 组合'
        diagnostics['file_stats'].append(file_stat)

    if with_diagnostics:
        diagnostics['summary'] = f'已生成 {len(plots)} 张图'
        return plots, diagnostics
    return plots
