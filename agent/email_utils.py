"""邮件发送工具模块 - 用于发送验证码邮件"""
import os
import smtplib
import random
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# SMTP 配置 - 从环境变量读取，避免凭据硬编码
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.qq.com').strip()
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '').strip()
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '').strip()


def _ensure_smtp_config():
    """检查 SMTP 关键配置是否齐全。"""
    if not SMTP_USER or not SMTP_PASSWORD:
        return False, "SMTP 配置缺失：请设置 SMTP_USER 和 SMTP_PASSWORD 环境变量"
    return True, ""


def generate_verification_code(length=6):
    """生成指定位数的数字验证码"""
    return ''.join(random.choices('0123456789', k=length))


def validate_email(email):
    """验证邮箱格式是否有效"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def send_verification_email(to_email, code, purpose="注册"):
    """
    发送验证码邮件

    Args:
        to_email: 收件人邮箱
        code: 验证码
        purpose: 用途（注册/找回密码）

    Returns:
        (success: bool, message: str)
    """
    try:
        ok, err = _ensure_smtp_config()
        if not ok:
            return False, err

        # 创建邮件
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f'物理实验报告生成器 - {purpose}验证码'
        # QQ邮箱要求严格的From格式，使用纯邮箱地址
        msg['From'] = SMTP_USER
        msg['To'] = to_email

        # 邮件正文 - HTML 版本
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                .container {{
                    background: #f5f5f7;
                    border-radius: 10px;
                    padding: 30px;
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 20px;
                }}
                .title {{
                    font-size: 20px;
                    font-weight: 600;
                    color: #2563eb;
                    margin-bottom: 10px;
                }}
                .code-box {{
                    background: #fff;
                    border-radius: 8px;
                    padding: 20px;
                    text-align: center;
                    margin: 20px 0;
                    border: 2px dashed #e2e8f0;
                }}
                .code {{
                    font-size: 36px;
                    font-weight: 700;
                    color: #2563eb;
                    letter-spacing: 8px;
                    font-family: 'Courier New', monospace;
                }}
                .hint {{
                    font-size: 13px;
                    color: #64748b;
                    margin-top: 15px;
                }}
                .footer {{
                    font-size: 12px;
                    color: #94a3b8;
                    text-align: center;
                    margin-top: 20px;
                }}
                .warning {{
                    background: #fef3c7;
                    border-left: 4px solid #d97706;
                    padding: 12px;
                    margin: 15px 0;
                    font-size: 13px;
                    color: #92400e;
                    border-radius: 4px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="title">📚 物理实验报告生成器</div>
                </div>

                <p>您好！</p>
                <p>您正在进行<strong>{purpose}</strong>操作，请使用以下验证码完成验证：</p>

                <div class="code-box">
                    <div class="code">{code}</div>
                    <div class="hint">验证码有效期为 10 分钟</div>
                </div>

                <div class="warning">
                    <strong>安全提示：</strong>请勿将验证码告知他人。如您没有发起此操作，请忽略此邮件。
                </div>

                <div class="footer">
                    此邮件由系统自动发送，请勿回复<br>
                    物理实验报告生成器
                </div>
            </div>
        </body>
        </html>
        """

        # 纯文本版本（备用）
        text_body = f"""
物理实验报告生成器 - {purpose}验证码

您好！
您正在进行{purpose}操作，请使用以下验证码完成验证：

验证码：{code}

验证码有效期为 10 分钟。

安全提示：请勿将验证码告知他人。如您没有发起此操作，请忽略此邮件。

此邮件由系统自动发送，请勿回复
同济大学物理实验中心
        """

        # 添加正文
        part1 = MIMEText(text_body, 'plain', 'utf-8')
        part2 = MIMEText(html_body, 'html', 'utf-8')
        msg.attach(part1)
        msg.attach(part2)

        # 连接 SMTP 服务器并发送
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
            server.starttls()  # 启用 TLS 加密
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_email, msg.as_string())

        return True, "验证码邮件已发送"

    except smtplib.SMTPAuthenticationError:
        return False, "邮件服务器认证失败，请检查邮箱授权码"
    except smtplib.SMTPRecipientsRefused:
        return False, "收件人邮箱地址无效"
    except smtplib.SMTPConnectError:
        return False, "无法连接到邮件服务器"
    except Exception as e:
        return False, f"发送邮件失败: {str(e)}"


def send_password_reset_success_email(to_email):
    """
    发送密码重置成功通知邮件

    Args:
        to_email: 收件人邮箱

    Returns:
        (success: bool, message: str)
    """
    try:
        ok, err = _ensure_smtp_config()
        if not ok:
            return False, err

        msg = MIMEMultipart('alternative')
        msg['Subject'] = '物理实验报告生成器 - 密码重置成功'
        # QQ邮箱要求严格的From格式，使用纯邮箱地址
        msg['From'] = SMTP_USER
        msg['To'] = to_email

        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                .container {{
                    background: #f0fdf4;
                    border-radius: 10px;
                    padding: 30px;
                    border: 1px solid #bbf7d0;
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 20px;
                }}
                .title {{
                    font-size: 20px;
                    font-weight: 600;
                    color: #16a34a;
                    margin-bottom: 10px;
                }}
                .footer {{
                    font-size: 12px;
                    color: #94a3b8;
                    text-align: center;
                    margin-top: 20px;
                }}
                .warning {{
                    background: #fef3c7;
                    border-left: 4px solid #d97706;
                    padding: 12px;
                    margin: 15px 0;
                    font-size: 13px;
                    color: #92400e;
                    border-radius: 4px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="title">✅ 密码重置成功</div>
                </div>

                <p>您好！</p>
                <p>您的密码已成功重置。您现在可以使用新密码登录系统。</p>

                <div class="warning">
                    <strong>安全提示：</strong>如果您没有进行此操作，请立即联系管理员或重置密码以保护账户安全。
                </div>

                <div class="footer">
                    此邮件由系统自动发送，请勿回复<br>
                    同济大学物理实验中心
                </div>
            </div>
        </body>
        </html>
        """

        text_body = f"""
物理实验报告生成器 - 密码重置成功

您好！
您的密码已成功重置。您现在可以使用新密码登录系统。

安全提示：如果您没有进行此操作，请立即联系管理员或重置密码以保护账户安全。

此邮件由系统自动发送，请勿回复
同济大学物理实验中心
        """

        part1 = MIMEText(text_body, 'plain', 'utf-8')
        part2 = MIMEText(html_body, 'html', 'utf-8')
        msg.attach(part1)
        msg.attach(part2)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, to_email, msg.as_string())

        return True, "通知邮件已发送"

    except Exception as e:
        return False, f"发送邮件失败: {str(e)}"
