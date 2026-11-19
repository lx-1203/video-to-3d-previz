# Updream for Codex 0.3.7

0.3.7 新增原位更新，自动沿用已登记的安装目录，防止旧包覆盖新版。下方 Windows 下载链接指向已发布的 0.3.2。可按文末命令从当前源码构建 0.3.7。

这个版本优先使用 **Codex 桌面端自带浏览器**。不再要求安装 Edge 扩展、开启开发人员模式、选择扩展文件夹、导入配对 JSON 或启动额外 Bridge 服务。

## 双击安装

1. 已安装 Windows x64 Codex 桌面端。
2. 下载 [Windows ZIP](https://github.com/lx-1203/cinematic-previz/releases/tag/updream-codex-v0.3.2)，完整解压，双击 **install.bat**。
3. 安装器自动找到 Codex，通过官方命令注册本地插件目录并安装，最后读回安装、启用和版本状态。成功时显示 `SUCCESS`。
4. 在 Codex 新开聊天；如果插件列表仍旧，重启 Codex一次。发送：**使用 Updream Codex 插件，只读检查当前 Updream 画布和可用工具。**

安装器自带 Node，不需要另装 Node/Python，不打开 Edge，不移动鼠标，不控制桌面。默认稳定目录是 `%LOCALAPPDATA%\UpdreamCodex`。已有其他 Codex 配置由官方 CLI 保留，安装前在本机 `.local` 留存配置备份；不要分享这个私有目录。

## Windows 无人值守安装

完整解压 Windows ZIP，在解压目录中用 PowerShell 执行以下命令。它直接运行同一个安装器，跳过双击入口末尾的暂停，无需单独安装 Node。

```powershell
.\runtime\node.exe .\scripts\install.cjs
```

安装程序输出 `SUCCESS` 和安装结果 JSON，成功时退出码为 `0`，失败时为 `1`。如果桌面端使用非默认 Codex 配置目录，可添加 `--codex-home '已存在的 Codex 配置目录'`。

更新已有本地安装时，给同一命令加 `--update-existing`。安装器从 Codex 读出现有 `xingji-updream` 目录，直接更新该目录并通过官方 CLI 刷新启用的插件，核对已生效的版本与插件文件。维护者可在下方验包命令末尾加 `--apply-local`，通过隔离安装验证后自动应用到本机。

这个命令安装的是 **Codex 应用插件**，可在「集成 → 插件」查看。独立 Bridge 是浏览器扩展，首次加载走浏览器的「加载未打包的扩展程序」入口。当前没有经过验证的 Codex 内置浏览器静默加载命令。

## 从 GitHub 快速安装

已能使用 Codex CLI 时运行：

```text
codex plugin marketplace add lx-1203/video-to-3d-previz
codex plugin add updream-codex@xingji-updream
```

本仓库 `.agents/plugins/marketplace.json` 是正式 marketplace 入口。它与 Windows 离线包中的 local marketplace 使用相同插件标识。重复安装使用同一来源；如要从本地来源切换到 GitHub 来源，应先按 Codex 官方命令管理原 marketplace，而不要手改缓存。

## 自动安装与网站连接分开

本插件提供 Codex 工作流，复用宿主已有的浏览器能力；它不自带浏览器驱动，也不新增账户凭据或修改安全权限。进入 Updream 后优先发现其实际提供的 Site tools/WebMCP；没有合适网站工具时，可使用同一浏览器已公开的页面操作能力。

用户需登录 Updream，宿主需允许使用浏览器。是否连接成功以当前项目的实际读取结果为准。获取到工具目录后，在同一次执行中使用目录提供的完整名称调用。只读调用遇到宿主明确提示注册过期时重新获取一次目录。若仍失败，可在同一浏览器中检查页面界面。写入或跳转超时后，先读回结果再决定下一步。详见 [WebMCP 读取与恢复](plugin/skills/updream/references/webmcp.md)。

旧的独立 Bridge 0.3.1 继续作为 Edge/Chrome 兼容路线保留；本安装器不删除、不覆盖它。历史 Bridge 0.4/0.6 源码状态与本插件发行分开。

`updream_connection_status`、`updream_diagnose` 和 `updream_list_tabs` 只检查独立 Bridge，不能验证 Codex 原生浏览器。原生路线应通过当前聊天的宿主浏览器读取网页验证。浏览器不可用时可用宿主正式入口尝试打开一次。若返回 `queued`，需用户切换到该聊天后才能显示。

官方依据：[Codex 插件命令](https://learn.chatgpt.com/docs/developer-commands)、[插件打包](https://developers.openai.com/plugins/build/plugins)、[网站工具](https://learn.chatgpt.com/docs/webmcp)。

## 开发与验证

```text
node --test Codex-Updream/tests/*.test.cjs
python Codex-Updream/scripts/package-windows.py
python Codex-Updream/scripts/verify-package.py Codex-Updream/artifacts/updream-codex-0.3.7-windows-x64.zip --codex-exe <官方Codex.exe路径>
```

实际 ZIP 安装测试只使用临时 CODEX_HOME，验证官方 CLI、安装版本/启用状态、重装和配置保留，不操作真实桌面或网站。包内 Node 固定为官方 24.18.0 Windows x64，SHA256 为 `9a4eb5f1c29c6a2e93852ead46b999e284a6a5ca8bab4d4e241d587d025a52de`，随包保留官方许可。
