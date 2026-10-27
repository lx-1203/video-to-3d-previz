# 每次调用前强制更新

此技能以 `update-source.json` 登记的GitHub公开仓库main分支为发布源。Python 3.10+即可运行更新脚本，无第三方Python依赖、GitHub令牌或桌面维护目录依赖。

每次新调用，包括同一聊天中再次调用，都先运行安装目录的 `scripts/check_update.py`。这不是定时检查或“一天一次”：每次成功放行都必须重新联网确认远端提交，同时校验本地文件。不要复用上一轮的输出或内存里的技能正文。

只有退出码0、JSON的status为current或updated且rerun_required为false，才允许重新读取返回的skill_file及本次所需参考后继续制作。若更新器自身有变化，脚本返回rerun_required=true；立即用新脚本再运行一次，直到重新检查通过。

| 返回情况 | 必须采取的动作 |
|---|---|
| current | 本轮已联网，且本地与已验证远端版本一致；重读技能后使用 |
| updated，rerun_required=false | 新版已校验、备份和安装；重读技能后使用 |
| updated，rerun_required=true | 更新器也更新了；必须先再运行新版检查 |
| blocked / 非零退出 / 输出缺失 | 停止调用本技能，报告具体原因；不能离线降级、改标current、修改校验清单或删除活跃更新锁 |

更新器先完整下载固定commit对应的清单与文件，校验路径、大小和SHA-256，再备份旧目录并切换。发生安装失败会尝试恢复旧目录，但这次调用仍然blocked；恢复旧文件不是使用旧版的许可。备份及更新状态位于安装目录以外的 `skill-updates` 目录，保留用于恢复。

本地修改、额外文件、缺文件或清单与上次成功记录不一致时，不自动覆盖。将修改保存在维护仓库，审查后发布新版；不要在安装目录重新生成manifest来绕过冲突。断网、权限/限流、远端缺失、损坏清单、回退版本或更新锁都没有“先用旧版”开关。

## 本机强制入口

把下面的等价规则写入本机实际生效的AGENTS.md，路径换成真实安装路径：

> 每次加载或执行video-to-3d-previz之前，先运行Python执行其scripts/check_update.py。只有退出码0、status为current或updated且rerun_required=false才允许继续；更新器变化须再跑一次。然后重新读取返回的skill_file。失败时保留文件和活跃锁，禁止使用旧内容、跳过联网或伪造通过。这条规则仅约束此技能。

SKILL.md自身也声明同样入口。这是调用流程中的强制规则加失败关闭的脚本，不是操作系统访问控制；不声称能阻止管理员绕过、任意直接读文件或宿主中所有工具路径。不要为了这一技能封锁无关任务。Codex技能及工具钩子的能力边界见[技能文档](https://learn.chatgpt.com/docs/build-skills)和[Hooks文档](https://learn.chatgpt.com/docs/hooks)。

## 维护和发布

维护仓库与安装目录分开。仓库里的README、测试和manifest生成工具不安装进运行技能；运行文件以release-manifest.json为准。完成修改后运行测试及技能校验，再执行scripts/build_manifest.py，审查Git差异并提交推送main。不要手改远端文件却遗漏manifest。

发布源的仓库或分支变化不自动接受，必须作为明确的维护迁移处理。初次安装须使用当前远端清单对应的全部运行文件；首次检查会与远端逐项绑定，本地未登记且与远端不同则阻止覆盖。
