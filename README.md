# 物理报告生成器桌面版

单机使用的物理实验报告工具。双击 App，配置自己的模型 API，导入资料和实验数据，即可生成和修订报告。不需要安装 Python、启动终端或手动运行 Web 服务。

## 下载版本

| 版本 | 输出 | 运行依赖 |
|---|---|---|
| Word 轻量版 | DOCX、报告原文、数据确认表 | 已包含 Python 与文档处理依赖 |
| 完整版 | 轻量版全部功能、PDF、LaTeX 源码资源包 | 额外包含私有 TeX Live/XeLaTeX 与 Fandol 中文字体 |

构建目标为 Windows x64、macOS arm64 和 macOS Intel。每个系统独立构建。安装包名称中的 `unsigned` 表示测试分发包，未经开发者证书签名和 Apple 公证。Windows 安装程序自动配置 WebView2，首次安装可能需要网络。macOS 将 App 拖入 Applications 即可。

## 使用

1. **模型设置**：分别选择报告写作与图片识别服务商，填写 API Key，测试后保存。首次启动不预选服务商。
2. **导入资料**：支持实验资料 PNG/JPEG、数据 XLSX/XLS/CSV、原始记录 PNG/JPEG、Word 参考样例 DOCX；导入后勾选本次使用的文件。
3. **填写信息**：实验名称、姓名、学号、组号、日期。可选择本地提示词模板；没有数据时可明确开启框架模式。
4. **生成并保存**：报告完成后点击结果文件，选择本地保存位置。完整版可保存包括图片的 LaTeX 源码 ZIP；编译失败仍保留原文、源码与日志。
5. **修订**：在历史记录中填写自然语言修订要求。每次修订创建新版本，保留原版本；重启后仍可修订。

仅提供原始记录图片时，首先生成 Excel 确认表。保存、核对并修改后，重新导入表格，勾选它，然后点击原任务的“使用选中数据继续”。同时提供结构化数据与记录图片时，使用表格计算，记录图片作为附录。

## 模型服务

| 服务商 | 预设写作模型 | 预设视觉模型 | Base URL |
|---|---|---|---|
| DeepSeek | deepseek-chat | 留空，另选视觉服务 | https://api.deepseek.com/v1 |
| Kimi | kimi-k2.5 | kimi-k2.5 | https://api.moonshot.cn/v1 |
| Qwen | qwen-plus | qwen-vl-plus | https://dashscope.aliyuncs.com/compatible-mode/v1 |
| MiniMax | MiniMax-M2.5 | 留空，另选视觉服务 | https://api.minimaxi.com/v1 |
| GLM | glm-4.7 | glm-4.6v | https://open.bigmodel.cn/api/paas/v4 |
| 自定义 | 用户填写 | 用户填写 | OpenAI Chat Completions 兼容接口根地址 |

模型 ID 与地址均可修改，不必等待 App 升级。预设不是免费服务或可用性保证，是否可调用取决于服务商和账户。写作与视觉可以跨服务商搭配。API Key 留空表示保留该接口已有的密钥；切换到新接口时需重新填写。自定义接口不会自动附加厂商专属参数。

官方文档：[DeepSeek](https://api-docs.deepseek.com/)、[Kimi](https://platform.kimi.com/docs/guide/benchmark-best-practice)、[Qwen](https://help.aliyun.com/zh/model-studio/model-calling-in-sub-workspace)、[MiniMax](https://platform.minimaxi.com/docs/api-reference/api-overview)、[GLM](https://docs.bigmodel.cn/cn/guide/start/model-overview)。预设于 2026-09-10 核对；真实 API 测试需有效密钥。

## 本地数据与任务

- Windows：`%LOCALAPPDATA%\PhysicsReport`；macOS：`~/Library/Application Support/PhysicsReport`。
- API Key 存入 Windows 凭据管理器或 macOS 钥匙串，不写入 JSON、任务记录或日志。
- 配置、资料、报告与任务记录存入应用数据目录。移出资料列表时保留已导入的原文件，以免破坏历史修订。卸载程序不自动删除报告资料。
- 退出 App 会停止当前工作进程及其编译子进程。意外退出的任务下次启动标记为中断，不自动重试计费请求。
- 模型调用会将本次所需资料传给用户配置的服务商；文档构建和编译在本机进行。
- 每次运行只监听 loopback 随机端口，使用临时访问凭据，不提供局域网访问和账号系统。

## 开发与打包

```sh
python3 -m venv .venv
# 激活虚拟环境后：
python -m pip install -r agent/requirements.txt pyinstaller pytest
python agent/desktop.py
python -m pytest -q
python packaging/build.py --edition word
python packaging/prepare_tex.py --destination work/texlive
python packaging/build.py --edition full --tex-runtime work/texlive
```

Windows 完整版构建还需要 Perl，Windows 安装包构建需要 Inno Setup 的 `iscc`。构建机需要 `curl`。`prepare_tex.py` 只安装到指定目录，不修改系统 PATH。完整编译环境不依赖用户电脑的 LaTeX 安装。字体、宏包和编译器保留各自许可证。

`packaging/build.py` 会先运行打包程序的 `--self-test`：不调用模型、不读取密钥，验证中文、公式、表格、原始图片和文档依赖；完整版额外真实编译 PDF。自测失败则不生成安装包。

GitHub Actions 工作流 `Desktop test installers` 覆盖三个系统架构与两种版本，可手动执行；安装包与 SHA-256 以 Actions artifacts 保存，不自动创建公开 Release。

## 代码结构

- `agent/harness.py`：独立任务进程、生成、修订、结果持久化。
- `agent/ai_generator.py`、`word_backend.py`、`latex_backend.py`、`plot_utils.py`：生成与文档核心。
- `agent/providers.py`：模型适配与系统凭据库。
- `agent/app.py`、`desktop.py`：本地接口、桌面窗口和原生保存对话框。
- `agent/static/`、`agent/templates/`：基础前端和内置报告模板。

保留原仓库 MIT 许可证和 Git 历史。桌面版移除了账号、管理员、邮件、公告、反馈、独立问答和服务器部署系统。

## 导入配套实验资料库

在「资料与模板」选择配套的 `实验资料库.zip`，点击「导入资料库」。在生成页面按「实验大类 → 具体实验」选择整组资料，自动使用全部图片与配套 Word 样例，并填写实验名称。展开「查看整组资料」可预览原图。再导入你自己的数据即可生成。资料复制到应用数据目录，导入后不再依赖 ZIP 或服务器；重复导入相同实验的相同文件自动跳过。

也支持自行把图片按实验名称分文件夹压缩为 ZIP（PNG/JPG/DOCX，总解压大小不超过 200MB）。资料包跨 Windows/macOS、轻量版/完整版通用。图片识别仍使用你配置的视觉模型。

维护者可在旧 Web 服务器执行以下命令导出配套资料。脚本只读 SQLite 数据库的 `system_materials` 表，导出启用实验的图片和参考样例，不导出用户、密钥或用户报告：

```bash
python3 tools/export_material_library.py --database agent/instance/app.db --uploads agent/uploads --output 实验资料库.zip
```

配套资料包独立于安装程序分发，可更新资料而无需重新安装 App。旧版 App 没有 ZIP 导入入口，需要安装支持资料库的新版本。

### 自己上传资料也按组保存

在生成页面「上传自己的实验资料组」填写实验大类和实验名称，一次多选本实验的所有 PNG/JPG 图片及可选 DOCX 样例，点击「保存资料组并选用」。已上传的实验可在保存方式中选择并追加文件，后续从两级下拉框整组选用。不同大类允许同名实验；同一大类的同名实验请用追加方式。每份报告只选一个实验资料组，后端会取齐组内全部文件。自己的数据表格、原始记录和独立补充样例仍在下方单独导入。

旧配套资料库自动保留「大物上／大物下」分类；旧版零散实验资料保留在「未分类／历史上传资料」。资料组移出列表后，旧报告修订所需原文件仍保留。
