# Updream 连接诊断修复 · 2026-11-09

## 根因与范围

原生 `updream-codex` 插件与独立 `updream-node-helper` Bridge 是两条连接路线。原生插件复用 Codex 宿主浏览器和网站 WebMCP；Bridge 需要 Edge/Chrome 扩展连接本机服务。两者各自维护版本，原生 0.3.2 与 Bridge 0.3.1 并非错配证据。

实测旧 Bridge 的 MCP 诊断响应正常，但 `browserConnected=false`、`extensionVersion=null`，没有已认证扩展接入。旧诊断文案只说“浏览器未连接，请检查插件配对”，没有标明它无法检查原生浏览器，容易把预期路线差异误判为原生插件故障。没有证据证明端口或配对令牌不匹配，也未修改配对信息。

本次独立诊断聊天起初浏览器清单为空，正式创建入口返回 `Browser is not available: iab`。通过 Codex 正式面板入口打开 Updream返回 `queued`，用户激活该聊天后，浏览器及标签页可见。默认 `cua.getTab` 绑定附带的界面快照超过 30 秒并重置会话；按宿主故障处理文档，用公开的 `browser.tabs.get(id)` 直接取得标签页后，读取 WebMCP 目录及当前项目成功。复现了绑定/快照阶段的超时；没有证据把所有 Runtime.evaluate 超时或延迟归因于同一内部原因，也没有证据将其归因于 Bridge。

## 修改

- 原生插件维护版 0.3.3：明确两条路线，禁止用 Bridge 状态作为原生连通的前置检查；浏览器不可用时只用宿主正式入口恢复一次，queued 时等待用户激活聊天。
- 当宿主绑定包装器附带的界面快照超时时，使用其公开文档支持的直接标签页句柄发现 WebMCP；给慢目录读取设置有界宿主调用预算，不要求先做界面快照，也不增加浏览器驱动或私有接口。
- Bridge 源码：状态与诊断新增 `connectionRoute=legacy_bridge`、`nativeBrowserConnection=not_checked`，修正工具描述与离线提示。保持原有错误码和连接逻辑，不新增浏览器权限。
- 只读诊断脚本即使连接旧服务，也会标明检测范围；离线时不探测标签页或创建工作区。
- 安装验收新增安装文件与源文件逐字节比较，以及保留独立 Bridge 配置的检查。

## 实际验证

- 原生插件基线 6 项、Bridge 基线 13 项全部通过。新增回归测试先观察到 3 项因缺少范围字段而失败，修复后通过；再增加真实 MCP / WebSocket 握手测试，证明扩展已连接也不冒称原生浏览器可用。
- 最终 `node --test Codex-Updream/tests/*.test.cjs`：6 项通过；`npm test --prefix Updream-Node-Helper`：17 项通过。
- `npm run check --prefix Updream-Node-Helper`：权限清单、文件引用和 21 项生产 JavaScript 语法检查通过。`git diff --check` 通过。
- 本地构建的 0.3.3 ZIP 共 13 个条目。真实 Codex CLI 在临时 CODEX_HOME 中验证首次安装、重装、已启用版本、源文件内容、中文特殊字符路径及原配置保留；安装测试不操作网站。最终 ZIP SHA256 及远端源码指纹记录在本机维护回执，不把历史中间包指纹当作当前产物。
- 经官方安装器更新本机原生插件至 0.3.3；已读回插件和技能内容，与当前源码逐字节一致，并比较所有 MCP 服务配置未变。
- 新诊断脚本通过现有认证配置读取真实旧 Bridge，报告 `serviceAvailable=true`、`browserConnected=false`、`pendingRequests=0`、`activeMutations=0`，并明确原生连接 `not_checked`。凭据仅在本机认证使用，未打印、复制到其他浏览器或提交。
- 激活聊天后，以宿主公开的直接标签页句柄读取实时 WebMCP 目录成功，耗时约 31 秒；调用目录实际提供的 `project_getCurrent` 成功，耗时 7616 毫秒，返回 `ok=true`、`view=canvas`，项目身份与本聊天标签页一致。不是其他聊天正在制作第三段素材的项目。只读业务调用已证明原生连接，与同一时段 Bridge 未连接并存。
- 按 global-self-review 执行 Full 级别审查：OCR 1.13.6 delegate preview/rule，读取全部改动及相关调用点，核对测试、安装包、安装副本和文档；没有发现本次改动导致的确定缺陷。Markdown 文件由人工检查；未运行另一个配置了外部模型的 OCR 仓库扫描。

## 保留的限制

共享 Bridge 服务和扩展未重载，仍运行既有 0.3.1 发行文件；服务端新增范围字段在源码中，尚未部署到该运行实例。只读脚本的范围提示已针对该旧实例验证。原生 0.3.3 是本地已安装维护版，现有 GitHub Windows Release 仍为 0.3.2；本次源码推送不代表发布了新 Release 安装资产。

本聊天原生网页只读连接已验证；自动界面快照仍有超时，底层延迟没有在本仓库修复，不承诺所有宿主操作都不再超时。没有操作其他聊天正在制作素材的画布，没有提交生成、消耗积分、重启共享服务或更改浏览器权限。若用户选择独立 Bridge 路线，其扩展加载、启用与配对仍须单独确认，不能由原生插件安装替代。
