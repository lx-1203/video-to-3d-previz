# 新接口直连与提交前核对

## 宿主还只显示旧工具时

先比较宿主可见工具和运行服务真实tools/list。扩展/服务0.4.0与宿主工具缓存是不同层，不能靠不断刷新制作页面解决宿主目录缓存。0.4.0共有33个工具；以后以真实目录为准。当前宿主无可调用的刷新工具时，不结束其他聊天、不改配对、不重启共享服务。

插件新增的本地入口可立即复用已经运行的服务。在实际插件项目目录运行：

```powershell
node scripts/mcp-session.cjs --discover
```

该CLI只列目录，不打开工作区、不上传、不生成；服务缺失或版本不符就停止，不负责启动/升级服务。不要运行setup作为只读检查。

需要操作时，在能保留JS变量的node_repl或一个完整Node脚本内使用同一个会话：

```javascript
var { createRequire } = await import('node:module');
// pluginRoot先取本次实际插件目录，例如同步脚本返回的project_root；不要照抄作者路径。
var pluginRequire = createRequire(pluginRoot + '/package.json');
var upSession = await pluginRequire('./scripts/mcp-session.cjs').connectExisting();
var catalog = await upSession.listTools();
// 只读调用示例；没有画布写入。
var state = await upSession.call('updream_connection_status', {});
```

library的call只接受实时目录中的工具，参数/业务校验仍在原服务器和网站完成，不绕过工作区。一次写入工作在同一upSession中完成open_workspace→claim_canvas→上传/节点操作→核对→release/close_workspace；所有ID均来自实际返回。不从另一个聊天借workspaceId，不用多个一次性进程分步写同一项目。不要让命令行打印完整目录Schema、令牌或base64。工作结束`await upSession.close()`；这只关闭自己的会话，不停止共享服务。未知结果仍先读回，不自动重试。

这是标准MCP SDK连接，不代表宿主原来的工具缓存已经刷新。制作界面、网站确认、专用首帧及未暴露参数仍按原流程处理。

## 准备提示词与预检

新增`scripts/preflight.cjs`只读核对一份计划与当前UI观察，不会采集页面、改提示词或提交任务。可在node_repl导入`pluginRequire('./scripts/preflight.cjs')`，或在插件目录运行：

```powershell
node scripts/preflight.cjs <本地预检JSON文件>
```

JSON顶层为`plan`与`observed`，最大1MiB。不能把计划、旧截图、旧清单或MCP视频节点的过时referenceImages当成当前UI观察。

| 对象 | 必填字段 |
| --- | --- |
| plan | projectId字符串、nodeId数字、promptTemplate、parameters、references；maxCnyPerSecond可选，默认1 |
| plan.parameters / observed.parameters | model、durationSeconds数字、resolution、aspectRatio、nativeAudio布尔值 |
| plan.references各项 | role英文用途ID、kind=image/video/audio、实际已上传url；expectedIndex可选，用于锁定已确认顺序 |
| observed | projectId、nodeId、source="visible_ui"、referencesComplete=true、实际观察UTC时间observedAt、parameters、完整prompt、references、可选quote |
| observed.references各项 | kind、当前1-based的index、url；按每类连续列出全部实际附件 |
| observed.quote | currency="CNY"或"credits"、amount、configurationFingerprint；积分还需conversion |
| quote.conversion | 实际核实的cnyPerCredit、verified=true、非空来源source；不从积分数猜人民币价格 |

模板每次引用素材都写用途占位符，例如`{{image:opening}}`、`{{image:scene}}`、`{{video:voice}}`，由实际URL确定`图片5`/`参考视频1`。不直接手写图片数字，也不对整段剧本全局替换数字。预检返回compiledPrompt供制作窗口写入；写后再次观察完整prompt，匹配才通过。当前列表中的每张图/视频都需声明用途，避免旧参考悄悄保留。观察参数应来自当前模型控件，而不是节点名称“10秒”等标签。

取得当前配置的实际报价时，调用`configurationFingerprint(observed)`把报价与这一刻的项目、节点、参数、prompt和参考列表绑定，存入quote.configurationFingerprint。此后任何配置变化都需重新观察与报价，不能为旧价格重新计算指纹来冒充新报价。observedAt只能写实际采集时间，不能为旧记录刷新时间。UI证据有效期5分钟；预检不会替操作者证明这些输入真实。

## 判断与授权

- `checksPassed=true`仅表示调用者提供的数据一致，不证明真实网页已验收、成片合格或允许付款；结果固定authorizesSubmission=false并标明evidenceScope。
- 缺失/重复/未声明素材、悬空占位符、手写编号、提示词未读回一致、参数不一致、旧/不完整证据、旧报价或积分换算不明均不能通过。
- `budget.status=over_budget`与needsBudgetDecision=true时，按项目预算规则询问接受本次超支或调整方案后重新报价。缩短时长只降低总价而不降低元/秒时，仍视为超支；不自动删对白、静音或更换模型。
- 程序退出码：0=输入核对通过，2=有待处理问题，1=文件/调用格式错误。仅传回必要问题与预算摘要，不重复读长篇DOM。
- 检查通过之后仍遵守用户已有逐次生成确认要求；预检不自动调用create_video_task，也不将本次维护授权当作制作付款授权。
