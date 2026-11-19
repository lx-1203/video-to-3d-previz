# Codex 优先自动安装设计

目标：用户只需双击安装入口，自动将 Updream 工作流注册为 Codex 原生插件，省掉 Edge 开发人员模式、文件夹选择、配对 JSON 和本机 Bridge 服务。不得控制用户桌面，不改浏览器安全设置、不导入账户凭据；正常网站登录、宿主权限和网站确认继续由产品处理。

选型：继续 Edge 扩展无法省去官方本地加载确认；复制浏览器配置/注入私有接口不受支持，弃用；采用 Codex 官方 local marketplace + `codex plugin marketplace add` / `codex plugin add`，结合内置浏览器 Site tools/WebMCP 作为推荐路线。旧 Bridge 0.3.1 保留为独立兼容发行线，不覆盖当前安装。

产品：独立 Updream Codex 0.3.2，`Codex-Updream/` 下含兼容 `.codex-plugin/plugin.json` 与 `skills/updream/SKILL.md`。仓库根提供 `.agents/plugins/marketplace.json`，便于 GitHub 快速安装。Windows ZIP 自带固定 SHA256 的 Node，安装器拷贝到 LocalAppData 稳定目录，仅通过官方 CLI 添加目录和插件，不手写 plugin cache 或 enabled 状态。采用复制出的本地 marketplace，离线完成安装；CLI 缺失/被组织策略拒绝时清晰失败。

技能运行：通过 Codex 已提供的浏览器工具发现当前 Updream 项目；优先使用页面真实 WebMCP 工具；每轮获取实时目录和 Schema。没有原生工具时仅使用该浏览器公开的界面能力，禁止脚本注入、私有 RPC、凭据搬运或伪造扩展连接。不操作桌面鼠标/键盘。项目目标和写入范围必须来自用户；登录/权限/读取超时/接口缺失准确报告。安装完成不等于画布连接完成。

当前只读预检：内置浏览器能被枚举，但获取浏览器绑定/标签页多次超时，因此本次不声称网站读写已验证。自动安装功能与实时浏览器能力分别验收。

验收：真实 Codex CLI 在隔离 CODEX_HOME 中添加本地 marketplace、安装插件、JSON 列表读回 enabled 与版本，重复安装保留无关设置；缺 CLI 和命令失败不得输出成功。Windows bat ASCII/CRLF、包清单、路径边界与篡改检测；实际 ZIP 内 Node 运行隔离安装。当前用户的安装和 UI不参与测试。GitHub 源码、独立标签/资产与文件指纹读回验证。
