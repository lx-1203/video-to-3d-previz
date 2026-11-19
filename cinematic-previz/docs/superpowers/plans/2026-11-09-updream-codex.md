# Codex 自动安装实现计划

**目标：** 用官方 CLI 全自动安装 Codex 插件，移除 Edge 手工加载步骤。
**架构：** 兼容插件 manifest + 独立工作流 skill + local marketplace + 自带 Node 的 Windows 安装器；无新增常驻服务。
**技术栈：** Node 标准库、Codex 官方 CLI、Python 标准库 ZIP、GitHub Windows 构建。

- [x] 创建 `.agents/plugins/marketplace.json` 与 `Codex-Updream/plugin/.codex-plugin/plugin.json`；编写 native browser 工作流和实际限制说明。
- [x] 先写 `Codex-Updream/tests/installer.test.cjs`：路径/校验/CLI错误/真实 CLI 隔离注册与重装；运行红灯后实现 `scripts/install.cjs`。
- [x] 安装器发现已安装 Codex，输出可读阶段；通过官方命令注册 local marketplace、安装、列表读回，保留用户既有配置，不设置浏览器权限。
- [x] 新增 ASCII/CRLF `install.bat`、离线包构建/验包和独立 Codex 发布工作流。复用已校验 Node，无第三方生产依赖。
- [x] 用真实 Codex CLI 和隔离配置验证安装、版本、启用和重复安装。不能将 CLI测试称为浏览器实站测试。
- [x] Full 自检与 OCR委托、打包实际ZIP、桌面交付；同步 GitHub main 和独立发行标签，下载资产核验内容及来源。

验证命令：`node --test Codex-Updream/tests/*.test.cjs`；`python Codex-Updream/scripts/package-windows.py`；`python Codex-Updream/scripts/verify-package.py <zip> --codex-exe <official exe>`；`ocr delegate preview --format json`；`git ls-remote origin refs/heads/main`。
