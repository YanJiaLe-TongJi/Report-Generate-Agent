# Web 模型服务配置与每日目录

网页支持 Kimi、DeepSeek、Qwen、MiniMax、GLM 与自定义 OpenAI 兼容接口。写作模型标明支持图片时共用密钥、接口与模型；纯文本模型需要额外配置视觉模型才能处理图片。用户可修改模型 ID 与 Base URL；自定义模型的图片能力需勾选并通过图片连接测试确认。各服务商额度、模型开通条件独立。

Kimi 默认从官方目录选择首个当前可用模型；2026-09-13 为 kimi-k3，亦提供 kimi-k2.6。旧 K2.5 与 Moonshot V1 不再可选。K3 需要平台充值开通，不能关闭思考；适配层使用 low reasoning_effort。其他服务商预设为实施时核验的静态目录，允许修改，并非每日自动发现。

`agent/model_catalog.py` 每次从 https://platform.kimi.com/docs/models.md 获取当前可用/下线模型与多模态能力，不使用用户密钥。原子写入 `instance/model_catalog.json`，网站每次读取该文件，无需重启。网络失败、空目录、解析异常时保留旧缓存，首次无缓存使用内置目录。页面显示最近成功同步时间。此目录反映官方公开可用模型，个人账户实际开通/余额以连接测试为准。

服务器安装（Docker 容器名 report-app）：

```sh
install -m 644 deploy/model-catalog/report-model-catalog.service /etc/systemd/system/
install -m 644 deploy/model-catalog/report-model-catalog.timer /etc/systemd/system/
systemctl daemon-reload
systemctl start report-model-catalog.service
systemctl enable --now report-model-catalog.timer
systemctl list-timers report-model-catalog.timer
journalctl -u report-model-catalog.service -n 20
```

每日服务器当地时间 04:15 运行（24 小时间隔），停机错过的任务在开机后补执行。缓存位于原有 instance 持久卷。失败由 systemd 记录非零退出，不覆盖原列表。更换容器名需同步修改 service。

账户配置整体经原有 AES-GCM 加密后存储于 user_model_settings 新表；读取接口只返回 has_key，不回传密钥。旧账户 Kimi 密钥可直接沿用。跨服务商或更换 Base URL 必须重新输入密钥。修改模型设置不改变已启动任务的配置，修订继续使用该报告生成时的配置。

验证：`python -m pytest -q tests/test_web_models.py`。连接测试会调用用户配置的模型，可能产生少量平台费用；文本模型测试文本，多模态模型额外识别随机四位数字图片。识别失败或模型调用失败应显示错误，不导出充满错误文字的成功报告。

官方参考：
- https://platform.kimi.com/docs/models.md
- https://platform.kimi.com/docs/guide/kimi-k3-quickstart.md
- https://api-docs.deepseek.com/quick_start/pricing/
- https://help.aliyun.com/zh/model-studio/qwen3-7-plus
- https://platform.minimaxi.com/docs/api-reference/text-openai-api.md
- https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2
