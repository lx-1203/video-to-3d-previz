# Updream for Codex 0.3.2 发布验收

## 交付与来源

- 独立 Codex 原生插件，源码提交 `e15026ae561c0803d4c67cee2d5371b22ea0aa10`；标签 `updream-codex-v0.3.2` 已远端解引用核验。
- [正式发行页](https://github.com/lx-1203/cinematic-previz/releases/tag/updream-codex-v0.3.2)。
- [GitHub Windows 构建](https://github.com/lx-1203/cinematic-previz/actions/runs/42515258621)：success；使用官方 npm Codex 0.167.0 完成实际离线包安装与重装测试。
- GitHub ZIP SHA256：`136efed837f2c42f7764c8865e51817e917a9ed8717370c9cb8d55cf6fd0f755`。
- 本地已测 ZIP SHA256：`e44f617794c50e27d28391f506b0b06487a41efc4f1318130d5182905f6ae088`。本地与 CI 检出换行不同，两个包分别核验；不要互换校验值。
- 重新下载 GitHub 资产后，ZIP/逐文件清单/发布摘要/构建回执一致。13 个包条目完整，10 个项目源文件与发布提交内容一致（仅允许文本换行差异），Node 二进制符合官方固定 SHA256；无账户、配对或 `.local` 私有文件。

## 验证与审查

Full 级自检已执行，OCR delegate preview/rule 已运行并按其规则人工审查；没有把委托规则输出冒称独立 LLM 扫描。

- 6 项 node:test 通过：篡改/路径拒绝、失败不报成功、断开的目录 junction 拒绝、缺 Codex 前置诊断、原生插件依赖边界、ASCII/CRLF 双击入口。
- 实际 ZIP 内 Node 在隔离 CODEX_HOME 中，通过本机官方 Codex 桌面 CLI `0.167.0-alpha.3.2` 自动注册 marketplace、安装、读回 installed/enabled/version、中文特殊字符目录安装、重复安装和无关设置保留，均通过。
- 在另一个隔离 CODEX_HOME 中，真实执行仓库 GitHub 快装命令成功，安装版本 0.3.2、enabled=true；没有修改用户真实配置。
- 官方稳定 CLI 0.167.0 的独立 Windows CI 测试通过。插件 manifest、marketplace 相对目录、工作流 YAML 和生产脚本语法均已核查。
- 原生优先的路由补充已仅追加到注册技能来源 `references/updream-mcp.md` 并同步到安装副本，保留其他用户/代理修改。

## 自动化边界

自动完成的是 Codex 插件安装，不再要求 Edge 扩展加载、文件夹选择或 JSON 配对。安装器不控制桌面，不改变浏览器安全权限，不复制账户凭据，不启动额外 Bridge 服务，也不替换旧 Bridge 0.3.1。

插件运行复用宿主公开浏览器能力和当前页面提供的 Site tools/WebMCP。当前宿主只读预检能枚举浏览器，但获取绑定/页面时超时；未完成实时画布读写验收，不能将安装成功表述为网站连接成功。用户正常登录、宿主权限与页面工具可用性仍需满足。安装后可能需要新聊天或重启 Codex 以刷新插件目录。
