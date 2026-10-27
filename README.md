# 视频转3D白模

将参考视频复刻为可编辑的Blender白模预演，覆盖空间、机位、人物动作、身份色、焦点标记、原声音轨与4K上下对照交付。包含实际制作经验、质量门槛和任务模板；技术检查通过不等于整体95%视觉相似度通过。

## 使用

调用名：`$video-to-3d-previz`。技能正文在[SKILL.md](SKILL.md)，公开版仅包含通用方法和匿名案例，不包含原视频、Blender工程、个人路径或会话记录。

安装到Codex技能目录的`video-to-3d-previz`文件夹。复制`release-manifest.json`以及其中列出的运行文件，勿把整个Git仓库（含.git、README和tests）当成安装目录。然后在本机生效的AGENTS.md写入[调用前更新规则](references/updates.md)。

每次调用前运行：

```text
python <安装目录>/video-to-3d-previz/scripts/check_update.py
```

只有退出码0且status为current或updated、rerun_required=false，才可重新读取技能继续。网络故障、本地冲突、损坏文件或旧版本均阻止使用；没有离线回退或跳过开关。更新器本身变化时必须再检查一次。

## 维护验证

Python 3.10+与Git；Python代码和测试仅用标准库。远端检查使用git ls-remote，无需GitHub令牌或匿名REST API配额。在维护仓库中：

```text
python -m unittest discover -s tests -v
python scripts/build_manifest.py
```

随后按本机skill-creator提供的quick_validate.py校验技能，审查差异，提交并推送main。清单覆盖所有运行文件的SHA-256；更新从固定commit下载，备份完成后切换，失败则阻止调用。

这不是自动视频重建软件，也不保证任意输入达到95%。调用前约束由AGENTS.md、技能入口和更新脚本共同实现，不是宿主权限隔离机制。
