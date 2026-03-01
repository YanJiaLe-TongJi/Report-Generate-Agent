#!/usr/bin/env python3
"""
API Key 加密/解密工具模块
使用 AES-256-GCM 加密算法，与前端 crypto.js 兼容
"""

import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def get_encryption_key():
    """获取加密密钥，优先从环境变量读取，否则使用默认密钥"""
    key = os.environ.get('API_KEY_ENCRYPTION_KEY')
    if key:
        # 从环境变量读取的密钥应该是 base64 编码的 32 字节密钥
        return base64.b64decode(key)
    # 默认密钥（仅用于开发，生产环境应设置环境变量）
    # 32 字节密钥用于 AES-256
    return b'physics-report-generator-key-32b'


def encrypt_api_key(plaintext: str) -> tuple[str, str]:
    """
    加密 API Key
    返回: (加密后的 base64 字符串, nonce base64 字符串)
    """
    if not plaintext:
        return None, None

    key = get_encryption_key()
    aesgcm = AESGCM(key)

    # 生成随机 nonce (12 字节是 GCM 的推荐值)
    nonce = os.urandom(12)

    # 加密数据
    plaintext_bytes = plaintext.encode('utf-8')
    ciphertext = aesgcm.encrypt(nonce, plaintext_bytes, None)

    # 返回 base64 编码的结果
    return base64.b64encode(ciphertext).decode('utf-8'), \
           base64.b64encode(nonce).decode('utf-8')


def decrypt_api_key(ciphertext_b64: str, nonce_b64: str) -> str | None:
    """
    解密 API Key
    参数:
        ciphertext_b64: base64 编码的加密数据
        nonce_b64: base64 编码的 nonce
    返回: 解密后的明文，失败返回 None
    """
    if not ciphertext_b64 or not nonce_b64:
        return None

    try:
        key = get_encryption_key()
        aesgcm = AESGCM(key)

        # 解码 base64
        ciphertext = base64.b64decode(ciphertext_b64)
        nonce = base64.b64decode(nonce_b64)

        # 解密数据
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext_bytes.decode('utf-8')
    except Exception as e:
        print(f"[ERROR] API Key 解密失败: {e}")
        return None


def decrypt_api_key_if_needed(api_key: str, nonce: str = None) -> str:
    """
    智能解密 API Key
    - 如果提供了 nonce，尝试解密
    - 如果没有 nonce，直接返回原字符串（兼容明文传输）
    - 解密失败返回原字符串（可能是明文）
    """
    if not api_key:
        return api_key

    # 如果没有 nonce，认为是明文传输
    if not nonce:
        return api_key

    # 尝试解密
    decrypted = decrypt_api_key(api_key, nonce)
    if decrypted:
        return decrypted

    # 解密失败，返回原文（可能是明文）
    return api_key
