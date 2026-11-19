# Updream MCP Bridge 0.3.1

## Windows 双击安装

下载本仓库 [Bridge 0.3.1 发布页](https://github.com/lx-1203/cinematic-previz/releases/tag/updream-bridge-v0.3.1) 的 **Windows x64 ZIP**，完整解压后双击 **install.bat**。不要下载 GitHub 自动生成的 Source code 来代替安装包。

安装包自带经官方 SHA256 核对的 Node.js 和锁定生产依赖，用户不需要自行安装 Node/Python，不需要打开终端。默认安装到 `%LOCALAPPDATA%\UpdreamMCPBridge`，创建桌面快捷方式，启动并验证本机 MCP 服务，只更新自己管理的 Codex 配置段，保留其他设置与原配对密钥。

双击后会打开中文引导。你只需在 Edge/Chrome 的扩展管理中开启开发人员模式、加载引导中的本机专用文件夹，然后在同一浏览器打开 Updream 项目，点击扩展的“启用当前画布并刷新”。扩展加载后自动配对，无需寻找或导入 JSON 文件。安装器不会代点浏览器、移动鼠标或控制桌面；首次加载确认由你自己完成。

本包面向 Windows x64 + Edge/Chrome，尚未验证 Codex 内置浏览器加载此扩展的兼容性。请保留稳定安装目录，不要分享安装后生成的 `.local`；其中含本机配对凭据。公开 ZIP/GitHub 源码不含这些凭据。

## 连接与使用

1. 安装并启用具体 Updream 项目后，在 Codex 重载 MCP 工具或新开聊天。
2. 先检查 `updream_diagnose` 与 `updream_list_tabs`，取得真实 tabId。
3. 只读可直接读取快照；写入前创建独立 workspace 并认领真实项目。修改后完整读回，工作完成释放认领。
4. 生成可能收费，必须在用户授权范围内，保留网站确认；初始化连接不构成付款授权。

30 个 MCP 工具、参数和协作规则见 [TOOLS.md](TOOLS.md) 与 [AGENT-GUIDE.md](AGENT-GUIDE.md)。现有配对不会自动覆盖；旧服务有在途请求、活动修改或已认领工作区时安装会停止，等待工作完成。

## 源码开发

```text
npm ci --ignore-scripts
npm test
npm run check
npm run setup
```

源码安装需 Node 22.12+/24+；`setup` 仍可单独注册服务。普通用户优先下载自带运行环境的 Windows ZIP。开发调试可手动加载 `extension`、导入 `.local/connection.json`，但此方式不享有免导入配对。

```text
python scripts/package-windows.py
python scripts/verify-windows-package.py artifacts/updream-mcp-bridge-0.3.1-windows-x64.zip
```

发布构建才需要 Python 标准库和联网下载 Node 运行时；最终安装无需联网下载依赖。安装后登录 Updream 仍需网络。

隔离验证通过 `--no-open --no-shortcut --install-dir <测试目录> --codex-home <测试配置目录>` 运行，不修改真实用户安装。默认安装不要传测试参数。

## 版本范围

本目录从完整 0.3.0 分享快照恢复并增补安装体验，作为 **独立 Bridge 0.3.1** 发布；不冒称历史 Bridge 0.4/0.6 已恢复，也不改变根目录 cinematic-previz 技能内容版本。来源、运行时与凭据边界见 [PROVENANCE.md](PROVENANCE.md)。
