# 2026-09-13 网站模型更新

本分支基于 GitHub main 建立，但 app.py、ai_generator.py 与页面先对齐实际生产容器的源码（线上使用 pre-assistant-63bfd9d62e523f6d2123b74e4cecbf66ca2c23f7 版本）。因此相对 main 的 diff 包含移除线上未启用的实验助手入口和路由。没有变更远端 main，也没有将桌面版代码覆盖网站。

新增共享模型适配层、加密的账户模型设置表、写作与视觉分别路由、六种接口选择、真实连接/图片测试入口，以及每日同步的 Kimi 官方目录。业务调用的供应商参数集中于 model_providers.py。图片识别或章节失败明确报错，不将错误文字导出成成功报告。

## 验证

- 25 项自动测试通过，覆盖官方目录/下线模型、失败保留缓存、图片能力路由、跨服务商密钥隔离、私网接口阻断、加密存储、认证权限、生成接口混合配置、模型错误脱敏，以及 SDK 兼容性。
- 使用与服务器相同的 OpenAI SDK 2.24.0 验证。requirements 限制到兼容的 1.x/2.x，并显式声明 httpx。
- 隔离服务器容器验证真实数据库副本迁移、设置加密读写、登录后页面和 SDK 构造。
- 浏览器交互检查：Kimi K3 自动隐藏独立视觉配置，DeepSeek 纯文本模型显示视觉配置，并可搭配 Qwen。
- 生产保留 113 个账户和 16 个系统实验；登录后页面、资料库接口和全部预设域名解析通过。
- 爬虫在服务器真实抓取成功，目录有 4 个可用 Kimi 模型。
- 没有使用用户密钥批量调用模型。本轮模型生成、401/403/404/429/超时错误覆盖采用模拟；各账户余额、模型开通及实际报告质量需使用页面连接测试和实际生成确认。

## 当前部署与回退

生产容器：report-app；镜像：agent-report-app:web-models-20260913。

新源码和模板目录：/root/report-web-models-20260913。仍挂载 /root/my-web/agent 下的 uploads、outputs、instance 数据目录。模板改为新源码中的 agent/templates。更新镜像应使用 deploy/Dockerfile.web-models（基于现有带 TeX 的生产基础镜像），不要直接用旧 main 重新覆盖运行容器。

旧容器已停止并保留：report-app-before-models-20260913。

数据库切换前备份：/root/my-web/agent/instance/before-model-switch-20260913.db。旧容器与新版共用数据库，新表为增量添加；回退通常无需还原数据库，否则会丢失切换后用户的新修改。

如需回退，在确认没有生成任务运行后：

```sh
systemctl disable --now report-model-catalog.timer
docker stop report-app
docker rename report-app report-app-models-rollback
docker rename report-app-before-models-20260913 report-app
docker start report-app
```

每日任务：report-model-catalog.timer，服务器 CST 时区每天 04:15，Persistent=true，首次任务已成功执行；下一次 2026-09-14 04:15。
