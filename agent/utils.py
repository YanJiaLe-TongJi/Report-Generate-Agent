#!/usr/bin/env python3
"""
工具函数模块
"""

import base64
from pathlib import Path
from io import BytesIO
from PIL import Image


def encode_image_to_base64(path):
    """将图片编码为 base64"""
    with open(path, 'rb') as f:
        return base64.b64encode(f.read()).decode('utf-8')


def get_mime_type(path):
    """根据文件扩展名获取 MIME 类型"""
    ext = Path(path).suffix.lower()
    return {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.gif': 'image/gif',
            '.webp': 'image/webp', '.bmp': 'image/bmp'}.get(ext, 'image/jpeg')


def resize_image_if_needed(path, max_dim=2048):
    """如果图片尺寸过大，则缩放并返回 base64"""
    try:
        with Image.open(path) as img:
            w, h = img.size
            if max(w, h) > max_dim:
                ratio = max_dim / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
                buf = BytesIO()
                fmt = 'JPEG' if img.mode != 'RGBA' else 'PNG'
                img.save(buf, format=fmt, quality=90)
                return base64.b64encode(buf.getvalue()).decode('utf-8'), \
                       'image/jpeg' if fmt == 'JPEG' else 'image/png'
    except Exception:
        pass
    return encode_image_to_base64(path), get_mime_type(path)
