# 首帧附件与转场执行检查

这是已有制作登记、权威Excel和工具调用记录的**临时校验视图**，不是第二套当前台账，也不是平台请求体。不让用户手工填写工程参数。操作者从本次选版及真实工具参数导出字段，输入前检查；调用后用实际保存的请求、附件或平台读回结果再次对照。预检数据只代表拟调用输入，不能写成已提交；无法取得实际输入时保持未核对，不为通过检查伪造记录。

```text
python "<skill-dir>/scripts/validate_execution.py" first-frame-check.json --stage first_frame
python "<skill-dir>/scripts/validate_execution.py" delivery-check.json --stage delivery
```

first_frame和delivery均要求 `schema_version=1` 和非空 `shots` 数组。文件路径相对校验JSON所在目录，或为本地绝对路径；Python调用可用 `validate(data, stage="first_frame", base_dir=project_dir)`。只读本地文件，不联网、不改词、不生成素材；远端素材先通过已授权下载取得本地同版文件，否则此项保持未验证。

所有文件对象均有 `file`（非空真实文件）及 `sha256`（64位十六进制），脚本重新读取文件计算SHA256。哈希只证明字节版本，不证明它是合格图片、真实提取帧、可播放视频或有效审查证据；实际查看/回放和来源核对继续执行。记录不能仅从本次计划复制成“已观察”。

## first_frame：选版到实际附件

每个镜头有唯一非空 `shot_id`、`mode="fresh"` 或 `"continue"`。

这里的mode只是校验分类：reset、reangle、cut等需要新首帧的情形归入fresh；项目原有的continue/reangle/cut/reset衔接模式独立保留，不被此字段替换。

fresh镜头：

| 字段 | 内容 |
|---|---|
| actors | 本镜角色数组，每项 `person_id`、`variant_id`，同一人同阶段不重复；空景可为空 |
| actors.face_visible / expression_required | 严格布尔值；由剧情、景别与实际画面决定，不由脚本猜测 |
| actors.gaze / facial_cues | 脸可见时写具体视线目标，`facial_cues`对象中至少两项 `eyes`、`brows`、`mouth`非空可见线索；语义仍需人工审读 |
| actors.expression_reason | 不需要独立表情参考时必填具体理由；不能为了填空而一律写不适用 |
| references | 对象，必须明确列出 `characters / expressions / scenes / props`四个数组，没选的类别为空数组 |
| references.auxiliary | 可选数组，保存实际所需构图引导、真实状态图与编辑目标；各项role只能是 `layout / state / edit_target`，无辅助图时可省略或为空数组 |
| references各项 | `asset_id / version / role / file / sha256 / review_status`；`role`对应 `character / expression / scene / prop`，`review_status="passed"`仅来自实际审图 |
| 人物与表情参考 | 另含 `person_id / variant_id`，须对应本镜当前角色阶段；每个角色至少一个人物参考，需要表情的角色还须有同阶段表情参考 |
| inputs | 拟调用或真实请求中的附件快照数组；每项 `asset_id / version / role / file / sha256`，须与选中参考逐项一致，不能遗漏或带入未声明旧附件 |

人物/表情参考按角色阶段复用，脚本不强制每镜重建资产。不需要独立表情图时仍设计并审查开镜面部线索；`expression_required=false`不能免除表演设计。多人分别选图、标用途；工具参数中实际附图，提示词里写路径不算附件。检查报告不证明这些文件确已传入服务，调用后须对照实际记录。

reangle的真实末态、新机位布局、图像编辑的原目标等实际附件进入auxiliary，与四类主参考一起核对asset_id、version、文件/SHA、review_status及inputs完整一致。辅助图不需要person_id/variant_id，不能替人物或表情参考补位；同asset_id/version不能跨栏重复。操作者须从真实拟调用导出所有所需布局、状态和编辑目标，不得为通过检查漏登记、删掉必需附件或伪归类到scene/character；未声明的额外附件和已声明但漏传的附件都应失败。四类主参考仍须显式列出数组。

continue镜头检查 `source_clip`、`frame_index`（0-based非负整数）、`tail_frame`、`input_frame`、`receipt`文件对象：文件须真实存在且SHA匹配，输入帧与尾帧SHA必须一致。`receipt`使用现有 `frame_bridge.py extract` 生成的JSON，不新造凭据；检查其schema_version、source_sha256、image_sha256、frame_index、decoded_frame_count与本次源片/尾帧/输入和有效解码帧范围一致。源片、实际出点及提取过程继续按 [接力规则](frame-handoff.md)核实；本脚本只核对提取凭据与文件完整性，不重新解码或证明记录未伪造，不因新增检查重画首帧或强加人物/表情附件。

例如近景角色P1青年阶段正在看信并忍笑：该actor声明 `person_id="P1" / variant_id="young" / face_visible=true / expression_required=true`，视线写“信件落款”，线索写眼睑收窄、嘴角压住上提；人物和表情参考都绑定P1/young，并将选中的两份文件与版本实际带入工具附件。若表情参考漏传、换成年长阶段或文件被覆盖，检查失败。

## 首帧四项人工审查 机械核对之后仍须完成

此处补充现有制作登记的实际审查内容，不改变上方schema_version=1的脚本接口。validate_execution.py只检查已声明角色阶段绑定、附件集合和本地文件指纹；它不能从像素判断年龄、表情格位、知识内容或纸面方向，也不能自动发现操作者漏报的强参考物品。不得把脚本退出0当成下面四项已经通过。

| 检查项 | 必须留下的实际依据 | 不能通过的情况 |
|---|---|---|
| 人物阶段与状态 | 镜号、剧本时期／年龄阶段／衣装／当前活动及身体状态、对应人物资产版本、实际输入、与原图及首帧的逐人对照 | 同一个泛称old标签套不同年龄；只传表情或旧人物首帧，没有正确阶段身份资产；附图存在但脸与衣装已漂移 |
| 表演与必要表情参考 | 具体神态、幅度与眼线；确需独立参考时记录person_id＋variant_id、所选图版本及实际审图。仅完整标准图集任务再检查九格顺序、喜怒哀乐、slot_id及派生关系 | 用露脸或空参考栏强制补图；未核对身份／阶段；需要完整图集却用散图冒充；把参考图集画成多人 |
| 强参考物品 | 镜中书／文字／地图等逐项盘点、prop_id版本、独立原件／内容图、来源及完整物品词、实际附件 | props为空但镜内存在关键地图；只拿空景或AI整镜当内容依据；物品图存在却未实际传入 |
| 入画内容与读向 | 内容图核对结果，实际读者／展示对象、页顶底／装订、地图四向及至少两处非对称锚点在画面中的位置、输出指纹和实际看图结论 | 对镜头正立却让读者倒读；把水平镜像说成纸面180度旋转；只看一个地标或只验OCR；审图遗漏而写通过 |

实际调用前核对资产齐全与职责；调用后核对真实附件并逐张审查像素。记录“缺资产／已生成待审／待修／审图通过／不可见未验证”，不机械补passed；对看不清的内容保留未验证。先修必要缺项，继续能独立推进的部分，沿用既有授权与有限重试，不让用户填写工程表格。

## video_prompt提交前原词检查

`python "<skill-dir>/scripts/validate_execution.py" "<prompt-check.json>" --stage video_prompt`

复用已有审批证据与真实平台读回数据生成检查记录。字段如下：

```json
{
  "schema_version": 1,
  "package_id": "PK01",
  "approval": {
    "evidence": "实际审核消息或记录的可追溯入口",
    "package_ids": ["PK01"],
    "workbook": {"file": "分镜.xlsx", "sha256": "已审核文件的64位SHA256"}
  },
  "target_node_id": "真实目标节点ID",
  "readback": {"node_id": "同一真实节点ID", "complete": true, "text": "平台实际保存的完整文本"}
}
```

例中的值只描述字段用途。审核覆盖和读回完整性必须先有真实依据，不能把发出去的文本填作读回。检查会读已审同版工作簿并按包号比对权威文本。返回blocked时按[原词执行规则](prompts.md#视频提示词以已审核excel为唯一执行来源)暂停并说明原因；matched只说明提供的文本匹配，不证明用户授权、素材已绑定或平台已核验。

## delivery：当前转场设计到实际输出

| 字段 | 内容 |
|---|---|
| shots各项 | 唯一 `shot_id`、当前选中 `clip`文件对象及其 `version` |
| planned_transitions | **独立从当前权威Excel/分镜导出的预期清单**；每项唯一 `transition_id`、`version`、`from_shot`、`to_shot`、完整 `instructions`，明确没有设计转场才为空数组 |
| transitions | 从实际制作/剪辑记录导出的执行条目，不能用它反推或缩减预期清单；ID集合必须与planned_transitions完全相等 |
| transitions各项 | `transition_id / version / from_shot / to_shot / instructions`必须与当前设计一致，另含 `execution` |
| execution | `version`为实际执行设计版本，`status="done"`仅在真实剪辑/合成执行后填写；`from_version / to_version`为实际使用的两侧选片版本，`from_sha256 / to_sha256 / output_sha256`对应两侧选片及最终输出的指纹 |
| execution.review | 实际回放记录：`status="passed"`、`output_sha256`、`evidence`文件对象；需绑定当前输出，在证据中说明实际转场区间、结果及问题 |
| output | 最终实际导出视频文件对象 |

planned_transitions与执行条目的版本、两侧镜号及完整剪辑指令分别比较，随后核对两侧当前选片的版本与指纹、执行输出与审查输出指纹。选片版本变化即使字节相同也使旧执行凭据失效；版本、指令、选片或最终导出变化后回到受影响环节制作/回放，不为旧凭据改版本/哈希冒充新执行。普通明确无特效的切镜无需造转场任务；设计了的动作/形状匹配切也须有真实选点与回放记录，`done`不意味着必须新增特效层。

例如已设计“后期闪白”，A/B生成词要求干净画面：两段视频生成成功只完成素材，不等于闪白完成。白层和切点尚未制作时，execution保持待剪辑，检查应失败；改成甩接后须同步当前设计、两侧视频/附件与Excel词、剪辑任务，并回放当前导出。没有剪辑能力时交付明确缺项，不假报完成。

## 结果与验证范围

退出码0表示本地文件和声明关系一致，报告仍为 `status="needs_visual_review"`。退出码1表示文件/附件/版本/制作证据有问题，2表示输入格式错误。所有结果固定 `authorizes_submission=false`、`visual_review="not_run"`，不能凭机械通过付款、生成或宣布表情/转场效果已验收。需修复的记录来自实际工作结果，不为消除错误机械补“passed/done”。

独立回归示例在 `scripts/test_validate_execution.py`，使用临时测试字节文件模拟指纹和依赖，覆盖漏附件、错角色阶段、表情缺失、覆盖文件、真实尾帧输入约束、待剪辑、漏转场、改版及输出替换。测试文件不是真实图片/视频，测试通过不构成实站或视觉验收。
