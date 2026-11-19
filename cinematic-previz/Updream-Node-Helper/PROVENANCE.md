# 来源与版本范围

Bridge 0.3.1 的 server/extension 基于本机保留的完整 Updream MCP Bridge 0.3.0 分享源码快照恢复，新增 Windows 离线安装、自动本机配对和构建验证；保留原有 30 个 MCP 工具、工作区认领、未知结果和网站确认规则。

这是独立 Bridge 恢复线，不代表历史 Bridge 0.4.0/0.6.0 源码已恢复，也不把根目录 cinematic-previz 技能内容 0.6.0 降级或替换。发布范围为本目录独立插件；技能文档与内容版本另行维护。

运行时来自 Node.js 官方 v24.18.0 Windows x64，可执行文件 SHA256 为 `9a4eb5f1c29c6a2e93852ead46b999e284a6a5ca8bab4d4e241d587d025a52de`，随包附带 Node 的完整许可。生产依赖由 package-lock.json 锁定，随各依赖自带许可打包。

公开源码和下载包不含本机 `.local`、配对凭据或浏览器账户数据。用户自行安装时才生成本机私有扩展副本；不要分享生成后的安装目录。
