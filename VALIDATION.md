# 桌面版验证记录

## 已验证

- 21 项自动化测试：生成主流程、独立视觉模型、数据解析、可编辑公式、Word 表格、修订保留未修改章节、模型适配、密钥不进入持久化数据、loopback 访问限制、任务中断恢复、进程停止及编译失败保留文件。
- JavaScript 语法检查、Python 模块编译检查与 Git diff 空白检查。
- 本机 Apple Silicon：开发窗口与打包窗口启动，资料/模型/说明页面，加载已保存任务；原生另存为已实际保存文件，SHA-256 与原件一致。
- Word 输出使用独立渲染器逐页检查了中文、封面、公式、表格与原始记录附录。
- 完整版打包程序使用自带 XeLaTeX、宏包和 Fandol 字体真实生成 PDF，逐页检查了中文和数学公式。测试不调用任何模型 API。
- 每个安装包构建都必须先通过打包程序的 `--self-test`，失败不产出安装包。

## 测试与真实调用的边界

- 生成主流程与模型适配使用模拟响应测试；没有使用用户的真实 API Key，没有向模型服务商提交私人资料或产生模型费用。
- 本机 GUI 检查覆盖 Apple Silicon。Windows / Intel Mac 通过各自 GitHub runner 执行自动测试、打包和文档自测，不等同于在所有用户系统版本上人工点验安装向导与 GUI。
- 本机未安装系统 XeLaTeX。完整版从 App 内部找到编译器；Word 构建不依赖 Office。开发机和 CI 仍有 Python 开发环境，因此这是独立打包自测，不冒称裸机人工验证。
- 当前安装包是未获开发者证书签名/公证的测试包，不是商店发行版。

## 重现

```sh
python -m pytest -q
node --check agent/static/app.js
python -m compileall -q agent
python packaging/build.py --edition word
python packaging/prepare_tex.py --destination work/texlive
python packaging/build.py --edition full --tex-runtime work/texlive
```

`REPORT_APP_DATA` 可指定隔离测试目录。打包程序的 `--self-test` 写入演示报告，不读取密钥或发起模型请求。正式使用不要将测试目录当作个人资料目录。
