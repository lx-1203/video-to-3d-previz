# 实施接口（root整合）

包名orderflow，安装ZIP顶层orderflow/。Python标准库逻辑放core.py；Blender相关放ui.py/worker.py；媒体依赖只在media.py子进程导入。

## core.py（导出实现者拥有）

MODE_SETTINGS = {"preview": {"width":1280,"height":720}, "final": {"width":3840,"height":2160}}。

validate_spec(spec:dict) -> dict：返回规范化副本，缺字段/越界抛ValueError。字段schema_version=1, name, source_blend, snapshot_blend, reference_video, output_root, frame_start, frame_end, scene_frame_start, fps_num, fps_den, source_start_seconds, mode, camera, ffmpeg, media_python, model_standard（可空）。frame_start>=scene_frame_start>=1；fps>0；reference/source/snapshot均真实文件；宽高由mode决定；不接受frame_end<frame_start或源起点<0。

new_job_dir(output_root, name) -> pathlib.Path：新UUID目录。write_job(job_dir,spec) -> Path(job.json)：绑定所有源SHA。load_job(path) -> dict。write_status(job_dir, state, **details)原子写status.json；状态必须枚举。read_status(job_dir)->dict。

blender_worker_command(blender, job_path)->list：命令含--background --factory-startup --disable-autoexec --python-exit-code 1 --python orderflow/worker.py -- --job job.json。

inspect_png_frames(frames_dir,start,end,width,height)->list[dict]：仅预期frame_000001.png等，逐帧尺寸及SHA，缺坏帧抛错；附name/frame/sha256/path。

package_submission(job_dir, acceptance=None)->Path：正式客户ZIP须final技术验证及同帧骨骼叠加统一检查器通过；缺配置或未审核拒绝。acceptance为UI显式协议/报告/required_actors/checker/更新器路径，预期帧映射来自冻结job，不信报告自报。仅收集scene.blend、依赖、两影片、说明，可选明确选定的规范；内部QA、叠加证据和交付清单留在job外部记录。ZIP不覆盖，按实际路径/SHA/CRC验证后返回。视觉字段原样保留，不把unverified变passed。

## worker.py（导出实现者拥有）

Blender CLI执行--job；打开快照，迁移实际依赖保存scene.blend、冻结SHA；逐帧原生render；保存render_manifest.json；subprocess调用[spec.media_python, media.py, --job, job_path]。设置并维护status；失败非零退出，cancel保留真实状态。

## media.py（导出实现者拥有）

CLI --job；读job/manifest，检查输入一致性。两影片white.mp4/comparison.mp4，verification.json报告含technical_passed、visual_status=unverified、checks、source_sha256、file_sha256。技术通过后status complete，不额外解释为用户视觉通过。外部FFmpeg日志保留。输出编码只针对本job。

## ui.py/__init__.py（UI实现者拥有）

bl_info name="接单助手 / OrderFlow",version=(0,1,0),blender=(4,2,0),category="Import-Export"。__init__.register/unregister委托ui；import orderflow不在非BlenderPython立即导入bpy。

OrderFlowSettings(Scene.orderflow)：name、reference_video、output_root、frame_start/frame_end、source_start_seconds、camera_name、model_standard、motion_job。偏好(OrderFlowPreferences)：ffmpeg、media_python、motion_cli、motion_update_script。

operators：orderflow.preflight / preview / export_final / cancel / refresh / open_output / package / motion_status / motion_next / import_motion_reference。Panel VIEW_3D UI tab="接单助手"。所有需要长执行的操作Popen后台、不等待render；timer读status刷新。只通过argv启动，不shell=True。

新job由UI new_job_dir→save_as_mainfile(snapshot.blend,copy=True,relative_remap=True)→write_job→Popen。原filepath、相机变换、材质验证保持；源未保存时明确阻止并提示先保存。运行job路径是Scene字符串属性active_job_dir。取消只写该job cancel.request。

动作桥在motion.py（UI调用固定入口）：motion_status(settings,prefs)->dict，motion_next(settings,prefs)->dict，import_reference(context,jobdir)->dict。前两项后台JSON命令通过Popen wrapper。import_reference只追加job登记candidate.blend中profile明确的rig/mesh，动作/sidecar/provenance及输入SHA有效；一次新鲜在线status结果是同次调用的导入准备，使用后消费。独立集合hide_render，不给现有角色赋动作，保持场景相机/颜色。导出操作只读当前实际动作。

UI不修改core/worker/media；导出实现者不改ui/__init__。测试文件也分别拥有。接口冲突先发root，不各自重命名。
