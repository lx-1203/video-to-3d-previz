# 多人身份与候选接入

多人任务先登记每镜需要复刻的稳定 actor_id，再按真实源帧逐人映射检测轨迹。数值 track_id 只在当前镜头有效；同一编号跨切点不能自动认作同一演员。漏人、合框、重获和不确定身份必须记录，不能按框数宣称全员通过。

## 候选合同

`scripts/motion/multi_actor.py` 是标准库离线审计工具，接受检测 JSON（`frames` 数组），以及逐帧人工映射 JSON。每帧以 `shot_id` 与零基源 `frame` 唯一定位。通用检测包含 `detections` 数组，各项为 `track_id`、`source`（observed/filled/missing/unknown）及原始观测；也可直接包装已有试验的 window/source_zero_based/raw_detections/observed_tracks/lost_buffer 帧记录。所有原始字段保留在输出的 input 中，lost_buffer 不提升为观测。

映射文件包含全局 `actor_ids` 和 `frames`。每帧包含 `shot_id`、`frame`、`expected_actor_ids` 与 `mappings`；每条 mapping 指定 actor_id 与 track_id。缺失演员明确记录 track_id=null、source=missing。映射必须依据实际审核，不从轨迹编号、框面积或颜色猜测身份。遗漏映射、重复演员/轨迹、未知演员、缺帧和漏人会使 valid=false。

```powershell
python "<skill>/scripts/motion/multi_actor.py" --detections "<job>/detections.json" --mapping "<job>/actor_mapping.json" --output "<job>/actor_candidate.json"
```

退出0仅表示候选结构与映射合同有效；退出2表示检查失败并保留审计。`delivery_ready` 始终 false。filled/unknown 与结构有效不能表示身份真实或关节准确。不要把失败审计交给下游当通过候选；先检查退出码与 errors。

## 制作衔接

1. 检测保留每帧全部原始框、原始轨迹、来源状态和真实PTS；人工/AI身份假设与直接可见观测分开。按稳定actor_id分配独立动作候选，禁止肢体串用。
2. 动作候选接入用户指定模板的真实可见绑定，保留已有相机与场景。姿态推理的隐藏关节和未校准深度仅作软参考；此工具不运行检测、多人姿态推理或Blender重定向。
3. 身份色按actor_id固定；眼标和胸标分别读取承载底色计算互补/对立色，并检查实际渲染辨识度。遵守本任务已确认的模板拓扑、比例与本地资产隐私。
4. 人物交互与手物动作按接近、首触、保持、释放逐段审核；真实三维连接/碰撞独立检查。
5. 保存重开工程，绑定当前SHA，按真实帧/PTS与同画幅制作全员骨架叠加视频；执行既有 skeleton-overlay-contract 和 validate_skeleton_overlay.py。候选身份审计不能替代该硬门槛。

## 已知能力边界

现有本机试验覆盖286帧CPU检测跟踪与少量双人Pose锚点，存在门口合框、漏人和换号；没有完整人工身份真值，不报告IDF1或整体准确率。该证据不随公共技能发布，也不证明三片全程、多人物三维或交互通过。正式4K、原声音轨及客户打包仍依主流程独立验收。

覆盖限制：本工具只核对提供的检测帧和映射帧是否一致；若两边同时遗漏某一源帧，它无法发现。全片完整性必须另按原片解码帧数、PTS和正式骨架合同检查。valid=true也不证明身份标注真实；filled/unknown须继续审核。

## 从轨迹到官方Pose的内部调用

不要求用户逐帧填写身份。先复用实际检测/跟踪，使用`scripts/motion/actor_identity_proposal.py --detections <frames.jsonl> --anchors <anchors.json> --output <new proposal.json>`，由少量源身份锚或AI范围假设展开逐帧审计mapping。anchors包含actor_ids及anchors数组；每项为shot_id、track_id、actor_id、start_frame、end_frame（源零基闭区间）、confirmation（confirmed/hypothesis）、evidence。换号无锚、范围冲突与切镜不自动继承身份；hypothesis保留假设，不能升级确认。输出保留全原始观测、完整audit_candidate及confirmed observed裁剪crop_candidate子集；子集有效不能掩盖required角色missing。

角色在镜(required_actor_ids)、可用裁剪框、source_visibility/partial_observation是三种独立事实。画外躯干而手臂可见不需造全人框，也不代表肢体不可审核。全员覆盖未通过不妨碍已确认子集的受限姿态候选，但正式交付仍须完整规定覆盖。

将proposal.crop_candidate单独写为JSON，配合实际K和本地后端配置调用`scripts/motion/multi_actor_pose.py plan --candidate <candidate.json> --source <video> --source-sha256 <SHA> --intrinsics <K.json> --python <GEM_python> --repo <GEM_repo> --checkpoint <weights> --soma-assets <assets> --device cpu --work <new_pose_job>`。plan只做文件/合同检查，绑定源SHA、官方代码/权重/资产、actor、源帧、PTS及time_base。检测支持box/box_xyxy/raw_box_xyxy或官方square_bbox_xys/bbox，后一格式为cx,cy,size；`--official-boxes`可在语义一致时保留旧官方文件原字节。unknown/filled裁剪仅在显式`--allow-inferred`时可推理，仍不变成关节真值。

`execute --plan <plan.json>`实际调用官方gem.inference pose-video，不经shell；会加载Torch/模型，按本机协作资源规则安排后执行，不因plan成功自动启动。记录退出码、日志、结果SHA及前后输入一致性，成功回执与未改结果才复用；旧失败/未绑定结果拒绝复用。framewise Pose是逐帧独立裁剪，不是多人联合优化、时序Motion或接触求解。

已有结果使用`import-result --plan <plan.json> --manifest <binding.json>`：manifest须含fingerprint、与plan完全相同的bindings、result路径/SHA、data_scope及可选evidence_sha256。它只校验不透明结果字节与调用方来源声明，不反序列化PT、不制造一次新推理成功，也不证明完整77关节或动作正确。结果引用包含逐actor绑定与共享PT路径；未实现逐actor动作文件导出时不可声称已输出独立动画。

当前接口为partial：已有真实多人跟踪及双人Pose缓存可接入，通用视频自动检测入口、完整时序动作和指定模板可见动画还需任务内实际验证。不得把假后端调度测试、结构通过或数值导出当成真实推理/画面验收。

## 指定模板语义适配

`scripts/motion/pose_semantic_profile.py`的`build_semantic_profile(candidates, profile)`接受已解码数值候选（joint_names与逐actor/源帧/PTS rows）和本机私有profile。profile含source_sha256、target_template_sha256，以及actors中mesh、rig、chains。每链明确source_start/source_end正式关节名、target_component、target_control、side_confirmation（confirmed与证据）、pivot_calibration（calibrated与证据）。缺少真实侧别或枢轴校准即blocked，不猜模板控制骨。输出端点、camera方向与独立可见性，投影有效不代表源可见；camera坐标不当多人物共同世界坐标。

LeftShoulder是锁骨/肩带、LeftArm是上臂根、LeftForeArm肘、LeftHand腕；甲方掌端或可见组件端面必须独立标定。该接口不执行Blender/IK或生成动画，ready_for_semantic_adaptation也不代表模板已驱动。真正正例由单片负责人在真实保存重开工程证明可见身体受驱动，再通过三维接触和同帧骨架审核。profile与客户资产留本机，公共包不带私有映射。

## 新视频检测入口

本机后端已配置后，调用`scripts/motion/multi_actor_detect.py --source <video> --source-sha256 <SHA> --repo <GEM_repo> --model <YOLOX.onnx> --model-sha256 <SHA> --shots <shots.json> --output-new-dir <new_dir> --threads2`。shots是数组，每项shot_id/source_zero_start/source_zero_end，源零基闭区间、不重叠。`--plan-only`只输出计划、不加载模型/不写目录；正式执行懒加载PyAV/NumPy/ONNXRuntime等，CPU provider与2线程受控，不下载、不导入Torch。

frames.jsonl保留每个请求帧真实PTS/time_base、全部官方detect()返回框（已受官方阈值/NMS，不是预阈值网络logits）、观测/lost/生命周期。receipt绑定源/模型/实际入口与官方代码/输出SHA及实际帧数，切镜重置轨迹。它不自动识别演员身份；再用源图/少量身份锚生成提案。单框可能是其它角色画外，不直接叫漏人；多框也不直接证明人数正确。逐角色在场、可检裁剪、可见链各自记录。

## 可执行的模板臂方向采样桥

在已有真实模板工程副本上运行`Blender --background --factory-startup --disable-autoexec --threads 2 <source.blend> --python-exit-code 1 --python <skill>/scripts/motion/blender_pose_prior.py -- --candidates <numeric77.json> --profile <private_profile.json> --output <NEW.blend> --samples <Blender帧...>`。不可自动执行附件脚本，不覆盖原源。

profile显式绑定source_sha256、expected_blend_sha256、target_template_path/SHA、frame_offset、每actor的mesh/rig，以及每侧upper/lower的控制骨、组件vertex_indices、proximal/distal端面索引、正式源链和侧别/pivot证据。必须来自实际模板几何标定，不能猜旧rig pivot。仅对候选已有且显式请求的采样帧传递相机单位方向，固定当前真实组件尺寸、不拷源深度；复制修改动作且保留原驱动。运行核验真实求值网格/端面/长度与非臂不变，旁写内部候选receipt；背景modifier允许改变原始拓扑，但应用前后求值拓扑必须一致。所有结果均非交稿。

保存后另进程重开实际输出，核可见网格受驱动、非目标部分/相机/绑定/身份材质/眼胸分区；需要辨识度和互补对比时还须检查实际引擎承载色及渲染。仅保留17标记面或材质一致不证明对比可读，也不证明源动作拟合或接触正确。桥的receipt不冒称独立重开；独立记录绑定输出SHA。采样通过不证明中间帧动作，正式门槛仍不变。
