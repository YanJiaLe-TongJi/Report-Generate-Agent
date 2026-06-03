# 开源前检查清单

本清单用于将项目发布到 GitHub 前做一次安全和合规检查，避免泄露敏感信息或上传无关大文件。

## 1. 明确不要上传的内容

以下内容不要提交到公开仓库：

- 真实环境变量文件：`.env`、`.env.*`
- 密钥/口令：`SECRET_KEY`、`API_KEY_ENCRYPTION_KEY`、SMTP 密码、管理员密码
- 数据库文件：`*.db`、`*.sqlite3`、`instance/`
- 用户上传与生成结果：`uploads/`、`outputs/`
- 本地日志与临时文件：`*.log`、`tmp/`、`temp/`
- 私有证书与私钥：`*.pem`、`*.key`

> 本项目已在 `.gitignore` 中忽略上述大多数路径，但发布前仍建议人工复查。

## 2. 开源前必须替换/确认

- 使用 `agent/.env.example` 作为模板，不要上传真实 `.env`
- 确认文档中未出现真实账号、邮箱、密码、密钥
- 确认截图/演示视频中未暴露 token、cookie、邮箱验证码
- 若历史中曾经泄露过凭据，先旋转凭据再开源

## 3. 提交前快速检查命令

在仓库根目录执行（建议）：

```bash
rg -n "SECRET_KEY|API_KEY_ENCRYPTION_KEY|SMTP_PASSWORD|INITIAL_ADMIN_PASSWORD|api[_-]?key|token|password" .
```

```bash
rg -n --glob "*.md" "@qq\\.com|授权码|密码|密钥|token|secret" .
```

```bash
git status
```

如果检出疑似敏感内容，请先清理后再提交。

## 4. GitHub 发布建议

- 开启仓库 Secret Scanning（若可用）
- 使用最小权限 Token
- 使用 GitHub Secrets 管理部署凭据，不写死在代码仓库
- 增加 `SECURITY.md`（漏洞上报方式）
- 建议选择开源许可证（例如 MIT）

## 5. 已经误传了怎么办

1. 立即在服务端轮换所有泄露的凭据  
2. 在仓库中删除相关文件并改写历史（如 `git filter-repo`）  
3. 强制推送后通知协作者重新拉取  
4. 检查 CI/CD、服务器环境变量是否同步更新
