# Updream MCP Bridge 0.3.1 发布核验

- 发布范围：`Updream-Node-Helper/` 独立 Bridge，基于保留的完整 0.3.0 源码恢复；历史 0.4/0.6 Bridge 未恢复，技能内容版本保持 0.6.0。
- 源码提交：`8b667e6fd8ce82493ab441be46ae2f08d0a6a071`。
- 标签：`updream-bridge-v0.3.1`，已从远端核对标签解引用与源码提交一致。
- [Windows 构建与测试](https://github.com/lx-1203/cinematic-previz/actions/runs/42513495210)：success。
- [正式下载](https://github.com/lx-1203/cinematic-previz/releases/tag/updream-bridge-v0.3.1)：Windows x64 ZIP、SHA256SUMS、逐文件构建回执均已公开。
- GitHub ZIP SHA256：`6042162442125bb9f2c7ad6f933ece1f2b7f8a19b801d5f2eb24a031c558ee06`。
- 本地验证构建 ZIP SHA256：`75a107b1d3e2abc41097a55cedf19c12f41c17b4b3b5ab0a6e62d1b4e5cbafab`。本地与 Windows CI 的文本检出换行不同，不能互换这两个 ZIP 的校验值；已分别校验来源和内容。

## 已运行的验证

1. 13 项 node:test：配置保留、未管理/损坏配置拒绝、精确备份、重复安装、包篡改/路径检查、私有扩展配对、已有配对保留、真实本机服务认证和网页来源拒绝。
2. 21 个生产 JavaScript 文件语法、扩展 manifest 权限与资源范围检查；30 个 MCP 工具的 JSON Schema 示例校验；npm 官方审计为零漏洞。
3. 实际离线 ZIP 解压后使用包内 Node：首次安装、中文及特殊字符路径、重装、其他 Codex 配置保留、私有配对副本，以及模拟扩展与真实 daemon 的认证握手均通过。
4. OCR delegate preview/rule 已执行，并按返回规则人工交叉审查。修复了启动器路径转义、锁定运行时文件重装失败、daemon 使用临时运行时路径三个已确认问题。没有宣称运行独立 OCR LLM 扫描。
5. 重新下载 GitHub 发布资产；ZIP SHA256 与发布摘要/回执一致；逐项核对 3735 个条目及清单；37 个项目源文件与远端源码提交一致（仅允许 Windows 检出换行差异），Node 可执行文件符合官方固定 SHA256；公开包无 `.local` 或私有配对文件。
6. 安装说明的维护范围补充已同步到注册技能来源及安装副本，文件 SHA256 为 `f58004bdd6a913f8ffff6b2d0f495d009070881cd089d9bc2f30a58f1c540331`。没有夹带其他代理在技能来源中的未提交修改。

## 验收边界

本轮只修改文件、打包和发布，没有控制用户桌面，也没有在用户真实浏览器中加载扩展。首次浏览器原生加载确认及 Updream 画布启用由用户自行完成；不能把模拟认证握手称为真实网站操作验收。Codex 内置浏览器加载兼容性仍未验证，本包明确面向 Edge/Chrome。
