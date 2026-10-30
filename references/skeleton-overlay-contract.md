# 同帧骨架叠加交稿检查合同 v1

本合同固定用于 `scripts/validate_skeleton_overlay.py`。它核对冻结输入、人工检查覆盖、证据完整性和视频实际解码结构，不判断视觉真相，不生成投影或叠加，也不自动判定协议是否选够了可观察的身体链。源片看不清时如实保留 unknown/failed；不得为了通过检查虚构姿态或减少必要检查。

## 调用与坐标

```text
python <skill>/scripts/validate_skeleton_overlay.py --protocol protocol.json --report report.json --candidate frozen.blend --source-video source.mp4 --media-python <python-with-av> --blender-start 1 --source-start 10 --frame-count 3 --fps-num 30 --fps-den 1 --required-actor actorA
```

API：`validate(protocol_path, report_path, candidate, source_video, *, media_python, blender_start, source_start, frame_count, fps_num, fps_den, required_actors) -> list[str]`。路径参数可为字符串或 Path。输入只读；主程序只用标准库，只通过明确指定的 `media_python` 运行内置固定 PyAV 探测代码，不执行报告提供的命令、不加载 pickle 或 Blender 文件。

退出 0 对应 JSON `{"status":"passed","errors":[],"limitation":"..."}`；退出 1 对应 `status="incomplete"` 与非空 errors。API 将输入/格式/文件/解码错误作为字符串列表返回。CLI 的缺参数等 argparse 用法错误同样返回 incomplete/退出 1。`required_actors` 至少一个，均为唯一非空字符串。

`frame_map` 固定为下列五个整数键，必须与调用参数逐项一致；bool 不算整数。`source_start >= 0`，`blender_start >= 0`，`frame_count/fps_num/fps_den > 0`。偏移 i 从 0 到 frame_count-1：源帧为 source_start+i（0 基），Blender 帧为 blender_start+i。所有 source_frames 都写源帧，不能混用 Blender 帧。源视频和 overlay 必须使用该有理数 fps；不自动重采样或猜测时基。

```json
{"source_start":10,"blender_start":1,"frame_count":3,"fps_num":30,"fps_den":1}
```

所有帧列表必须唯一、升序、在本 job 范围内。actor_ids、actors 的 actor 键与 required_actors 完全集合一致，actor_ids 次序不具语义。

## protocol.json（字段冻结）

```json
{
  "schema_version":1,
  "protocol_type":"skeleton_overlay_v1",
  "candidate_sha256":"<frozen.blend SHA256>",
  "source_video_sha256":"<source.mp4 SHA256>",
  "frame_map":{"source_start":10,"blender_start":1,"frame_count":3,"fps_num":30,"fps_den":1},
  "actor_ids":["actorA"],
  "actors":{"actorA":{"chains":{"torso":{"source_frames":[10,11,12]}}}},
  "continuity":{"source_frames":[10,11,12]},
  "cuts":[11]
}
```

每 actor 必须至少声明一条实际可观察链，链名只能为 `torso/head/left_arm/right_arm/left_leg/right_leg`，每条链有非空 source_frames。不要用任意 technical-only checks 替代 actors/chains。可观察性由冻结协议的制定者与真实视频负责，checker 无法证明只选一条链足够。

continuity.source_frames 必须恰好覆盖整个 job，不能用单帧或稀疏采样充当连续性核验。cuts 是所有镜头切换后的第一帧，必须唯一升序，满足 source_start < cut <= source_start+frame_count-1；每个切点必须检查 cut-1 和 cut。无切点时用空列表 []，report 仍须执行 cuts 检查并提供证据，表示实际看过且未发现内部切点；job 起点不是内部切点。

## report.json（字段冻结）

下例 E 只代表通用证据对象 `{"path":"relative/or/absolute/file","sha256":"<64 lowercase hex>"}`，不是合法占位输入。所有实际 evidence 都必须是非空对象列表；相对路径均相对 report.json 所在目录解析。协议、报告与 projection JSON 都拒绝重复 JSON 键、NaN/Infinity。SHA 全部为小写 64 位十六进制。

```json
{
  "schema_version":1,
  "report_type":"skeleton_overlay_report_v1",
  "protocol_sha256":"<protocol.json SHA256>",
  "candidate_sha256":"<frozen.blend SHA256>",
  "source_video_sha256":"<source.mp4 SHA256>",
  "frame_map":{"source_start":10,"blender_start":1,"frame_count":3,"fps_num":30,"fps_den":1},
  "actor_ids":["actorA"],
  "reviewer":{"name":"实际检查人姓名","date":"2026-10-30"},
  "executed":true,
  "status":"passed",
  "actors":{"actorA":{"chains":{"torso":{
    "executed":true,"status":"passed","source_frames":[10,11,12],"evidence":[{"path":"overlay.mp4","sha256":"<overlay SHA256>"}]
  }}}},
  "continuity":{"executed":true,"status":"passed","source_frames":[10,11,12],"evidence":[{"path":"overlay.mp4","sha256":"<overlay SHA256>"}]},
  "cuts":{"executed":true,"status":"passed","source_frames":[10,11],"evidence":[{"path":"overlay.mp4","sha256":"<overlay SHA256>"}]},
  "overlay":{"kind":"same_frame_overlay","path":"overlay.mp4","sha256":"<overlay SHA256>","blend_weights":{"source":0.5,"skeleton":0.5}},
  "projection":{"kind":"evaluated_bones_and_visible_components","path":"projection.json","sha256":"<projection.json SHA256>"},
  "source_pose":{"kind":"source_pose_reference","path":"source_pose.json","sha256":"<source_pose.json SHA256>"}
}
```

report 的 actors、各 actor 的 chains 必须与 protocol 完全集合一致。每链 source_frames 必须覆盖协议要求帧，可以增加 job 内其他帧；continuity 必须恰好全 job；cuts 必须覆盖所有切点邻帧（无切点时 source_frames=[]）。所有结果（含无切点 cuts）executed 必须严格 true，status 必须严格 passed；unknown、failed、skipped、N/A、空和未执行全部拒绝。reviewer.name 非空，date 为有效 ISO 日期 YYYY-MM-DD。不能把字段存在等同真实观看。

overlay.blend_weights 仅接受 source 与 skeleton 两个有限数值（拒绝 bool），各严格大于 0 且小于 1，两者之和允许 1e-9 误差地等于 1。overlay 不能是原 source_video 本身或具有相同 SHA 的副本。两权重的声明和视频结构仍不能证明实际画面含正确骨架，换编码的纯原片/纯白模或伪造投影仍须人工观看识别。

source_pose 是源姿态的可追溯材料（如人工描点、标注帧或已核验关键点文件），必须存在且 SHA 一致；本 v1 只核其文件证据，不把来源算法或格式标签当准确率。projection.path 必须指向下述实际 JSON，不接受仅把某张图或文本改名为投影证明。

## projection.json（字段冻结）

```json
{
  "schema_version":1,
  "kind":"evaluated_bones_and_visible_components",
  "candidate_sha256":"<frozen.blend SHA256>",
  "source_video_sha256":"<source.mp4 SHA256>",
  "frame_map":{"source_start":10,"blender_start":1,"frame_count":3,"fps_num":30,"fps_den":1},
  "actor_ids":["actorA"],
  "component_ids":{"actorA":["visible_torso_component"]},
  "from_evaluated_mesh":true,
  "from_evaluated_bones":true,
  "source_frames":[10,11,12],
  "frames":[
    {"source_frame":10,"blender_frame":1,"actors":{"actorA":{"bone_segments":{"spine":[[20,20],[20,40]]},"component_points":{"visible_torso_component":[[15,20],[25,20],[20,40]]}}}},
    {"source_frame":11,"blender_frame":2,"actors":{"actorA":{"bone_segments":{"spine":[[20,20],[20,40]]},"component_points":{"visible_torso_component":[[15,20],[25,20],[20,40]]}}}},
    {"source_frame":12,"blender_frame":3,"actors":{"actorA":{"bone_segments":{"spine":[[20,20],[20,40]]},"component_points":{"visible_torso_component":[[15,20],[25,20],[20,40]]}}}}
  ]
}
```

component_ids 必须是 actor_ids 完全对应的对象，每 actor 至少一个唯一非空字符串组件 ID。两个 from_evaluated_* 必须严格 true；source_frames 必须恰好全 job。

frames 必须恰好 frame_count 项，依 job 顺序一一记录 source_frame=source_start+i 与 blender_frame=blender_start+i（严格整数，拒 bool）；每项 actors 键必须与 actor_ids 完全一致。每 actor 的 bone_segments 必须为非空对象，非空 bone_id 映射到恰好两个二维点 `[[x,y],[x,y]]`；component_points 键必须与该 actor 的 component_ids 完全一致，每组件至少三个二维点。每个 x/y 必须是有限实数，拒 bool、NaN 和 Infinity。坐标为源画幅像素平面上的投影，可处在画外，不做 0..1 或画幅边界裁剪。

只有元数据 flags、ID 或帧号而没有上述实际逐帧骨段和组件点的数据不符合合同。这里登记的是从冻结工程真实求值骨架和可见组件得到的投影来源；checker 不运行 Blender，也无法只靠数值数据证实其来源/姿态/投影真实，不新增投影算法。

## 实际媒体验证与局限

PyAV 子进程实际解码源视频至所选 job 区间及整个 overlay，不只信帧数字段。source_start 以源解码显示顺序索引；所选源帧必须够 frame_count，overlay 恰好 frame_count。overlay 每帧与所选源帧同宽高，不接受上下 stack；所选区间的尺寸不能变。两视频流 average_rate 必须等于 fps_num/fps_den。

所选源区间 PTS 必须严格单调，按实际首选帧 PTS + i/fps 对齐；overlay 首帧 PTS 必须为 0，后续严格单调且按 i/fps 对齐。帧 PTS 不可缺失；有理数时间比较允许不超过该帧 time_base 的半刻度误差，以兼容容器时基量化。源时间原点与 overlay 的 0 起点通过上述逐帧映射明确连接，不自动延长、补帧、变速或猜起点。

技术通过只表示：输入与证据字节绑定、声明检查覆盖满足冻结合同、解码画幅/帧数/fps/PTS 对齐。它不能证明协议充分、指定人物身份正确、骨架/组件来源真实、相机投影正确、叠加内容未伪造、动作/遮挡/接触视觉合格或最终交稿整体达标；仍必须由有能力的检查者真实观看同帧叠加与原片。
