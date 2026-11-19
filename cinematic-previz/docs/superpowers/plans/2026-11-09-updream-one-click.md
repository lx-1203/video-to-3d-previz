# Updream 双击安装包实现计划

**目标：** 用户自行双击安装并免文件导入配对，发布源码及可下载安装包。

**架构：** 保留 Bridge 原有安全边界与 MCP 业务工具，独立新增 Node 安装入口、纯文件安装向导、私有扩展初始化和可复现 Windows 发布构建。

**技术栈：** Node 24、node:test、锁定 TOML 解析器、Python 标准库打包、GitHub Actions。当前聊天内联执行，不操作桌面。

## 文件与步骤

- [x] 恢复 `Updream-Node-Helper/{server,extension,scripts}` 0.3.0，明确来源、版本与范围；提交本规格和计划。
- [x] 先写 `tests/installer.test.cjs`：断言 `configureCodexText` 保留现有表、拒绝未管理表和无效 TOML，断言包验证拒绝缺失/改动文件。
- [x] 运行 `node --test tests/installer.test.cjs`，确认新模块缺失导致红灯；实现 `scripts/install.cjs`、`scripts/configure-codex.cjs`、`scripts/setup.cjs`，重复运行确认绿灯。
- [x] 先写 `tests/pairing.test.cjs`：空浏览器存储从私有扩展资源取配对，已有存储不覆盖，无私有资源仍可手动导入；运行红灯后修改 `extension/background.js` 和 popup 文案，确认绿灯。
- [x] 创建 ASCII/CRLF `install.bat` 和中文 `INSTALL.md`；安装器可通过 `--no-open --no-shortcut --install-dir <temp> --codex-home <temp> --port <free>` 隔离运行；不自动安装浏览器扩展或绕过原生确认。
- [x] 新增 `scripts/package-windows.py`：下载固定版本 Node 并验证官方 SHA256、收集锁定生产依赖、生成逐文件清单、排除 `.local` 与任何私有配对，产生 ZIP/SHA256SUMS。新增 tag 发布工作流。
- [x] 运行全部 node:test、脚本语法、真实 MCP/隔离安装/重复安装/ZIP 解压测试；跑 OCR 及人工交叉审查，修复确定缺陷；更新 README、版本、变更说明与维护状态。
- [x] 精确提交并非强推到现有 GitHub main，推独立标签；检查 Actions/Release，读回 commit、关键源文件和资产校验值，交付本地与 GitHub 下载链接。

## 验证命令

```text
npm ci --ignore-scripts
node --test tests/*.test.cjs
python scripts/package-windows.py --node-exe <verified-node.exe> --node-license <LICENSE> --out <artifacts>
python scripts/verify-windows-package.py <archive.zip>
ocr delegate preview --format json
ocr delegate rule --format json Updream-Node-Helper
git ls-remote origin refs/heads/main refs/tags/updream-bridge-v0.3.1
```

验收不包含真实桌面或账户操作；代码/模拟握手测试通过不证明用户浏览器已安装。
