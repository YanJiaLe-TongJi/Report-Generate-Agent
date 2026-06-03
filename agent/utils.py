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


def resize_image_if_needed(path, max_dim=2048, max_file_size_mb=20):
    """如果图片尺寸过大，则缩放并返回 base64。
    
    优化:
    1. 使用更快的 BILINEAR 算法替代 LANCZOS
    2. 添加文件大小限制检查
    3. 添加日志输出便于调试
    """
    import os
    import logging
    
    logger = logging.getLogger(__name__)
    file_size_mb = os.path.getsize(path) / (1024 * 1024)
    
    # 检查文件大小限制
    if file_size_mb > max_file_size_mb:
        logger.warning(f"图片文件过大: {path} ({file_size_mb:.1f}MB > {max_file_size_mb}MB)")
        raise ValueError(f"图片文件过大 ({file_size_mb:.1f}MB)，请压缩后上传（建议不超过 {max_file_size_mb}MB）")
    
    try:
        with Image.open(path) as img:
            w, h = img.size
            logger.info(f"处理图片: {path}, 尺寸: {w}x{h}, 大小: {file_size_mb:.1f}MB")
            
            if max(w, h) > max_dim:
                ratio = max_dim / max(w, h)
                new_w, new_h = int(w * ratio), int(h * ratio)
                logger.info(f"缩放图片: {w}x{h} -> {new_w}x{new_h}")
                
                # 使用更快的 BILINEAR 算法替代 LANCZOS
                img_resized = img.resize((new_w, new_h), Image.BILINEAR)
                
                buf = BytesIO()
                fmt = 'JPEG' if img_resized.mode != 'RGBA' else 'PNG'
                
                # 对于大图片降低质量以加快处理
                quality = 85 if file_size_mb > 5 else 90
                img_resized.save(buf, format=fmt, quality=quality, optimize=True)
                
                result_size = len(buf.getvalue()) / (1024 * 1024)
                logger.info(f"图片处理完成: {result_size:.1f}MB (质量={quality})")
                
                return base64.b64encode(buf.getvalue()).decode('utf-8'), \
                       'image/jpeg' if fmt == 'JPEG' else 'image/png'
            else:
                logger.info(f"图片无需缩放，直接编码")
    except ValueError:
        raise
    except Exception as e:
        logger.warning(f"图片处理失败，使用原始文件: {e}")
    
    return encode_image_to_base64(path), get_mime_type(path)
