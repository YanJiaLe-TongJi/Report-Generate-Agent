# Docker 部署说明

## 1. 准备环境

- 服务器安装 Docker / Docker Compose
- 开放端口 `5001`（或按需改 compose 映射）

## 2. 配置环境变量

在项目根目录创建 `.env`：

```bash
cp .env.example .env
```

修改 `.env` 中的以下关键项：

- `SECRET_KEY`：随机强密码
- `API_KEY_ENCRYPTION_KEY`：Base64 编码的 32 字节密钥（用于加密用户 API Key）

可选清理参数（默认即可）：

- `FILE_CLEANUP_TTL_SECONDS=600`：文件保留时间（秒）
- `FILE_CLEANUP_INTERVAL_SECONDS=60`：后台清理扫描间隔（秒）

## 3. 启动服务

```bash
docker compose up -d --build
```

### 非 Docker（直接用 Gunicorn 启动）

```bash
gunicorn -c gunicorn.conf.py app:app
```

常用可调参数（环境变量）：

- `GUNICORN_BIND`（默认 `0.0.0.0:5001`）
- `GUNICORN_WORKERS`（默认 `1`）
- `GUNICORN_THREADS`（默认 `8`）
- `GUNICORN_TIMEOUT`（默认 `300`）

查看日志：

```bash
docker compose logs -f
```

可只看清理日志：

```bash
docker compose logs -f | grep cleanup
```

## 4. 访问与健康检查

- 应用地址：`http://<服务器IP>:5001`
- 健康检查：`http://<服务器IP>:5001/healthz`

## 5. 数据持久化

`docker-compose.yml` 已挂载以下目录，重启/重建容器不会丢数据：

- `./instance`：SQLite 数据库（`app.db`）
- `./uploads`：用户上传文件
- `./outputs`：报告输出文件

## 6. 更新版本

拉取新代码后执行：

```bash
docker compose down
docker compose up -d --build
```

## 7. 管理员手动触发清理（可选）

登录管理员后可调用：

```bash
curl -X POST http://<服务器IP>:5001/api/admin/cleanup \
  -H "Cookie: <你的登录会话cookie>"
```

返回示例：

```json
{
  "success": true,
  "message": "清理完成",
  "deleted_upload_dirs": 2,
  "deleted_output_files": 5,
  "reclaimed_bytes": 1839204
}
```

