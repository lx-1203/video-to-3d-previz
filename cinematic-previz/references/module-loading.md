# 树状模块读取与专业交付

分类像树：一个节点只有一个parent，根为aigc。执行依赖另用requires连接不同枝条，保持无环；分类归属和实际工作先后分开。新增类型通常只需在既有分支加叶节点及来源，不能复制一份公共规则。

## 使用现成代码

~~~text
python "<skill-dir>/scripts/read_modules.py" tree
python "<skill-dir>/scripts/read_modules.py" tree camera --json
python "<skill-dir>/scripts/read_modules.py" read camera.orbit lighting --json
python "<skill-dir>/scripts/read_modules.py" read storyboard camera.rise-orbit --json
python "<skill-dir>/scripts/read_modules.py" check --json
~~~

所有路径由脚本自身安装位置解析，不依赖作者电脑、当前工作目录、网络或第三方Python库。默认只读；不会执行模块内命令或调用生成服务。首次同步仍按SKILL前置要求执行，读取器不替代同步器。

- tree返回分支/叶节点名称和关系，不向模型展开规则正文。代码为验证目录可能在本地读取来源，模型上下文只接收实际返回内容。
- read接受一个或多个可读节点。先带上required共享规则，再按parent和requires依赖返回正文；相同节点只返回一次，兄弟分支不会自动展开。无正文分类只用于导航，直接read会列出可选children。
- check核对整个索引、树/依赖关系、文件和精确章节；status=valid只证明索引可读。read的status=loaded不代表脚本、图片、表演或视频验收通过。
- --max-chars限制返回的规则正文字符总数，不含来源等元数据，不是token预算；超限时退出2且无半份正文。应分阶段读取必要模块，不截断必需规则，更不能删减最终脚本。
- 缺ID、重ID、父链/依赖循环、越界/缺文件、标题消失或同名标题歧义均报错，不能默默选第一处或使用旧缓存。每次重新读取，结果带目录SHA256和每个源文件SHA256；它们只证明来源版本，不证明内容正确。

## 单项查询与完整交付是不同读取范围

查一种运镜可读camera对应叶节点并组合lighting/performance/style；完整脚本必须读storyboard，它自动包含全部专业交付分支。具体运镜方法由剧情决定后追加，不能只读摄影公共规则就假设已加载16种方法。完整制作使用workflow并按阶段补充具体平台和文字关键帧等分支，禁止按分类树把所有平台和全部示例一次性灌入。

完整脚本按 [director.md](director.md) 交付核心主表与必要附表，保留全部设计语义，逐镜过 [workflow.md的全维度检查](workflow.md#逐镜交付逐项核对)。主表是分镜、当前完整视频词和最终参考节点的单一交接入口；分层按需减少的是当前不相关的参考，不是拆走主表必需内容。多阶段读取所得结果在同镜号、同版本下合流，当前词唯一维护、其他必要展示同步，不以零散模块输出代替完整脚本。

previs.start即使单独读取也包含character-assets、performance、expression-assets和execution，保障人物阶段与表情实际输入不靠临时想起。expression-assets可单独读取而不启动完整影片或平台流程。generation同时包含转场公共规则及“转场的生成与剪辑分工”，delivery包含执行记录检查；具体运镜/转场叶节点仍由剧情选择，不自动灌入所有兄弟模板。索引测试须覆盖这些实际入口，不仅校验文件存在。

只有文字或示例模板的检查无法证明实际图片或运动通过。缺图、未知模型/资产、待上游真实尾帧、不适用音频等分别说明，保持已执行原词和规划版本，不能用“专业级”字样代替证据。

## 索引维护契约

module-index.json的version为格式版本1；root为唯一根节点ID；required为每次read都携带且有正文的共享节点。nodes按稳定展示顺序保存id、title、parent，可选requires和source。source.path为技能包内相对路径，source.sections可选、必须是源文件中唯一出现的完整ATX标题行，例如“## M03｜360°环绕”；不填sections才返回整个文件。

章节包含其子标题内容，截止下一个同级或更高级标题，代码围栏中的伪标题不参与定位；不要用行号当稳定接口。保持现有来源单一，不在索引写入正文副本。变更后运行check及scripts/test_read_modules.py，并检查受影响实际读取组合。无Python时按此相同依赖关系人工读取并明确未运行自动校验。


## 公共规则的正式来源

规则正文只在下表位置维护；module-index.json的rule_sources保存对应模块与精确标题，供跨文档检查。入口给摘要，工作流说何时执行，记录文档说保存什么，平台文档说如何操作；这些位置不得复制完整政策。操作点保留简短提醒，实际执行提示仍须独立展开必要条件。

| 规则 | 正式正文 | 模块 |
| --- | --- | --- |
| 任务范围、状态、暂停与重试 | [共享边界](core.md) | core |
| 制作顺序与两次用户审核 | [阶段顺序](workflow.md#制作阶段与审核顺序) | stages |
| 生成/剪辑时长及完整句 | [时间与完整句](timing.md) | timing |
| 声音组成、音乐与口型职责 | [声音规范](sound.md) | sound |
| 当前词来源、修改与原词执行 | [提示词规范](prompts.md) | prompts |
| 单一主表与交付内容 | [导演主表](director.md#默认交付专业分镜表) | storyboard |
| 真实末态和接力输入 | [尾帧接力](frame-handoff.md) | handoff |
| 预算和付费授权 | [预算](workflow.md#视频项目预算) | budget |
| 模型能力、附件和生成检查 | [生成适配](generation.md) | generation |
| 项目字段与真实执行证据 | [制作记录](production-contract.md) | records |

只查一种方法时加载短core和该方法，按需追加专项；进入制作时加载stages及相应正式规则。storyboard仍加载全部必需专业分支，generation通过prompts带入stages、timing、sound。分类与模块ID保持兼容，不为缩短入口删除交付职责。

旧generation提示词标题和workflow完整句标题保留迁移入口；已有外部链接仍能定位，新链接直接指向正式正文。迁移入口只给链接，不存第二份规则。维护者修改政策时检查rule_sources及受影响示例，不把历史CHANGELOG当现行规范。

## 跨文档验证

```text
python "<skill-dir>/scripts/read_modules.py" check --json
python "<skill-dir>/scripts/validate_documents.py"
python -B -m unittest discover -s "<skill-dir>/scripts" -p "test_*.py"
```

validate_documents检查包内Markdown路径/标题、正式规则来源、两个当前示例中generation_seconds/edit_seconds的范围关系与声明成片总长，并用实际时长校验器核对项目JSON的generation_segments接口及引用。它不执行生成、不验证画面，也不证明没有所有语义重复；版本、审核、实际接点和音画仍按对应流程人工验收。
