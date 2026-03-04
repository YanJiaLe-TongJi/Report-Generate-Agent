# 物理实验报告生成系统项目说明

## 1. 项目简介

这是一个基于 Flask 的 Web 应用，用于辅助生成和修订物理实验报告，支持用户上传实验资料、数据文件和参考样例，通过大模型生成 LaTeX/Word 报告，并提供管理员后台进行用户与系统资料管理。

## 2. 核心功能

- 用户注册/登录（邮箱验证码、找回密码）
- API Key 加密存储与读取
- 报告生成（LaTeX/PDF 与 Word）
- 报告二次修订（自然语言指令）
- 系统公告发布与历史查看
- 问题反馈提交（文字 + 图片）与管理员回复
- 管理端用户管理、反馈管理、资料管理、临时文件清理

## 3. 技术栈

- 后端：Flask + Flask-Login + Flask-SQLAlchemy
- 数据库：SQLite（默认）/ 可通过 `DATABASE_URL` 切换
- 前端：原生 HTML/CSS/JavaScript（模板在 `templates/`）
- 文档生成：LaTeX 工具链 + Word 模板生成流程
- 邮件：SMTP（验证码与通知）

## 4. 目录结构（主要）

- `app.py`：主应用入口、路由、核心业务逻辑
- `templates/`：前端页面模板（用户端与管理端）
- `word_backend.py`：Word 报告生成逻辑
- `latex_backend.py`：LaTeX 报告生成与编译逻辑
- `plot_utils.py`：图表/数据处理相关工具
- `crypto_utils.py`：API Key 加解密
- `email_utils.py`：邮件发送与邮箱校验
- `constants.py`：常量配置
- `docker-compose.yml`：容器部署配置
- `.env`：本地运行配置（敏感信息）
- `.env.example`：环境变量模板

## 5. 运行方式

### 5.1 本地运行

1. 准备 Python 环境并安装依赖（按项目实际 requirements）。
2. 配置 `agent/.env`（可基于 `.env.example`）。
3. 启动服务（示例）：

```bash
cd agent
python app.py
```

### 5.2 Docker 运行

```bash
cd agent
docker compose up -d --build
```

默认服务端口映射为 `5001`。

## 6. 关键环境变量

- `SECRET_KEY`：Flask 会话签名密钥（必填）
- `API_KEY_ENCRYPTION_KEY`：Base64 编码的 32 字节密钥，用于加密用户 API Key（必填）
- `DATABASE_URL`：数据库连接（默认 SQLite）
- `INITIAL_ADMIN_EMAIL`：初始化管理员账号
- `INITIAL_ADMIN_PASSWORD`：初始化管理员密码
- `SMTP_SERVER` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD`：邮件服务配置
- `FILE_CLEANUP_TTL_SECONDS`：临时文件保留时长
- `FILE_CLEANUP_INTERVAL_SECONDS`：清理任务扫描间隔

## 7. 主要业务流程

### 7.1 报告生成

1. 用户上传资料/数据/样例并提交生成请求。
2. 后端创建任务并异步处理。
3. 前端通过 SSE 获取任务进度。
4. 生成完成后提供下载链接（PDF/Word/原文/源码）。

### 7.2 报告修订

1. 用户对已完成报告提交修订指令。
2. 后端调用模型对章节内容进行最小必要修改。
3. 重新编译并返回新的下载链接。

### 7.3 问题反馈

1. 用户提交问题文本与图片。
2. 管理员在后台回复并更新状态（待处理/已回复/已关闭）。
3. 用户端可查看反馈历史与管理员回复。

## 8. 安全与运维注意事项

- 不要将真实 `.env` 提交到版本库。
- 生产环境必须使用强随机 `SECRET_KEY` 与 `API_KEY_ENCRYPTION_KEY`。
- 管理端接口需依赖登录态与管理员权限。
- 对前端动态渲染文本保持转义，防止 XSS。
- 建议定期轮换管理员密码与 SMTP 凭据。

## 9. 常见维护操作

- 手动触发临时文件清理：管理员后台点击“立即清理”。
- 数据库重置脚本：`reset_db.py`（谨慎使用，会清空数据）。
- 查看健康状态：`/healthz`。

## 10. 后续建议

- 增加自动化测试（鉴权、下载权限、反馈状态流转、XSS 回归）。
- 增加数据库迁移管理（如 Alembic）以替代手写兼容逻辑。
- 将任务状态从内存迁移到持久化存储，提升多实例一致性。
