import os
import json
import time
import threading
import uuid
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, request, jsonify, send_file, render_template, Response, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user, UserMixin
from sqlalchemy import text, inspect, or_
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from openai import OpenAI

from crypto_utils import encrypt_api_key, decrypt_api_key, decrypt_api_key_if_needed
from word_backend import run_word_generation
from latex_backend import run_latex_generation
from constants import COVER_FIELDS
from constants import KNOWN_SECTIONS
from latex_backend import build_latex_document, compile_latex
from word_backend import build_word_document_from_template
from email_utils import (
    generate_verification_code, send_verification_email, send_password_reset_success_email,
    validate_email
)

# ===================== Flask App Config =====================

PROJECT_DIR = Path(__file__).parent


def _load_local_env_file():
    """加载项目根目录 .env（仅填充未设置的环境变量）。"""
    env_path = PROJECT_DIR / '.env'
    if not env_path.exists() or not env_path.is_file():
        return
    try:
        for raw_line in env_path.read_text(encoding='utf-8').splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and ((value[0] == value[-1]) and value[0] in ("'", '"')):
                value = value[1:-1]
            os.environ.setdefault(key, value)
    except Exception:
        pass


_load_local_env_file()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024
secret_key = os.environ.get('SECRET_KEY')
if not secret_key:
    raise RuntimeError('SECRET_KEY environment variable is required')
app.config['SECRET_KEY'] = secret_key
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///app.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = '请先登录'

BASE_DIR = PROJECT_DIR
UPLOAD_FOLDER = BASE_DIR / 'uploads'
OUTPUT_FOLDER = BASE_DIR / 'outputs'
TEMPLATE_DIR = BASE_DIR / 'templates'
FEEDBACK_FOLDER = UPLOAD_FOLDER / 'feedback'
UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)
FEEDBACK_FOLDER.mkdir(parents=True, exist_ok=True)

tasks = {}
FILE_CLEANUP_TTL_SECONDS = int(os.environ.get('FILE_CLEANUP_TTL_SECONDS', str(10 * 60)))
FILE_CLEANUP_INTERVAL_SECONDS = int(os.environ.get('FILE_CLEANUP_INTERVAL_SECONDS', '60'))
TASK_DIR_REGEX = re.compile(r'^[0-9a-fA-F-]{36}$')
DOWNLOAD_URL_PREFIX = '/api/download/'


def _normalize_storage_path(raw_path):
    """将历史绝对路径统一规范为相对存储路径（uploads/... 或 outputs/...）。"""
    raw = (str(raw_path or '')).strip()
    if not raw:
        return ''

    normalized = raw.replace('\\', '/')
    lowered = normalized.lower()

    for marker in ('/uploads/', '/outputs/'):
        idx = lowered.find(marker)
        if idx >= 0:
            suffix = normalized[idx + len(marker):].lstrip('/')
            prefix = marker.strip('/')
            return f'{prefix}/{suffix}' if suffix else prefix

    for marker in ('uploads/', 'outputs/'):
        if lowered.startswith(marker):
            return normalized.lstrip('./')

    p = Path(normalized)
    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(BASE_DIR.resolve())
            return str(rel).replace('\\', '/')
        except Exception:
            return normalized

    return normalized.lstrip('./')


def _resolve_storage_path(raw_path):
    """把数据库中的相对路径还原到当前运行目录下的真实路径。"""
    normalized = _normalize_storage_path(raw_path)
    if not normalized:
        return None
    p = Path(normalized)
    if p.is_absolute():
        return p
    return (BASE_DIR / p).resolve()


def _to_storage_path(path_obj):
    """把本地绝对路径转换为相对存储路径。"""
    try:
        rel = Path(path_obj).resolve().relative_to(BASE_DIR.resolve())
        return str(rel).replace('\\', '/')
    except Exception:
        return _normalize_storage_path(str(path_obj))


def _safe_upload_name(raw_name, prefix='file'):
    """清理用户上传文件名，避免路径穿越和奇异字符。"""
    base_name = Path(raw_name or '').name
    safe_name = secure_filename(base_name)
    if safe_name:
        return safe_name
    return f"{prefix}_{uuid.uuid4().hex}"


def _can_access_task(task, user):
    """判断用户是否可访问任务。"""
    if not task or not user.is_authenticated:
        return False
    return bool(user.is_admin or task.get('user_id') == user.id)


def _public_task_payload(task):
    """返回可对外暴露的任务状态，避免泄露敏感配置。"""
    if not task:
        return {}
    return {
        'status': task.get('status'),
        'steps': task.get('steps') or [],
        'current_step': task.get('current_step'),
        'error': task.get('error'),
        'download_url': task.get('download_url'),
        'raw_url': task.get('raw_url'),
        'tex_url': task.get('tex_url'),
    }


def _task_contains_download(task, filename):
    """检查文件名是否属于任务输出。"""
    for key in ('download_url', 'raw_url', 'tex_url'):
        url = task.get(key)
        if not isinstance(url, str) or not url.startswith(DOWNLOAD_URL_PREFIX):
            continue
        if Path(url[len(DOWNLOAD_URL_PREFIX):]).name == filename:
            return True
    return False


def _ensure_data_file_extension(filename, original_name):
    """确保数据文件名包含可识别后缀，避免 openpyxl 因无后缀拒绝解析。"""
    name = str(filename or '')
    suffix = Path(name).suffix.lower()
    if suffix in ('.xlsx', '.xls', '.csv'):
        return name

    original = Path(str(original_name or '')).name.lower()
    if original.endswith('.xlsx'):
        return name + '.xlsx'
    if original.endswith('.xls'):
        return name + '.xls'
    if original.endswith('.csv'):
        return name + '.csv'

    # 兼容类似 ".xlsx" 被 secure_filename 处理成 "xlsx" 的场景
    low = name.lower()
    if low.endswith('_xlsx') or low == 'xlsx':
        return name + '.xlsx'
    if low.endswith('_xls') or low == 'xls':
        return name + '.xls'
    if low.endswith('_csv') or low == 'csv':
        return name + '.csv'
    return name


def _is_safe_next_url(next_url):
    """仅允许站内相对路径跳转，避免开放重定向。"""
    if not next_url:
        return False
    parsed = urlsplit(next_url)
    return (
        not parsed.scheme
        and not parsed.netloc
        and next_url.startswith('/')
        and not next_url.startswith('//')
    )

DEFAULT_SYSTEM_PROMPT = """你是一个物理实验报告撰写助手。请根据提供的实验资料、数据和参考样例，撰写完整的物理实验报告，保持学术性风格。

核心要求：
1. 严格按照章节结构编写
2. 实验目的、实验原理、实验内容、实验仪器部分请严格摘抄资料原文，不要省略或改写
3. 如果资料中包含“思考题/问答题/讨论题”，需要单独完整作答并写入“思考题”章节；除思考题外，是否保留拓展或选做内容可按资料要求处理
4. 实验资料中的图片描述（如装置图、电路图）已经以文字形式提供，请直接引用这些描述，不要输出图片占位符
5. 数学公式使用标准LaTeX：行内用 \\(...\\)，独立公式用 \\[...\\] 或 equation 环境
6. 表格使用 LaTeX tabular/table 环境，用 booktabs 的 \\toprule \\midrule \\bottomrule
7. 数据处理部分必须包含完整计算链路：原始数据表 -> 公式与符号说明 -> 代入计算 -> 中间结果 -> 最终结果（含单位和有效数字）
8. 不要输出任何 Markdown 语法（不要用 **加粗**、不要用 |表格|、不要用 # 标题）
9. 输出内容将直接插入 LaTeX 文档，请确保语法正确可编译
10. 如果提供了参考样例，请严格参考其写作风格、详细程度和格式
11. 当资料要求或用户明确要求展示数据趋势时，可插入数据图并在正文中解释图像结论"""


# ===================== Database Models =====================

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    
    # API Key 加密存储
    api_key_encrypted = db.Column(db.Text)
    api_key_nonce = db.Column(db.String(255))
    
    # 关系
    templates = db.relationship('Template', backref=db.backref('user', lazy=True), lazy=True, cascade='all, delete-orphan')
    examples = db.relationship('UserExample', backref=db.backref('user', lazy=True), lazy=True, cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def set_api_key(self, api_key):
        """加密并存储 API Key"""
        if api_key:
            encrypted, nonce = encrypt_api_key(api_key)
            self.api_key_encrypted = encrypted
            self.api_key_nonce = nonce
        else:
            self.api_key_encrypted = None
            self.api_key_nonce = None
    
    def get_api_key(self):
        """解密并返回 API Key"""
        if self.api_key_encrypted and self.api_key_nonce:
            return decrypt_api_key(self.api_key_encrypted, self.api_key_nonce)
        return None
    
    def has_api_key(self):
        return bool(self.api_key_encrypted)


class Template(db.Model):
    __tablename__ = 'templates'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    cover_info = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SystemMaterial(db.Model):
    __tablename__ = 'system_materials'
    
    id = db.Column(db.Integer, primary_key=True)
    experiment_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    default_cover = db.Column(db.JSON)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    
    # 文件路径存储
    material_paths = db.Column(db.JSON, default=list)  # 资料图片路径列表
    example_path = db.Column(db.String(500))  # 参考样例路径
    # 预解析缓存（避免每次生成重复调用 Vision）
    parsed_contents = db.Column(db.JSON, default=list)  # [{page, path, content}, ...]
    parsed_at = db.Column(db.DateTime)
    parse_error = db.Column(db.Text)
    parse_status = db.Column(db.String(20), default='idle')  # idle / parsing / done / error
    parse_total = db.Column(db.Integer, default=0)
    parse_done = db.Column(db.Integer, default=0)
    parse_started_at = db.Column(db.DateTime)
    parse_finished_at = db.Column(db.DateTime)

    def get_category(self):
        """从 default_cover 中读取分类，避免数据库迁移"""
        if isinstance(self.default_cover, dict):
            return (self.default_cover.get('category') or '未分类').strip()
        return '未分类'
    
    def to_dict(self):
        parsed_count = len(self.parsed_contents) if isinstance(self.parsed_contents, list) else 0
        material_count = len(self.material_paths) if self.material_paths else 0
        return {
            'id': self.id,
            'experiment_name': self.experiment_name,
            'category': self.get_category(),
            'description': self.description,
            'default_cover': self.default_cover,
            'uploaded_at': _to_utc_iso(self.uploaded_at),
            'is_active': self.is_active,
            'material_count': material_count,
            'has_example': bool(self.example_path),
            'is_parsed': bool(material_count > 0 and parsed_count >= material_count and not self.parse_error),
            'parsed_at': _to_utc_iso(self.parsed_at),
            'parse_error': self.parse_error,
            'parse_status': self.parse_status or 'idle',
            'parse_total': int(self.parse_total or material_count),
            'parse_done': int(self.parse_done or 0),
            'parse_started_at': _to_utc_iso(self.parse_started_at),
            'parse_finished_at': _to_utc_iso(self.parse_finished_at),
        }


class UserExample(db.Model):
    """用户上传的参考样例"""
    __tablename__ = 'user_examples'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    file_path = db.Column(db.String(500), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'uploaded_at': _to_utc_iso(self.uploaded_at)
        }


class VerificationCode(db.Model):
    """邮箱验证码"""
    __tablename__ = 'verification_codes'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    code = db.Column(db.String(6), nullable=False)
    purpose = db.Column(db.String(20), nullable=False)  # 'register' 或 'reset_password'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_used = db.Column(db.Boolean, default=False)

    def is_expired(self):
        """检查验证码是否过期"""
        return datetime.utcnow() > self.expires_at

    def is_valid(self):
        """检查验证码是否有效（未过期且未使用）"""
        return not self.is_expired() and not self.is_used


class Feedback(db.Model):
    """用户问题反馈"""
    __tablename__ = 'feedbacks'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    image_paths = db.Column(db.JSON, default=list)
    status = db.Column(db.String(20), default='pending')  # pending / replied / closed
    admin_reply = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    replied_at = db.Column(db.DateTime)

    user = db.relationship('User', backref=db.backref('feedbacks', lazy=True, cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_email': self.user.email if self.user else None,
            'content': self.content,
            'image_paths': self.image_paths or [],
            'status': self.status,
            'admin_reply': self.admin_reply,
            'created_at': _to_utc_iso(self.created_at),
            'replied_at': _to_utc_iso(self.replied_at)
        }


class Announcement(db.Model):
    """系统公告（管理员发布）"""
    __tablename__ = 'announcements'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content_md = db.Column(db.Text, nullable=False)
    is_pinned = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    start_at = db.Column(db.DateTime)
    end_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    author = db.relationship('User', foreign_keys=[created_by], lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'content_md': self.content_md,
            'is_pinned': bool(self.is_pinned),
            'is_active': True if self.is_active is None else bool(self.is_active),
            'start_at': self.start_at.isoformat() if self.start_at else None,
            'end_at': self.end_at.isoformat() if self.end_at else None,
            'created_by': self.created_by,
            'created_by_email': self.author.email if self.author else None,
            'created_at': _to_utc_iso(self.created_at)
        }


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def _safe_unlink(path):
    """安全删除文件"""
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _safe_rmtree(path):
    """安全删除目录"""
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _is_expired(path, now_ts, ttl_seconds):
    """按最后修改时间判断是否过期"""
    try:
        return (now_ts - path.stat().st_mtime) > ttl_seconds
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _calc_path_size(path):
    """计算文件或目录大小（字节）"""
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            total = 0
            for p in path.rglob('*'):
                if p.is_file():
                    total += p.stat().st_size
            return total
    except Exception:
        return 0
    return 0


def _cleanup_system_material_files(material):
    """删除系统资料关联文件与目录（仅限 uploads 下的 material_* 目录）。"""
    upload_root = UPLOAD_FOLDER.resolve()
    raw_paths = []
    if isinstance(material.material_paths, list):
        raw_paths.extend(material.material_paths)
    if material.example_path:
        raw_paths.append(material.example_path)

    candidate_dirs = set()
    deleted_files = 0
    for raw in raw_paths:
        if not raw:
            continue
        try:
            p = _resolve_storage_path(raw)
        except Exception:
            continue
        if not p:
            continue
        if upload_root not in p.parents:
            continue
        if p.exists() and p.is_file():
            _safe_unlink(p)
            deleted_files += 1
        parent = p.parent
        if parent != upload_root and upload_root in parent.parents and parent.name.startswith('material_'):
            candidate_dirs.add(parent)

    deleted_dirs = 0
    for d in sorted(candidate_dirs, key=lambda x: len(x.parts), reverse=True):
        if not d.exists() or not d.is_dir():
            continue
        # 仅删除空目录；若目录里仍有残留文件，再用安全 rmtree 清理
        try:
            if any(d.iterdir()):
                _safe_rmtree(d)
            else:
                d.rmdir()
            deleted_dirs += 1
        except Exception:
            _safe_rmtree(d)
            deleted_dirs += 1

    return {'deleted_files': deleted_files, 'deleted_dirs': deleted_dirs}


def _parse_datetime_text(value):
    """解析前端传入的时间字符串（如 datetime-local）。"""
    txt = (value or '').strip()
    if not txt:
        return None
    try:
        return datetime.fromisoformat(txt)
    except ValueError:
        return None


def _to_utc_iso(dt):
    """Serialize datetime as UTC ISO8601 with Z suffix."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace('+00:00', 'Z')


def _collect_active_task_dirs():
    """收集仍在处理中的任务目录，避免误删"""
    active_dirs = set()
    for task in tasks.values():
        if task.get('status') == 'processing':
            cfg = task.get('config') or {}
            task_dir = cfg.get('task_dir')
            if task_dir:
                active_dirs.add(str(Path(task_dir).resolve()))
    return active_dirs


def _ensure_task_work_dir(config_task_dir, task_id):
    """
    修订/重编译前确保任务工作目录可用。
    - 兼容历史绝对路径；
    - 目录被清理后可自动重建；
    - 路径异常时回退到 uploads/<task_id>。
    """
    raw = (str(config_task_dir or '')).strip()
    task_dir = None

    if raw:
        p = Path(raw)
        if p.is_absolute():
            task_dir = p
        else:
            task_dir = (BASE_DIR / p).resolve()

    if task_dir is None:
        task_dir = (UPLOAD_FOLDER / task_id).resolve()

    try:
        task_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        task_dir = (UPLOAD_FOLDER / task_id).resolve()
        task_dir.mkdir(parents=True, exist_ok=True)

    return str(task_dir)


def cleanup_expired_files():
    """
    自动清理过期文件：
    1) uploads 下的任务临时目录（UUID目录）；
    2) outputs 下的模型输出文件。
    注意：不会删除 user_examples、feedback、system materials 等长期文件。
    """
    now_ts = time.time()
    active_dirs = _collect_active_task_dirs()
    deleted_upload_dirs = 0
    deleted_output_files = 0
    reclaimed_bytes = 0

    # 清理任务上传临时目录（仅 UUID 命名目录）
    for child in UPLOAD_FOLDER.iterdir():
        if not child.is_dir():
            continue
        if not TASK_DIR_REGEX.match(child.name):
            continue
        resolved = str(child.resolve())
        if resolved in active_dirs:
            continue
        if _is_expired(child, now_ts, FILE_CLEANUP_TTL_SECONDS):
            reclaimed_bytes += _calc_path_size(child)
            _safe_rmtree(child)
            deleted_upload_dirs += 1

    # 清理模型输出目录
    for f in OUTPUT_FOLDER.iterdir():
        if not f.is_file():
            continue
        if _is_expired(f, now_ts, FILE_CLEANUP_TTL_SECONDS):
            reclaimed_bytes += _calc_path_size(f)
            _safe_unlink(f)
            deleted_output_files += 1

    stats = {
        'deleted_upload_dirs': deleted_upload_dirs,
        'deleted_output_files': deleted_output_files,
        'reclaimed_bytes': reclaimed_bytes,
    }
    return stats


def start_cleanup_worker():
    """启动后台清理线程"""
    def _worker():
        while True:
            try:
                stats = cleanup_expired_files()
                if stats['deleted_upload_dirs'] or stats['deleted_output_files']:
                    app.logger.info(
                        "[cleanup] deleted_upload_dirs=%s deleted_output_files=%s reclaimed_bytes=%s ttl=%ss",
                        stats['deleted_upload_dirs'],
                        stats['deleted_output_files'],
                        stats['reclaimed_bytes'],
                        FILE_CLEANUP_TTL_SECONDS,
                    )
            except Exception:
                pass
            time.sleep(FILE_CLEANUP_INTERVAL_SECONDS)

    t = threading.Thread(target=_worker, daemon=True, name='file-cleanup-worker')
    t.start()


# ===================== Auth Routes =====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        user = User.query.filter_by(email=email).first()
        
        if user and user.check_password(password):
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            next_page = request.args.get('next')
            if _is_safe_next_url(next_page):
                return redirect(next_page)
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='邮箱或密码错误')
    
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')
        verification_code = request.form.get('verification_code', '').strip()

        # 验证邮箱格式
        if not email.endswith('@tongji.edu.cn'):
            return render_template('register.html', error='仅接受同济大学邮箱(@tongji.edu.cn)注册')

        # 验证密码
        if len(password) < 6:
            return render_template('register.html', error='密码长度至少6位')

        if password != password_confirm:
            return render_template('register.html', error='两次输入的密码不一致')

        # 检查验证码
        if not verification_code:
            return render_template('register.html', error='请输入验证码')

        # 查找有效的验证码
        code_record = VerificationCode.query.filter_by(
            email=email,
            code=verification_code,
            purpose='register',
            is_used=False
        ).order_by(VerificationCode.created_at.desc()).first()

        if not code_record:
            return render_template('register.html', error='验证码错误')

        if code_record.is_expired():
            return render_template('register.html', error='验证码已过期，请重新获取')

        # 检查邮箱是否已注册
        if User.query.filter_by(email=email).first():
            return render_template('register.html', error='该邮箱已注册')

        # 标记验证码为已使用
        code_record.is_used = True

        # 创建新用户
        user = User(email=email)
        user.set_password(password)

        # 第一个用户设为管理员
        if User.query.count() == 0:
            user.is_admin = True

        db.session.add(user)
        db.session.commit()

        flash('注册成功，请登录', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/welcome.md')
def welcome_md():
    """提供 welcome.md 文档"""
    welcome_path = TEMPLATE_DIR / 'welcome.md'
    if welcome_path.exists():
        return send_file(welcome_path, mimetype='text/markdown')
    return jsonify({'error': '文档不存在'}), 404


@app.route('/healthz')
def healthz():
    """容器健康检查"""
    return jsonify({'status': 'ok'})


@app.route('/favicon.ico')
def favicon():
    """浏览器标签页图标。"""
    raw_logo_path = os.environ.get('SITE_LOGO_PATH', '').strip()
    if not raw_logo_path:
        return '', 204

    try:
        logo_path = Path(raw_logo_path).expanduser().resolve()
    except Exception:
        return '', 204

    if not logo_path.exists() or not logo_path.is_file():
        return '', 204

    resp = send_file(str(logo_path))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


@app.route('/api/sponsor/qr/<provider>')
@login_required
def sponsor_qr(provider):
    """读取赞助二维码图片（由环境变量指定文件路径）。"""
    key_map = {
        'wechat': 'SPONSOR_WECHAT_QR_PATH',
        'alipay': 'SPONSOR_ALIPAY_QR_PATH',
    }
    env_key = key_map.get(provider)
    if not env_key:
        return jsonify({'error': '无效的赞助类型'}), 400

    raw_path = os.environ.get(env_key, '').strip()
    if not raw_path:
        return jsonify({'error': '二维码未配置'}), 404

    try:
        qr_path = Path(raw_path).expanduser().resolve()
    except Exception:
        return jsonify({'error': '二维码路径无效'}), 400

    if not qr_path.exists() or not qr_path.is_file():
        return jsonify({'error': '二维码文件不存在'}), 404

    return send_file(str(qr_path))


# ===================== Main Routes =====================

@app.route('/')
def index():
    if not current_user.is_authenticated:
        return redirect(url_for('login'))
    
    # 获取系统实验资料列表
    system_experiments = SystemMaterial.query.filter_by(is_active=True).all()
    system_experiments_data = [m.to_dict() for m in system_experiments]
    
    # 获取当前用户的API Key（解密后）
    user_api_key = None
    if current_user.api_key_encrypted:
        user_api_key = decrypt_api_key_if_needed(current_user.api_key_encrypted, current_user.api_key_nonce)
    
    # 获取用户保存的参考样例
    user_examples = UserExample.query.filter_by(user_id=current_user.id).all()
    sponsor_wechat = os.environ.get('SPONSOR_WECHAT_ACCOUNT', '').strip()
    sponsor_alipay = os.environ.get('SPONSOR_ALIPAY_ACCOUNT', '').strip()
    sponsor_wechat_qr_path = os.environ.get('SPONSOR_WECHAT_QR_PATH', '').strip()
    sponsor_alipay_qr_path = os.environ.get('SPONSOR_ALIPAY_QR_PATH', '').strip()

    return render_template('index.html',
                          system_experiments=system_experiments,
                          system_experiments_data=system_experiments_data,
                          user_api_key=user_api_key,
                          user_examples=user_examples,
                          sponsor_wechat=sponsor_wechat,
                          sponsor_alipay=sponsor_alipay,
                          sponsor_wechat_qr_enabled=bool(sponsor_wechat_qr_path),
                          sponsor_alipay_qr_enabled=bool(sponsor_alipay_qr_path))


@app.route('/admin')
@login_required
def admin():
    if not current_user.is_admin:
        flash('需要管理员权限', 'error')
        return redirect(url_for('index'))
    return render_template('admin.html')


# ===================== API Routes =====================

@app.route('/api/user/profile')
@login_required
def get_user_profile():
    """获取当前用户信息"""
    return jsonify({
        'id': current_user.id,
        'email': current_user.email,
        'is_admin': current_user.is_admin,
        'has_api_key': current_user.has_api_key(),
        'created_at': _to_utc_iso(current_user.created_at)
    })


@app.route('/api/user/apikey', methods=['POST'])
@login_required
def update_user_api_key():
    """更新用户 API Key"""
    data = request.get_json(silent=True) or {}
    api_key = data.get('api_key', '').strip()
    
    if not api_key:
        return jsonify({'error': 'API Key 不能为空'}), 400
    
    if not api_key.startswith('sk-'):
        return jsonify({'error': '无效的 API Key 格式'}), 400
    
    current_user.set_api_key(api_key)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'API Key 已保存'})


@app.route('/api/user/templates')
@login_required
def get_user_templates():
    """获取用户的模板列表"""
    templates = Template.query.filter_by(user_id=current_user.id).order_by(Template.updated_at.desc()).all()
    return jsonify([{
        'id': t.id,
        'name': t.name,
        'created_at': _to_utc_iso(t.created_at),
        'updated_at': _to_utc_iso(t.updated_at)
    } for t in templates])


@app.route('/api/user/templates', methods=['POST'])
@login_required
def create_template():
    """创建新模板"""
    data = request.get_json()
    name = data.get('name', '').strip()
    content = data.get('content', '').strip()
    cover_info = data.get('cover_info', {})
    
    if not name:
        return jsonify({'error': '模板名称不能为空'}), 400
    
    template = Template(
        user_id=current_user.id,
        name=name,
        content=content,
        cover_info=cover_info
    )
    db.session.add(template)
    db.session.commit()
    
    return jsonify({'success': True, 'id': template.id})


@app.route('/api/user/templates/<int:template_id>', methods=['DELETE'])
@login_required
def delete_template(template_id):
    """删除模板"""
    template = Template.query.filter_by(id=template_id, user_id=current_user.id).first()
    if not template:
        return jsonify({'error': '模板不存在'}), 404
    
    db.session.delete(template)
    db.session.commit()
    return jsonify({'success': True})


# ===================== User Examples API =====================

@app.route('/api/user/examples')
@login_required
def get_user_examples():
    """获取用户上传的参考样例列表"""
    examples = UserExample.query.filter_by(user_id=current_user.id).order_by(UserExample.uploaded_at.desc()).all()
    return jsonify([ex.to_dict() for ex in examples])


@app.route('/api/user/examples', methods=['POST'])
@login_required
def upload_user_example():
    """上传用户参考样例"""
    if 'file' not in request.files:
        return jsonify({'error': '请选择文件'}), 400
    
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': '请选择文件'}), 400
    original_name = Path(file.filename).name
    
    # 检查文件类型
    if not original_name.lower().endswith('.docx'):
        return jsonify({'error': '仅支持 .docx 格式'}), 400
    
    # 保存文件
    user_examples_dir = UPLOAD_FOLDER / 'user_examples' / str(current_user.id)
    user_examples_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{int(time.time())}_{_safe_upload_name(original_name, 'example')}"
    file_path = user_examples_dir / filename
    file.save(file_path)
    
    # 创建数据库记录
    example = UserExample(
        user_id=current_user.id,
        name=original_name,
        file_path=_to_storage_path(file_path)
    )
    db.session.add(example)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'id': example.id,
        'name': example.name
    })


@app.route('/api/user/examples/<int:example_id>', methods=['DELETE'])
@login_required
def delete_user_example(example_id):
    """删除用户参考样例"""
    example = UserExample.query.filter_by(id=example_id, user_id=current_user.id).first()
    if not example:
        return jsonify({'error': '样例不存在'}), 404
    
    # 删除文件
    try:
        resolved = _resolve_storage_path(example.file_path)
        if resolved and resolved.exists():
            resolved.unlink()
    except OSError as e:
        pass  # 文件可能已被删除
    
    db.session.delete(example)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/user/examples/<int:example_id>/download')
@login_required
def download_user_example(example_id):
    """下载用户参考样例文件"""
    example = UserExample.query.filter_by(id=example_id, user_id=current_user.id).first()
    if not example:
        return jsonify({'error': '样例不存在'}), 404
    
    resolved = _resolve_storage_path(example.file_path)
    if not resolved or not resolved.exists():
        return jsonify({'error': '文件不存在'}), 404

    return send_file(str(resolved), as_attachment=True, download_name=example.name)


# ===================== Feedback API =====================

@app.route('/api/feedback', methods=['POST'])
@login_required
def create_feedback():
    """用户提交问题反馈（文本 + 图片）"""
    content = request.form.get('content', '').strip()
    if not content:
        return jsonify({'error': '反馈内容不能为空'}), 400
    if len(content) > 5000:
        return jsonify({'error': '反馈内容过长（最多5000字）'}), 400

    image_paths = []
    images = request.files.getlist('images')
    if len(images) > 5:
        return jsonify({'error': '最多上传5张图片'}), 400

    user_feedback_dir = FEEDBACK_FOLDER / str(current_user.id)
    user_feedback_dir.mkdir(parents=True, exist_ok=True)

    for idx, image in enumerate(images):
        if not image or not image.filename:
            continue
        lower_name = image.filename.lower()
        if not (lower_name.endswith('.png') or lower_name.endswith('.jpg') or lower_name.endswith('.jpeg') or lower_name.endswith('.webp')):
            return jsonify({'error': '仅支持 PNG/JPG/JPEG/WEBP 图片'}), 400

        safe_name = f"{int(time.time())}_{idx}_{_safe_upload_name(image.filename, f'feedback_{idx}')}"
        save_path = user_feedback_dir / safe_name
        image.save(save_path)
        image_paths.append(_to_storage_path(save_path))

    feedback = Feedback(
        user_id=current_user.id,
        content=content,
        image_paths=image_paths,
        status='pending'
    )
    db.session.add(feedback)
    db.session.commit()
    return jsonify({'success': True, 'message': '反馈提交成功', 'feedback_id': feedback.id})


@app.route('/api/feedback/mine')
@login_required
def get_my_feedbacks():
    """获取当前用户反馈记录"""
    rows = Feedback.query.filter_by(user_id=current_user.id).order_by(Feedback.created_at.desc()).all()
    return jsonify([row.to_dict() for row in rows])


@app.route('/api/feedback/image')
@login_required
def get_feedback_image():
    """访问反馈图片（仅本人或管理员）"""
    path = request.args.get('path', '').strip()
    if not path:
        return jsonify({'error': '缺少图片路径'}), 400
    target = _resolve_storage_path(path)
    if not target:
        return jsonify({'error': '缺少图片路径'}), 400
    feedback_root = FEEDBACK_FOLDER.resolve()
    if feedback_root not in target.parents:
        return jsonify({'error': '无权限访问该图片'}), 403
    if not target.exists():
        return jsonify({'error': '图片不存在'}), 404
    if not current_user.is_admin:
        try:
            owner_id = int(target.parent.name)
        except ValueError:
            return jsonify({'error': '无权限访问该图片'}), 403
        if owner_id != current_user.id:
            return jsonify({'error': '无权限访问该图片'}), 403
    return send_file(str(target))


@app.route('/api/system/materials')
def get_system_materials():
    """获取系统实验资料列表（公开接口）"""
    materials = SystemMaterial.query.filter_by(is_active=True).all()
    return jsonify([m.to_dict() for m in materials])


@app.route('/api/system/materials/<int:material_id>')
def get_system_material_detail(material_id):
    """获取单个实验资料详情"""
    material = SystemMaterial.query.get_or_404(material_id)
    if not material.is_active:
        return jsonify({'error': '该资料已禁用'}), 403
    
    return jsonify({
        'id': material.id,
        'experiment_name': material.experiment_name,
        'category': material.get_category(),
        'description': material.description,
        'default_cover': material.default_cover,
        'material_count': len(material.material_paths) if material.material_paths else 0,
        'has_example': bool(material.example_path)
    })


@app.route('/api/announcements/latest')
@login_required
def get_latest_announcement():
    """获取最新公告（登录用户）"""
    now_dt = datetime.now()
    latest = Announcement.query.filter(
        or_(Announcement.is_active.is_(True), Announcement.is_active.is_(None)),
        or_(Announcement.start_at.is_(None), Announcement.start_at <= now_dt),
        or_(Announcement.end_at.is_(None), Announcement.end_at >= now_dt)
    ).order_by(
        Announcement.is_pinned.desc(),
        Announcement.created_at.desc(),
        Announcement.id.desc()
    ).first()
    if not latest:
        return jsonify({'announcement': None})
    return jsonify({'announcement': latest.to_dict()})


@app.route('/api/announcements/history')
@login_required
def get_announcement_history():
    """获取公告历史（登录用户）"""
    now_dt = datetime.now()
    rows = Announcement.query.filter(
        or_(Announcement.is_active.is_(True), Announcement.is_active.is_(None)),
        or_(Announcement.start_at.is_(None), Announcement.start_at <= now_dt)
    ).order_by(
        Announcement.is_pinned.desc(),
        Announcement.created_at.desc(),
        Announcement.id.desc()
    ).all()
    return jsonify([row.to_dict() for row in rows])


@app.route('/api/generate', methods=['POST'])
@login_required
def generate():
    """生成实验报告"""
    task_id = str(uuid.uuid4())
    task_dir = UPLOAD_FOLDER / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # 优先使用用户保存的 API Key，其次使用表单提交的
    api_key_encrypted = request.form.get('api_key', '').strip()
    api_key_nonce = request.form.get('api_key_nonce', '').strip()
    
    # 解密 API Key（如果提供了 nonce）
    api_key = None
    if api_key_encrypted:
        api_key = decrypt_api_key_if_needed(api_key_encrypted, api_key_nonce)
    
    # 如果表单没有提供，尝试使用用户保存的 API Key
    if not api_key:
        api_key = current_user.get_api_key()
    
    if not api_key:
        return jsonify({'error': '请提供 API Key'}), 400

    base_url = request.form.get('base_url', 'https://api.moonshot.cn/v1').strip()
    system_prompt = request.form.get('system_prompt', DEFAULT_SYSTEM_PROMPT)
    vision_model = request.form.get('vision_model', 'moonshot-v1-32k-vision-preview')
    text_model = request.form.get('text_model', 'kimi-k2.5').strip()
    # 兼容旧版：kimi2.5 -> kimi-k2.5（官方正确模型名）
    if text_model == 'kimi2.5':
        text_model = 'kimi-k2.5'

    cover_info = {}
    for field in COVER_FIELDS:
        cover_info[field] = request.form.get(field, '').strip()

    def save_files(key, multi=True):
        paths = []
        for f in request.files.getlist(key):
            if f.filename:
                safe = f"{key}_{len(paths)}_{_safe_upload_name(f.filename, key)}"
                if key == 'data':
                    safe = _ensure_data_file_extension(safe, f.filename)
                p = task_dir / safe
                f.save(p)
                paths.append(str(p))
                if not multi:
                    break
        return paths

    # 检查是否使用系统资料
    system_material_id = request.form.get('system_material_id')
    material_paths = []
    system_example_path = None
    pre_parsed_contents = None
    
    if system_material_id:
        material = SystemMaterial.query.get(system_material_id)
        if material and material.is_active:
            # 使用系统资料
            material_paths = []
            for raw in (material.material_paths or []):
                resolved = _resolve_storage_path(raw)
                if resolved and resolved.exists():
                    material_paths.append(str(resolved))
            resolved_example = _resolve_storage_path(material.example_path)
            if resolved_example and resolved_example.exists():
                system_example_path = str(resolved_example)
            # 若系统资料已有完整预解析缓存，则优先复用
            if isinstance(material.parsed_contents, list) and material.parsed_contents:
                pre_parsed_contents = []
                for i, item in enumerate(material.parsed_contents):
                    if not isinstance(item, dict):
                        pre_parsed_contents = None
                        break
                    content = (item.get('content') or '').strip()
                    if not content:
                        pre_parsed_contents = None
                        break
                    # 生成阶段只依赖 page/content，path 仅用于调试展示
                    pre_parsed_contents.append({
                        'page': int(item.get('page') or (i + 1)),
                        'path': item.get('path') or '',
                        'content': content,
                    })
                if pre_parsed_contents and len(pre_parsed_contents) != len(material_paths):
                    pre_parsed_contents = None
    
    # 如果没有系统资料或系统资料为空，使用用户上传的资料
    if not material_paths:
        material_paths = save_files('materials')
    
    data_paths = save_files('data')
    example_paths = save_files('examples')

    # 处理用户选择的已保存参考样例（支持多选）
    selected_example_ids = request.form.getlist('selected_example_ids')
    for example_id in selected_example_ids:
        if example_id:
            example = UserExample.query.filter_by(id=example_id, user_id=current_user.id).first()
            if example:
                resolved_example = _resolve_storage_path(example.file_path)
                if resolved_example and resolved_example.exists():
                    example_paths.append(str(resolved_example))

    # 如果系统有参考样例且用户没有上传，使用系统的
    if system_example_path and not example_paths:
        example_paths = [system_example_path]
    
    # 处理原始数据记录单（单张图片）
    raw_data_path = None
    raw_data_file = request.files.get('raw_data')
    if raw_data_file and raw_data_file.filename:
        safe = f"raw_data_{_safe_upload_name(raw_data_file.filename, 'raw_data')}"
        p = task_dir / safe
        raw_data_file.save(p)
        raw_data_path = str(p)

    # 仅在“没有上传 Excel/CSV 数据文件”时，才允许识别原始数据记录单；
    # 若同时上传了数据文件，则只在报告末尾附上原始数据记录单，不做 OCR 识别。
    has_structured_data_files = bool(data_paths)
    use_raw_data_ocr = bool(raw_data_path and not has_structured_data_files)
    append_raw_data_image = bool(raw_data_path and has_structured_data_files)

    # 获取输出格式（latex 或 word）
    format_type = request.form.get('output_format', 'latex').lower()
    if format_type not in ['latex', 'word']:
        format_type = 'latex'

    tasks[task_id] = {
        'status': 'processing', 'steps': [],
        'current_step': '准备中...', 'error': None,
        'download_url': None, 'raw_url': None,
        'user_id': current_user.id,
    }

    config = {
        'api_key': api_key, 'base_url': base_url,
        'system_prompt': system_prompt,
        'vision_model': vision_model, 'text_model': text_model,
        'material_paths': material_paths, 'data_paths': data_paths,
        'example_paths': example_paths, 'cover_info': cover_info,
        'task_dir': str(task_dir),
        'format_type': format_type,
        'raw_data_path': raw_data_path,
        'use_raw_data_ocr': use_raw_data_ocr,
        'append_raw_data_image': append_raw_data_image,
        'pre_parsed_contents': pre_parsed_contents,
    }
    tasks[task_id]['config'] = config

    # 根据格式类型调用不同的生成函数
    if format_type == 'word':
        thread = threading.Thread(target=run_word_generation, args=(task_id, config, tasks))
    else:
        thread = threading.Thread(target=run_latex_generation, args=(task_id, config, tasks))
    thread.daemon = True
    thread.start()
    return jsonify({'task_id': task_id})


def _extract_json_object(text):
    """从模型输出中提取 JSON 对象"""
    text = (text or '').strip()
    if text.startswith('```'):
        text = text.strip('`')
        text = text.replace('json', '', 1).strip()
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _revise_sections_with_instruction(section_contents, instruction, config):
    """根据用户修订意见，生成新的章节内容"""
    client = OpenAI(api_key=config['api_key'], base_url=config['base_url'])
    section_json = json.dumps(section_contents, ensure_ascii=False)
    system_prompt = (
        "你是一个物理实验报告编辑助手。"
        "你会根据用户的修订意见，对已有报告进行最小必要修改，保持其余内容风格和结构不变。"
    )
    user_prompt = (
        "下面是当前报告的章节内容（JSON）：\n"
        f"{section_json}\n\n"
        "用户修订意见：\n"
        f"{instruction}\n\n"
        "请返回一个 JSON 对象，键必须且只包含以下章节名：\n"
        f"{KNOWN_SECTIONS}\n"
        "每个键对应修订后的章节正文（字符串）。"
        "不要返回任何解释性文字，不要使用 markdown 代码块。"
    )

    extra_body = None
    model_name = str(config.get('text_model') or '').lower()
    if model_name.startswith('kimi'):
        # 通过 extra_body 透传厂商扩展参数，避免 SDK 关键字参数报错
        extra_body = {'thinking': {'type': 'disabled'}}

    resp = client.chat.completions.create(
        model=config['text_model'],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.6,
        max_tokens=8192,
        extra_body=extra_body,
    )
    raw = resp.choices[0].message.content or ''
    obj = json.loads(_extract_json_object(raw))
    revised = {}
    for section_name in KNOWN_SECTIONS:
        value = obj.get(section_name, section_contents.get(section_name, ''))
        revised[section_name] = value if isinstance(value, str) else str(value)
    return revised


@app.route('/api/revise/<task_id>', methods=['POST'])
@login_required
def revise_report(task_id):
    """基于自然语言反馈修订报告并重新编译"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    if task.get('user_id') != current_user.id and not current_user.is_admin:
        return jsonify({'error': '无权限操作该任务'}), 403
    if task.get('status') != 'done':
        return jsonify({'error': '仅可修订已完成的报告'}), 400
    if not task.get('config'):
        return jsonify({'error': '任务配置缺失，无法修订'}), 400
    if not task.get('section_contents'):
        return jsonify({'error': '未找到报告章节内容，无法修订'}), 400

    data = request.get_json() or {}
    instruction = (data.get('instruction') or '').strip()
    if not instruction:
        return jsonify({'error': '请填写修订意见'}), 400

    try:
        task['status'] = 'processing'
        task['current_step'] = '正在根据修订意见重写内容...'
        task['steps'].append(task['current_step'])

        config = task['config']
        # 原始任务目录可能已被清理线程删除，修订前确保可用
        config['task_dir'] = _ensure_task_work_dir(config.get('task_dir'), task_id)
        revised_sections = _revise_sections_with_instruction(task['section_contents'], instruction, config)
        task['section_contents'] = revised_sections

        now_tag = int(time.time())
        format_type = config.get('format_type', 'latex')
        task['current_step'] = '正在重新编译报告...'
        task['steps'].append(task['current_step'])

        if format_type == 'word':
            word_path = build_word_document_from_template(
                config['cover_info'], revised_sections, config['task_dir'],
                config.get('raw_data_path'), config.get('material_paths'), config.get('plot_paths'),
                append_raw_data_image=config.get('append_raw_data_image', False)
            )
            out_name = f'实验报告_修订_{task_id[:8]}_{now_tag}.docx'
            out_path = OUTPUT_FOLDER / out_name
            from shutil import copy2
            copy2(word_path, out_path)
            raw_name = f'报告原文_修订_{task_id[:8]}_{now_tag}.md'
            raw_path = OUTPUT_FOLDER / raw_name
            raw_parts = [f'## 修订意见\n\n{instruction}\n']
            for sec_name in KNOWN_SECTIONS:
                raw_parts.append(f'## {sec_name}\n\n{revised_sections.get(sec_name, "(空)")}\n')
            raw_path.write_text('\n'.join(raw_parts), encoding='utf-8')
            task['download_url'] = f'/api/download/{out_name}'
            task['raw_url'] = f'/api/download/{raw_name}'
        else:
            tex_path = build_latex_document(
                config['cover_info'], revised_sections, config['task_dir'],
                config.get('raw_data_path'), config.get('material_paths'), config.get('plot_paths'),
                config.get('append_raw_data_image', False)
            )
            out_name = f'实验报告_修订_{task_id[:8]}_{now_tag}.pdf'
            out_path = str(OUTPUT_FOLDER / out_name)
            success, compile_log = compile_latex(tex_path, out_path)
            tex_save_name = f'报告源码_修订_{task_id[:8]}_{now_tag}.tex'
            from shutil import copy2
            copy2(tex_path, OUTPUT_FOLDER / tex_save_name)
            raw_name = f'报告原文_修订_{task_id[:8]}_{now_tag}.md'
            raw_path = OUTPUT_FOLDER / raw_name
            raw_parts = [f'## 修订意见\n\n{instruction}\n']
            for sec_name in KNOWN_SECTIONS:
                raw_parts.append(f'## {sec_name}\n\n{revised_sections.get(sec_name, "(空)")}\n')
            raw_path.write_text('\n'.join(raw_parts), encoding='utf-8')

            if not success:
                task['status'] = 'error'
                task['error'] = '修订后 LaTeX 编译失败，请下载源码检查。'
                task['download_url'] = f'/api/download/{tex_save_name}'
                return jsonify({'error': task['error'], 'download_url': task['download_url']}), 500

            task['download_url'] = f'/api/download/{out_name}'
            task['raw_url'] = f'/api/download/{raw_name}'
            task['tex_url'] = f'/api/download/{tex_save_name}'

        task['status'] = 'done'
        task['current_step'] = '修订完成！'
        task['steps'].append(task['current_step'])
        return jsonify({
            'success': True,
            'download_url': task.get('download_url'),
            'raw_url': task.get('raw_url'),
            'tex_url': task.get('tex_url')
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        task['status'] = 'error'
        task['error'] = f'修订失败: {str(e)}'
        return jsonify({'error': task['error']}), 500


@app.route('/api/progress/<task_id>')
@login_required
def progress(task_id):
    """获取任务进度（支持 SSE 与 JSON 轮询）"""
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    if not _can_access_task(task, current_user):
        return jsonify({'error': '无权限访问该任务'}), 403

    # 轮询模式：返回单次 JSON，避免某些代理/网络环境下 SSE 被中断
    if request.args.get('json') == '1':
        return jsonify(_public_task_payload(task))

    def stream():
        while True:
            current_task = tasks.get(task_id)
            if not current_task:
                yield f"data: {json.dumps({'status': 'error', 'error': '任务不存在'})}\n\n"
                break
            if not _can_access_task(current_task, current_user):
                yield f"data: {json.dumps({'status': 'error', 'error': '无权限访问该任务'}, ensure_ascii=False)}\n\n"
                break
            payload = _public_task_payload(current_task)
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if current_task.get('status') in ('done', 'error'):
                break
            time.sleep(1)
    return Response(stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/download/<path:filename>')
@login_required
def download(filename):
    """下载文件"""
    if Path(filename).name != filename:
        return jsonify({'error': '文件名无效'}), 400
    output_root = OUTPUT_FOLDER.resolve()
    path = (output_root / filename).resolve()
    if output_root not in path.parents:
        return jsonify({'error': '无权限访问该文件'}), 403
    if not current_user.is_admin:
        allowed = any(
            _can_access_task(task, current_user) and _task_contains_download(task, filename)
            for task in tasks.values()
        )
        if not allowed:
            return jsonify({'error': '无权限访问该文件'}), 403
    if path.exists() and path.is_file():
        return send_file(str(path), as_attachment=True, download_name=path.name)
    return jsonify({'error': '文件不存在'}), 404


# ===================== Admin API Routes =====================

@app.route('/api/admin/stats')
@login_required
def admin_stats():
    """获取管理员统计数据"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403
    
    return jsonify({
        'user_count': User.query.count(),
        'template_count': Template.query.count(),
        'material_count': SystemMaterial.query.count()
    })


@app.route('/api/admin/cleanup', methods=['POST'])
@login_required
def admin_cleanup_files():
    """管理员手动触发过期文件清理"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403
    stats = cleanup_expired_files()
    app.logger.info(
        "[cleanup-manual] operator=%s deleted_upload_dirs=%s deleted_output_files=%s reclaimed_bytes=%s",
        current_user.email,
        stats['deleted_upload_dirs'],
        stats['deleted_output_files'],
        stats['reclaimed_bytes'],
    )
    return jsonify({
        'success': True,
        'message': '清理完成',
        **stats
    })


@app.route('/api/admin/users')
@login_required
def admin_users():
    """获取用户列表（管理员）"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403
    
    users = User.query.all()
    return jsonify([{
        'id': u.id,
        'email': u.email,
        'is_admin': u.is_admin,
        'created_at': _to_utc_iso(u.created_at),
        'last_login': _to_utc_iso(u.last_login),
        'template_count': len(u.templates),
        'has_api_key': u.has_api_key(),
    } for u in users])


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@login_required
def admin_delete_user(user_id):
    """删除用户（管理员）"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403

    if user_id == current_user.id:
        return jsonify({'error': '不能删除自己'}), 400

    user = User.query.get_or_404(user_id)

    # 禁止删除主管理员账号（admin）
    if user.email == 'admin':
        return jsonify({'error': '不能删除主管理员账号'}), 400

    try:
        # 先删除用户的 examples 关联的文件
        for example in user.examples:
            try:
                resolved = _resolve_storage_path(example.file_path)
                if resolved and resolved.exists():
                    resolved.unlink()
            except OSError:
                pass  # 文件可能已被删除

        # 级联删除：用户的 templates 和 examples 会通过 cascade 自动删除
        db.session.delete(user)
        db.session.commit()
        return jsonify({'success': True, 'message': f'用户 {user.email} 已删除'})
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'删除失败: {str(e)}'}), 500


@app.route('/api/admin/users/<int:user_id>/admin', methods=['PUT'])
@login_required
def admin_toggle_admin(user_id):
    """切换用户管理员权限（仅主管理员可操作）"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403
    
    # 只有主管理员（admin）可以册封/撤销其他管理员权限
    if current_user.email != 'admin':
        return jsonify({'error': '只有主管理员可以册封管理员权限'}), 403
    
    # 不能修改自己的权限
    if user_id == current_user.id:
        return jsonify({'error': '不能修改自己的权限'}), 400
    
    user = User.query.get_or_404(user_id)
    
    # 不能修改主管理员的权限
    if user.email == 'admin':
        return jsonify({'error': '不能修改主管理员权限'}), 400
    
    # 切换权限
    data = request.get_json()
    is_admin = data.get('is_admin', False)
    user.is_admin = is_admin
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f"已{'授予' if is_admin else '撤销'} {user.email} 的管理员权限"
    })


def _parse_system_material(material_id):
    """
    后台预解析系统资料图片，缓存 Vision 识别结果。
    只做“追加能力”，不影响已有生成流程；失败时记录 parse_error。
    """
    with app.app_context():
        material = SystemMaterial.query.get(material_id)
        if not material:
            print(f"[预解析] 资料不存在: {material_id}")
            return
        paths = material.material_paths or []
        
        print(f"[预解析] 开始解析资料 '{material.experiment_name}', 图片数量: {len(paths)}")
        
        material.parse_status = 'parsing'
        material.parse_total = len(paths)
        material.parse_done = 0
        material.parse_started_at = datetime.utcnow()
        material.parse_finished_at = None
        material.parse_error = None
        db.session.commit()

        if not paths:
            material.parsed_contents = []
            material.parsed_at = datetime.utcnow()
            material.parse_error = None
            material.parse_status = 'done'
            material.parse_finished_at = datetime.utcnow()
            db.session.commit()
            return

        # 优先使用系统 API Key，回退到用户提供的 API Key
        api_key = (os.environ.get('SYSTEM_API_KEY') or os.environ.get('MOONSHOT_API_KEY') or '').strip()
        if not api_key and user_api_key:
            api_key = user_api_key.strip()
            print(f"[预解析] 使用传入的 API Key 进行解析")
        
        if not api_key:
            material.parse_error = '未配置 SYSTEM_API_KEY 或 MOONSHOT_API_KEY，且无用户 API Key，无法预解析'
            material.parse_status = 'error'
            material.parse_finished_at = datetime.utcnow()
            db.session.commit()
            print(f"[预解析] 错误: 未配置 API Key")
            return

        base_url = (os.environ.get('SYSTEM_BASE_URL') or 'https://api.moonshot.cn/v1').strip()
        # 使用32k vision模型
        vision_model = (os.environ.get('VISION_MODEL') or 'moonshot-v1-32k-vision-preview').strip()
        
        print(f"[预解析] 使用 API: {base_url}, 模型: {vision_model}")
        
        client = OpenAI(api_key=api_key, base_url=base_url)

        try:
            from ai_generator import extract_single_image

            parsed = []
            for i, raw in enumerate(paths):
                print(f"[预解析] 正在处理第 {i+1}/{len(paths)} 张图片: {raw}")
                
                resolved = _resolve_storage_path(raw)
                if not resolved or not resolved.exists():
                    print(f"[预解析] 文件不存在: {raw}")
                    parsed.append({'page': i + 1, 'path': str(raw), 'content': '(文件不存在，跳过解析)'})
                    material.parse_done = i + 1
                    db.session.commit()
                    continue
                
                try:
                    content = extract_single_image(str(resolved), client, vision_model)
                    parsed.append({'page': i + 1, 'path': str(raw), 'content': (content or '').strip()})
                    print(f"[预解析] 第 {i+1} 张图片解析完成，长度: {len(content or '')}")
                except Exception as img_err:
                    print(f"[预解析] 第 {i+1} 张图片解析失败: {img_err}")
                    parsed.append({'page': i + 1, 'path': str(raw), 'content': f'(解析失败: {str(img_err)[:100]})'})
                
                material.parse_done = i + 1
                db.session.commit()
                if i < len(paths) - 1:
                    time.sleep(0.5)

            material.parsed_contents = parsed
            material.parsed_at = datetime.utcnow()
            material.parse_error = None
            material.parse_status = 'done'
            material.parse_finished_at = datetime.utcnow()
            db.session.commit()
            print(f"[预解析] 资料 '{material.experiment_name}' 解析完成")
        except Exception as e:
            print(f"[预解析] 解析过程出错: {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            material.parse_error = f'{type(e).__name__}: {str(e)[:200]}'
            material.parse_status = 'error'
            material.parse_finished_at = datetime.utcnow()
            db.session.commit()


@app.route('/api/admin/materials', methods=['GET', 'POST'])
@login_required
def admin_materials():
    """获取/创建系统资料（管理员）"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403
    
    if request.method == 'GET':
        materials = SystemMaterial.query.all()
        return jsonify([m.to_dict() for m in materials])
    
    # POST - 创建新资料
    experiment_name = request.form.get('experiment_name', '').strip()
    description = request.form.get('description', '').strip()
    category = request.form.get('category', '未分类').strip() or '未分类'
    is_active = request.form.get('is_active', 'true') == 'true'
    default_cover = request.form.get('default_cover', '{}')
    
    if not experiment_name:
        return jsonify({'error': '实验名称不能为空'}), 400
    
    # 解析默认封面信息
    try:
        default_cover = json.loads(default_cover) if default_cover else {}
    except json.JSONDecodeError:
        default_cover = {}
    if not isinstance(default_cover, dict):
        default_cover = {}
    default_cover['category'] = category
    
    # 保存上传的文件
    task_dir = UPLOAD_FOLDER / f"material_{int(time.time())}"
    task_dir.mkdir(parents=True, exist_ok=True)
    
    material_paths = []
    for f in request.files.getlist('materials'):
        if f.filename:
            safe = f"material_{len(material_paths)}_{_safe_upload_name(f.filename, 'material')}"
            p = task_dir / safe
            f.save(p)
            material_paths.append(_to_storage_path(p))
    
    example_path = None
    example_file = request.files.get('example')
    if example_file and example_file.filename:
        safe = f"example_{_safe_upload_name(example_file.filename, 'example')}"
        p = task_dir / safe
        example_file.save(p)
        example_path = _to_storage_path(p)
    
    material = SystemMaterial(
        experiment_name=experiment_name,
        description=description,
        is_active=is_active,
        default_cover=default_cover,
        material_paths=material_paths,
        example_path=example_path
    )
    db.session.add(material)
    db.session.commit()

    # 自动触发后台预解析（异步，不阻塞上传）
    parse_thread = threading.Thread(target=_parse_system_material, args=(material.id,), daemon=True)
    parse_thread.start()

    return jsonify({'success': True, 'id': material.id, 'parsing_started': True})


@app.route('/api/admin/materials/<int:material_id>', methods=['PUT', 'DELETE'])
@login_required
def admin_material_detail(material_id):
    """更新/删除系统资料（管理员）"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403

    material = SystemMaterial.query.get_or_404(material_id)

    if request.method == 'DELETE':
        cleanup_stats = _cleanup_system_material_files(material)
        db.session.delete(material)
        db.session.commit()
        app.logger.info(
            "[admin-material-delete] operator=%s material_id=%s deleted_files=%s deleted_dirs=%s",
            current_user.email,
            material_id,
            cleanup_stats['deleted_files'],
            cleanup_stats['deleted_dirs'],
        )
        return jsonify({'success': True})

    # PUT - 更新资料
    material.experiment_name = request.form.get('experiment_name', material.experiment_name).strip()
    material.description = request.form.get('description', material.description).strip()
    category = request.form.get('category', '').strip()
    material.is_active = request.form.get('is_active', 'true') == 'true'

    default_cover = request.form.get('default_cover')
    if default_cover:
        try:
            material.default_cover = json.loads(default_cover)
        except json.JSONDecodeError:
            pass

    # 支持单独更新分类
    if category:
        if not isinstance(material.default_cover, dict):
            material.default_cover = {}
        material.default_cover['category'] = category

    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/materials/<int:material_id>/parse', methods=['POST'])
@login_required
def admin_material_parse(material_id):
    """手动触发系统资料预解析（管理员）"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403
    material = SystemMaterial.query.get_or_404(material_id)
    
    # 获取当前管理员的 API Key 用于预解析
    user_api_key = current_user.get_api_key()
    if not user_api_key:
        return jsonify({'error': '请先在首页设置 Moonshot API Key 才能使用预解析功能'}), 400
    
    parse_thread = threading.Thread(target=_parse_system_material, args=(material.id, user_api_key), daemon=True)
    parse_thread.start()
    return jsonify({'success': True, 'message': '预解析任务已启动'})


@app.route('/api/admin/feedback', methods=['GET'])
@login_required
def admin_feedback_list():
    """管理员获取反馈列表"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403
    rows = Feedback.query.order_by(Feedback.created_at.desc()).all()
    status_rank = {'pending': 0, 'replied': 1, 'closed': 2}
    rows.sort(key=lambda r: status_rank.get(r.status, 9))
    return jsonify([row.to_dict() for row in rows])


@app.route('/api/admin/feedback/<int:feedback_id>/reply', methods=['PUT'])
@login_required
def admin_feedback_reply(feedback_id):
    """管理员回复反馈"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403
    row = Feedback.query.get_or_404(feedback_id)
    data = request.get_json() or {}
    reply = (data.get('reply') or '').strip()
    status = (data.get('status') or 'replied').strip()
    if not reply:
        return jsonify({'error': '回复内容不能为空'}), 400
    if status not in ('pending', 'replied', 'closed'):
        return jsonify({'error': '无效状态'}), 400

    row.admin_reply = reply
    row.status = status
    row.replied_at = datetime.utcnow()
    db.session.commit()
    return jsonify({
        'success': True,
        'message': '回复已保存',
        'feedback': row.to_dict()
    })


@app.route('/api/admin/feedback/<int:feedback_id>/status', methods=['PUT'])
@login_required
def admin_feedback_status(feedback_id):
    """管理员更新反馈状态"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403
    row = Feedback.query.get_or_404(feedback_id)
    data = request.get_json() or {}
    status = (data.get('status') or '').strip()
    if status not in ('pending', 'replied', 'closed'):
        return jsonify({'error': '无效状态'}), 400
    row.status = status
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/admin/announcements', methods=['GET', 'POST'])
@login_required
def admin_announcements():
    """管理员获取/发布公告"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403

    if request.method == 'GET':
        rows = Announcement.query.order_by(
            Announcement.is_pinned.desc(),
            Announcement.created_at.desc(),
            Announcement.id.desc()
        ).all()
        return jsonify([row.to_dict() for row in rows])

    data = request.get_json() or {}
    announcement_id = data.get('announcement_id')
    title = (data.get('title') or '').strip()
    content_md = (data.get('content_md') or '').strip()
    is_pinned = bool(data.get('is_pinned', False))
    is_active = bool(data.get('is_active', True))
    start_at = _parse_datetime_text(data.get('start_at'))
    end_at = _parse_datetime_text(data.get('end_at'))

    if not title:
        return jsonify({'error': '公告标题不能为空'}), 400
    if not content_md:
        return jsonify({'error': '公告内容不能为空'}), 400
    if len(title) > 200:
        return jsonify({'error': '公告标题过长（最多200字）'}), 400
    if len(content_md) > 50000:
        return jsonify({'error': '公告内容过长（最多50000字）'}), 400
    if (data.get('start_at') and not start_at) or (data.get('end_at') and not end_at):
        return jsonify({'error': '时间格式无效，请重新选择生效时间'}), 400
    if start_at and end_at and start_at > end_at:
        return jsonify({'error': '生效开始时间不能晚于结束时间'}), 400

    if announcement_id is not None:
        try:
            announcement_id = int(announcement_id)
        except (TypeError, ValueError):
            return jsonify({'error': '公告ID无效'}), 400

        row = Announcement.query.get_or_404(announcement_id)
        row.title = title
        row.content_md = content_md
        row.is_pinned = is_pinned
        row.is_active = is_active
        row.start_at = start_at
        row.end_at = end_at
        db.session.commit()
        return jsonify({'success': True, 'announcement': row.to_dict()})

    announcement = Announcement(
        title=title,
        content_md=content_md,
        is_pinned=is_pinned,
        is_active=is_active,
        start_at=start_at,
        end_at=end_at,
        created_by=current_user.id
    )
    db.session.add(announcement)
    db.session.commit()
    return jsonify({'success': True, 'announcement': announcement.to_dict()})


@app.route('/api/admin/announcements/<int:announcement_id>', methods=['PUT', 'DELETE'])
@login_required
def admin_announcement_detail(announcement_id):
    """管理员更新/删除公告"""
    if not current_user.is_admin:
        return jsonify({'error': '需要管理员权限'}), 403

    row = Announcement.query.get_or_404(announcement_id)

    if request.method == 'PUT':
        data = request.get_json() or {}
        title = (data.get('title') or '').strip()
        content_md = (data.get('content_md') or '').strip()
        is_pinned = bool(data.get('is_pinned', False))
        is_active = bool(data.get('is_active', True))
        start_at = _parse_datetime_text(data.get('start_at'))
        end_at = _parse_datetime_text(data.get('end_at'))

        if not title:
            return jsonify({'error': '公告标题不能为空'}), 400
        if not content_md:
            return jsonify({'error': '公告内容不能为空'}), 400
        if len(title) > 200:
            return jsonify({'error': '公告标题过长（最多200字）'}), 400
        if len(content_md) > 50000:
            return jsonify({'error': '公告内容过长（最多50000字）'}), 400
        if (data.get('start_at') and not start_at) or (data.get('end_at') and not end_at):
            return jsonify({'error': '时间格式无效，请重新选择生效时间'}), 400
        if start_at and end_at and start_at > end_at:
            return jsonify({'error': '生效开始时间不能晚于结束时间'}), 400

        row.title = title
        row.content_md = content_md
        row.is_pinned = is_pinned
        row.is_active = is_active
        row.start_at = start_at
        row.end_at = end_at
        db.session.commit()
        return jsonify({'success': True, 'announcement': row.to_dict()})

    db.session.delete(row)
    db.session.commit()
    return jsonify({'success': True, 'message': '公告已删除'})


# ===================== Verification Code API =====================

@app.route('/api/send-verification-code', methods=['POST'])
def send_verification_code():
    """发送验证码到指定邮箱"""
    import traceback
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': '无效的请求数据'}), 400

        email = data.get('email', '').strip().lower()
        purpose = data.get('purpose', 'register')  # 'register' 或 'reset_password'

        # 验证邮箱格式
        if not validate_email(email):
            return jsonify({'error': '邮箱格式无效'}), 400

        # 验证邮箱域名
        if not email.endswith('@tongji.edu.cn'):
            return jsonify({'error': '仅接受同济大学邮箱(@tongji.edu.cn)'}), 400

        # 根据用途检查邮箱状态
        if purpose == 'register':
            # 注册时检查邮箱是否已存在
            if User.query.filter_by(email=email).first():
                return jsonify({'error': '该邮箱已注册'}), 400
        elif purpose == 'reset_password':
            # 找回密码时检查邮箱是否存在
            if not User.query.filter_by(email=email).first():
                return jsonify({'error': '该邮箱未注册'}), 400
        else:
            return jsonify({'error': '无效的用途参数'}), 400

        # 检查发送频率限制（60秒内只能发送一次）
        recent_code = VerificationCode.query.filter_by(
            email=email,
            purpose=purpose
        ).order_by(VerificationCode.created_at.desc()).first()

        if recent_code:
            time_since_last = (datetime.utcnow() - recent_code.created_at).total_seconds()
            if time_since_last < 60:
                wait_seconds = int(60 - time_since_last)
                return jsonify({'error': f'请 {wait_seconds} 秒后再试'}), 429

        # 生成验证码
        code = generate_verification_code(6)

        # 发送到邮箱
        success, message = send_verification_email(email, code, purpose='注册' if purpose == 'register' else '找回密码')

        if not success:
            return jsonify({'error': message}), 500

        # 保存验证码记录
        expires_at = datetime.utcnow() + timedelta(minutes=10)  # 10分钟有效期
        code_record = VerificationCode(
            email=email,
            code=code,
            purpose=purpose,
            expires_at=expires_at
        )
        db.session.add(code_record)

        # 清理过期的验证码记录
        expired_threshold = datetime.utcnow() - timedelta(hours=24)
        VerificationCode.query.filter(
            VerificationCode.email == email,
            VerificationCode.created_at < expired_threshold
        ).delete()

        db.session.commit()

        return jsonify({
            'success': True,
            'message': '验证码已发送到您的邮箱',
            'expires_in': 600  # 10分钟（秒）
        })
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': '服务器错误，请稍后重试'}), 500


# ===================== Password Reset Routes =====================

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """密码找回页面"""
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        verification_code = request.form.get('verification_code', '').strip()
        new_password = request.form.get('new_password', '')
        password_confirm = request.form.get('password_confirm', '')

        # 验证邮箱
        if not email.endswith('@tongji.edu.cn'):
            return render_template('forgot_password.html', error='仅接受同济大学邮箱', step='request')

        # 检查用户是否存在
        user = User.query.filter_by(email=email).first()
        if not user:
            return render_template('forgot_password.html', error='该邮箱未注册', step='request')

        # 验证验证码
        if not verification_code:
            return render_template('forgot_password.html', error='请输入验证码', step='verify', email=email)

        code_record = VerificationCode.query.filter_by(
            email=email,
            code=verification_code,
            purpose='reset_password',
            is_used=False
        ).order_by(VerificationCode.created_at.desc()).first()

        if not code_record:
            return render_template('forgot_password.html', error='验证码错误', step='verify', email=email)

        if code_record.is_expired():
            return render_template('forgot_password.html', error='验证码已过期', step='request')

        # 验证密码
        if len(new_password) < 6:
            return render_template('forgot_password.html', error='密码长度至少6位', step='reset', email=email, verified=True)

        if new_password != password_confirm:
            return render_template('forgot_password.html', error='两次输入的密码不一致', step='reset', email=email, verified=True)

        # 标记验证码为已使用
        code_record.is_used = True

        # 重置密码
        user.set_password(new_password)
        db.session.commit()

        # 发送密码重置成功通知
        send_password_reset_success_email(email)

        flash('密码重置成功，请使用新密码登录', 'success')
        return redirect(url_for('login'))

    return render_template('forgot_password.html')


# ===================== Initialization =====================

def init_admin():
    """初始化管理员账号"""
    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        initial_admin_password = os.environ.get('INITIAL_ADMIN_PASSWORD', '').strip()
        if len(initial_admin_password) < 12:
            app.logger.warning('[INIT] INITIAL_ADMIN_PASSWORD 未设置或长度不足12位，跳过默认管理员创建')
            return

        initial_admin_email = os.environ.get('INITIAL_ADMIN_EMAIL', 'admin').strip() or 'admin'
        admin = User(
            email=initial_admin_email,
            is_admin=True
        )
        admin.set_password(initial_admin_password)
        db.session.add(admin)
        db.session.commit()
        app.logger.info('[INIT] 已创建初始化管理员账号: %s', initial_admin_email)


def init_database():
    """初始化数据库与默认管理员（用于生产容器启动）"""
    with app.app_context():
        db.create_all()
        ensure_announcement_schema()
        ensure_system_material_schema()
        migrate_stored_paths_to_relative()
        init_admin()


def ensure_announcement_schema():
    """
    向后兼容：
    如果 announcements 表已存在但缺少新字段，自动补齐，避免手动迁移。
    """
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if 'announcements' not in table_names:
        return

    columns = {col['name'] for col in inspector.get_columns('announcements')}
    alter_sqls = []
    if 'is_pinned' not in columns:
        alter_sqls.append("ALTER TABLE announcements ADD COLUMN is_pinned BOOLEAN DEFAULT 0")
    if 'is_active' not in columns:
        alter_sqls.append("ALTER TABLE announcements ADD COLUMN is_active BOOLEAN DEFAULT 1")
    if 'start_at' not in columns:
        alter_sqls.append("ALTER TABLE announcements ADD COLUMN start_at DATETIME")
    if 'end_at' not in columns:
        alter_sqls.append("ALTER TABLE announcements ADD COLUMN end_at DATETIME")

    if not alter_sqls:
        return

    for sql in alter_sqls:
        db.session.execute(text(sql))
    db.session.commit()


def ensure_system_material_schema():
    """
    向后兼容：
    为 system_materials 自动补齐预解析字段，避免改代码后因缺列启动失败。
    """
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if 'system_materials' not in table_names:
        return

    columns = {col['name'] for col in inspector.get_columns('system_materials')}
    alter_sqls = []
    if 'parsed_contents' not in columns:
        alter_sqls.append("ALTER TABLE system_materials ADD COLUMN parsed_contents JSON")
    if 'parsed_at' not in columns:
        alter_sqls.append("ALTER TABLE system_materials ADD COLUMN parsed_at DATETIME")
    if 'parse_error' not in columns:
        alter_sqls.append("ALTER TABLE system_materials ADD COLUMN parse_error TEXT")
    if 'parse_status' not in columns:
        alter_sqls.append("ALTER TABLE system_materials ADD COLUMN parse_status VARCHAR(20)")
    if 'parse_total' not in columns:
        alter_sqls.append("ALTER TABLE system_materials ADD COLUMN parse_total INTEGER DEFAULT 0")
    if 'parse_done' not in columns:
        alter_sqls.append("ALTER TABLE system_materials ADD COLUMN parse_done INTEGER DEFAULT 0")
    if 'parse_started_at' not in columns:
        alter_sqls.append("ALTER TABLE system_materials ADD COLUMN parse_started_at DATETIME")
    if 'parse_finished_at' not in columns:
        alter_sqls.append("ALTER TABLE system_materials ADD COLUMN parse_finished_at DATETIME")

    if not alter_sqls:
        return

    for sql in alter_sqls:
        db.session.execute(text(sql))
    db.session.commit()


def migrate_stored_paths_to_relative():
    """
    启动时将历史绝对路径迁移为相对路径，避免跨机器/跨容器路径失效。
    """
    changed = 0

    examples = UserExample.query.all()
    for ex in examples:
        new_path = _normalize_storage_path(ex.file_path)
        if new_path and new_path != (ex.file_path or ''):
            ex.file_path = new_path
            changed += 1

    materials = SystemMaterial.query.all()
    for m in materials:
        new_list = []
        for raw in (m.material_paths or []):
            new_list.append(_normalize_storage_path(raw))
        if new_list != (m.material_paths or []):
            m.material_paths = new_list
            changed += 1

        new_example = _normalize_storage_path(m.example_path)
        if new_example != (m.example_path or ''):
            m.example_path = new_example or None
            changed += 1

        if isinstance(m.parsed_contents, list):
            new_parsed = []
            parsed_changed = False
            for item in m.parsed_contents:
                if not isinstance(item, dict):
                    new_parsed.append(item)
                    continue
                copied = dict(item)
                raw_path = copied.get('path')
                if isinstance(raw_path, str):
                    normalized = _normalize_storage_path(raw_path)
                    if normalized != raw_path:
                        copied['path'] = normalized
                        parsed_changed = True
                new_parsed.append(copied)
            if parsed_changed:
                m.parsed_contents = new_parsed
                changed += 1

    feedbacks = Feedback.query.all()
    for row in feedbacks:
        old_paths = row.image_paths or []
        new_paths = [_normalize_storage_path(p) for p in old_paths]
        if new_paths != old_paths:
            row.image_paths = new_paths
            changed += 1

    if changed:
        db.session.commit()
        app.logger.info('[PATH-MIGRATION] normalized path records: %s', changed)


# 在导入模块时初始化，确保 gunicorn 部署也可自动建表
init_database()
start_cleanup_worker()


# ===================== Main Entry =====================

if __name__ == '__main__':
    raise SystemExit(
        "Production startup must use gunicorn. "
        "Run: gunicorn -c gunicorn.conf.py app:app"
    )


