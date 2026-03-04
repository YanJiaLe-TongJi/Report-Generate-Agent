/**
 * API Key 加密工具
 * 使用 AES-256-GCM 算法，与后端 Python crypto_utils 兼容
 */

const CRYPTO = {
    // 32 字节密钥（与后端默认密钥相同）
    // 生产环境应从环境变量或安全的方式获取
    KEY: new TextEncoder().encode('physics-report-generator-key-32b'),

    /**
     * 加密 API Key
     * @param {string} plaintext - 明文的 API Key
     * @returns {Promise<{ciphertext: string, nonce: string}>} - 加密结果
     */
    async encrypt(plaintext) {
        if (!plaintext) {
            return { ciphertext: '', nonce: '' };
        }

        // 生成随机 nonce (12 字节)
        const nonce = crypto.getRandomValues(new Uint8Array(12));

        // 导入密钥
        const key = await crypto.subtle.importKey(
            'raw',
            this.KEY,
            { name: 'AES-GCM', length: 256 },
            false,
            ['encrypt']
        );

        // 加密数据
        const plaintextBytes = new TextEncoder().encode(plaintext);
        const ciphertext = await crypto.subtle.encrypt(
            { name: 'AES-GCM', iv: nonce },
            key,
            plaintextBytes
        );

        // 转换为 base64
        return {
            ciphertext: this.arrayBufferToBase64(ciphertext),
            nonce: this.arrayBufferToBase64(nonce)
        };
    },

    /**
     * 解密 API Key（调试用，前端一般不需要解密）
     * @param {string} ciphertextB64 - base64 编码的加密数据
     * @param {string} nonceB64 - base64 编码的 nonce
     * @returns {Promise<string>} - 解密后的明文
     */
    async decrypt(ciphertextB64, nonceB64) {
        if (!ciphertextB64 || !nonceB64) {
            return '';
        }

        // 解码 base64
        const ciphertext = this.base64ToArrayBuffer(ciphertextB64);
        const nonce = this.base64ToArrayBuffer(nonceB64);

        // 导入密钥
        const key = await crypto.subtle.importKey(
            'raw',
            this.KEY,
            { name: 'AES-GCM', length: 256 },
            false,
            ['decrypt']
        );

        // 解密数据
        const plaintextBytes = await crypto.subtle.decrypt(
            { name: 'AES-GCM', iv: nonce },
            key,
            ciphertext
        );

        return new TextDecoder().decode(plaintextBytes);
    },

    /**
     * ArrayBuffer 转 Base64
     */
    arrayBufferToBase64(buffer) {
        const bytes = new Uint8Array(buffer);
        let binary = '';
        for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    },

    /**
     * Base64 转 ArrayBuffer
     */
    base64ToArrayBuffer(base64) {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes.buffer;
    }
};

// 导出供其他模块使用
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CRYPTO;
}
