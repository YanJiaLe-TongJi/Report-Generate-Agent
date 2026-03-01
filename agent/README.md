# 物理实验报告生成系统

基于 Flask 的物理实验报告生成与管理平台，支持用户上传实验资料、生成 LaTeX/Word 报告、二次修订，以及管理员后台管理用户、资料、公告与反馈。

---

## 功能概览

- 用户注册/登录、邮箱验证码、找回密码
- API Key 加密保存
- 报告生成（LaTeX/PDF、Word）
- 报告自然语言修订并重新编译
- 系统公告展示与历史记录
- 问题反馈提交（文字 + 图片）与管理员回复
- 管理端：用户管理、系统资料管理、反馈处理、临时文件清理

---

## 技术栈

- Python + Flask
- Flask-Login / Flask-SQLAlchemy
- SQLite（默认，可替换）
- 原生 HTML/CSS/JavaScript
- LaTeX / Word 文档生成流程

---

## 快速开始

### 1) 准备环境变量

在 `agent` 目录下创建并填写 `.env`：

```bash
cp .env.example .env
```

至少需要配置：

- `SECRET_KEY`
- `API_KEY_ENCRYPTION_KEY`（Base64 编码的 32 字节密钥）
- `INITIAL_ADMIN_PASSWORD`
- （如需邮箱验证码）`SMTP_USER` / `SMTP_PASSWORD`

生成加密密钥示例：

```bash
python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
```

### 2) 本地启动

```bash
cd agent
python app.py
```

服务默认监听 `5001` 端口。

### 3) Docker 启动

```bash
cd agent
docker compose up -d --build
```

---

## 目录说明

- `app.py`：主应用入口与路由
- `templates/`：前端模板（用户端 + 管理端）
- `latex_backend.py`：LaTeX 报告生成与编译
- `word_backend.py`：Word 报告生成
- `plot_utils.py`：图表与数据处理
- `crypto_utils.py`：API Key 加解密
- `email_utils.py`：邮件与验证码
- `PROJECT_DESCRIPTION.md`：更完整的项目说明

---

## 常用接口（示例）

- 健康检查：`GET /healthz`
- 任务进度：`GET /api/progress/<task_id>`
- 报告下载：`GET /api/download/<filename>`
- 提交反馈：`POST /api/feedback`

> 说明：管理接口需管理员身份访问。

---

## 安全建议

- 不要提交真实 `.env` 到仓库
- 定期轮换 `SECRET_KEY`、管理员密码、SMTP 凭据
- 生产环境请使用 HTTPS 与反向代理

---

## 维护说明

- 临时文件支持自动清理（可配置 TTL 与扫描间隔）
- 管理员可在后台手动触发清理
- 数据库重置脚本：`reset_db.py`（会清空数据，请谨慎）

---

## 开发者指南

### 本地开发环境

```bash
cd agent
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

### 建议开发流程

1. 新建功能分支开发
2. 修改后先本地冒烟验证（登录、生成、下载、管理后台）
3. 提交前检查敏感信息与临时文件
4. 发起 PR 并附带测试说明

### 最低回归建议

- 普通用户：注册/登录、上传资料、生成报告、下载报告
- 管理员：用户列表、反馈回复、公告发布、资料增删改
- 安全项：任务进度鉴权、下载权限校验、反馈内容转义

---

## 开源前检查

开源前请先阅读：`OPEN_SOURCE_CHECKLIST.md`
