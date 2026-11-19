"""Blender controls for scene blockout and the Auto Box workflow."""
import json
import os
from pathlib import Path
import subprocess

import bpy
from bpy.props import EnumProperty, FloatVectorProperty, IntProperty, StringProperty

from . import core, local_pipeline, motion, multiactor, scene_tools
from .ui import _absolute, _native_popen, _resolve_executable, preferences


_processes = {}
_local_processes = {}


class OrderFlowSceneTools(bpy.types.PropertyGroup):
    kind: EnumProperty(name='白模部件', items=[('FLOOR', '地面', ''), ('WALL', '墙体', ''),
                                                ('OPENING', '带门洞墙体', ''), ('BOX', '道具方块', '')], default='WALL')
    dimensions: FloatVectorProperty(name='宽 / 深 / 高', size=3, default=(4, 0.2, 3), min=0.001, unit='LENGTH')
    opening: FloatVectorProperty(name='净宽 / 净高', size=2, default=(1, 2), min=0.001, unit='LENGTH')
    asset: StringProperty(name='本地资产 .blend', subtype='FILE_PATH')
    collection: StringProperty(name='集合名称')
    message: StringProperty(name='建模状态')


class OrderFlowAutoBox(bpy.types.PropertyGroup):
    backend: StringProperty(name='Auto Box 后端 JSON', subtype='FILE_PATH')
    job: StringProperty(name='多人任务目录', subtype='DIR_PATH')
    shot: StringProperty(name='镜头 ID', default='s1')
    first: IntProperty(name='原片首帧（零基）', min=0)
    last: IntProperty(name='原片尾帧（零基）', min=0, default=89)
    actor: StringProperty(name='演员 ID')
    track: IntProperty(name='轨迹 ID', min=0)
    anchor_first: IntProperty(name='身份首帧', min=0)
    anchor_last: IntProperty(name='身份尾帧', min=0, default=89)
    evidence: StringProperty(name='看图确认依据')
    review_frames: StringProperty(name='审核帧（英文逗号）')
    message: StringProperty(name='多人任务状态', default='先设置后端和镜头范围，再建立多人任务。')


class OrderFlowLocalPipeline(bpy.types.PropertyGroup):
    mode: EnumProperty(name='流水线', items=[('scene', '场景估计与白模', ''),
                                                ('people-scene', '场景与人物合成', '')])
    python: StringProperty(name='流水线 Python', subtype='FILE_PATH')
    runner: StringProperty(name='本机 pipeline_runner.py', subtype='FILE_PATH')
    runtime: StringProperty(name='场景运行配置', subtype='FILE_PATH')
    profile: StringProperty(name='本地角色配置', subtype='FILE_PATH')
    anchors: StringProperty(name='身份锚 JSON', subtype='FILE_PATH')
    binding: EnumProperty(name='角色绑定', items=[('translation-adapter', '平移适配', ''),
                                                  ('rigid-components', '刚性部件', '')])
    output: StringProperty(name='本地任务根目录', subtype='DIR_PATH')
    first: IntProperty(name='原片首帧（零基）', min=0)
    last: IntProperty(name='原片尾帧（零基）', min=0, default=89)
    job: StringProperty(name='本地候选任务', subtype='DIR_PATH')
    message: StringProperty(name='流水线状态', default='选择已配置的本地计算环境。')


class ORDERFLOW_OT_scene_tool(bpy.types.Operator):
    bl_idname = 'orderflow.scene_tool'
    bl_label = '白模搭建与本地资产'
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    action: EnumProperty(items=[('blockout', '搭建', ''), ('scan', '读取集合', ''), ('append', '追加资产', '')])

    def execute(self, context):
        scene = context.scene
        tools = scene.orderflow_scene_tools
        try:
            if self.action == 'blockout':
                result = scene_tools.add_blockout(scene, tools.kind, tuple(tools.dimensions), tuple(tools.opening))
                tools.message = f'已在 3D 游标处创建 {result.name}。'
            elif self.action == 'scan':
                names = scene_tools.local_collection(scene, _absolute(tools.asset))
                if len(names) == 1:
                    tools.collection = names[0]
                tools.message = '可选集合：' + ('、'.join(names) or '无')
            else:
                result = scene_tools.local_collection(scene, _absolute(tools.asset), tools.collection.strip())
                tools.message = f'已追加本地集合 {result.name}。'
            self.report({'INFO'}, tools.message)
        except (OSError, RuntimeError, ValueError, KeyError) as exc:
            tools.message = str(exc)
            self.report({'ERROR'}, tools.message)
            return {'CANCELLED'}
        return {'FINISHED'}


def refresh(scene):
    local = scene.orderflow_local_pipeline
    process = _local_processes.get(local.job)
    if process and process.poll() is not None:
        _local_processes.pop(local.job, None)
        if process.returncode == 0 and (Path(local.job) / 'run_report.json').is_file():
            local.message = '计算已结束，可以核对候选工程。'
        else:
            local.message = '流水线退出，请查看任务报告或根目录中的同名日志。'
    if not hasattr(scene, 'orderflow_auto_box'):
        return
    settings = scene.orderflow_auto_box
    if not settings.job:
        return
    job = Path(_absolute(settings.job))
    process = _processes.get(str(job))
    status_file = job / 'box_status.json'
    if status_file.is_file():
        status = core.read_json(status_file)
        settings.message = status['message']
    if process and process.poll() is not None:
        _processes.pop(str(job), None)
        if process.returncode and (not status_file.is_file() or status.get('state') != 'failed'):
            settings.message = '后台异常退出，请查看任务目录中的 box.log。'


def _queue(job, spec, stage, data):
    process = _processes.get(str(job))
    if (process and process.poll() is None) or (job / 'box.lock').exists():
        raise ValueError('该任务仍在运行。')
    interpreter = Path(spec['backend'].get('python', ''))
    if not interpreter.is_file():
        raise ValueError('Auto Box 后端缺少有效的 Python。')
    env = motion._env()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get('PYTHONPATH', '')
    command = [str(interpreter), '-u', '-m', 'orderflow.multiactor', str(job), stage,
               '--data=' + json.dumps(data, ensure_ascii=True)]
    with (job / 'box.log').open('ab') as log:
        _processes[str(job)] = _native_popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, cwd=job)


class ORDERFLOW_OT_auto_box(bpy.types.Operator):
    bl_idname = 'orderflow.auto_box'
    bl_label = '多人骨架'
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}
    action: EnumProperty(items=[(key, value, '') for key, value in
                                dict(multiactor.LABELS, new='新建任务', folder='打开任务目录',
                                     review_folder='打开审核目录', reference='导入参考骨架').items()])

    def execute(self, context):
        project = context.scene.orderflow
        settings = context.scene.orderflow_auto_box
        try:
            reference = _absolute(project.reference_video)
            if self.action == 'new':
                if not project.output_root:
                    raise ValueError('请指定订单输出目录。')
                prefs = preferences(context)
                backend = multiactor.backend_config(_absolute(settings.backend), _resolve_executable(prefs.media_python),
                                                     _resolve_executable(prefs.ffmpeg))
                settings.job = str(multiactor.create_job(
                    _absolute(project.output_root), 'auto-box-' + settings.shot.strip(), reference, settings.shot.strip(),
                    settings.first, settings.last, backend, _absolute(prefs.motion_update_script)))
                settings.message = '任务已创建，下一步运行多人检测。'
                return {'FINISHED'}
            folder, spec, records = multiactor.read_job(_absolute(settings.job))
            if self.action in ('folder', 'review_folder'):
                if self.action == 'review_folder':
                    key = 'skeleton' if 'skeleton' in records else 'review'
                    folder = multiactor.artifact(folder, records, key).parent
                bpy.ops.wm.path_open(filepath=str(folder))
            elif self.action == 'reference':
                collection, count = multiactor.import_skeleton(context.scene, folder, reference, project.source_start_seconds)
                settings.message = f'已导入 {count} 个角色帧到 {collection.name}（候选参考）。'
            else:
                if not reference or Path(reference).resolve() != Path(spec['reference']).resolve():
                    raise ValueError('所选多人任务对应另一份参考视频。')
                data = dict(actor=settings.actor, track=settings.track, first=settings.anchor_first,
                            last=settings.anchor_last, evidence=settings.evidence, frames=settings.review_frames)
                _queue(folder, spec, self.action, data)
                settings.message = '已提交后台：' + multiactor.LABELS[self.action]
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            settings.message = str(exc)
            self.report({'ERROR'}, settings.message)
            return {'CANCELLED'}
        return {'FINISHED'}


class ORDERFLOW_OT_local_pipeline(bpy.types.Operator):
    bl_idname = 'orderflow.local_pipeline'
    bl_label = '本地白模计算流水线'
    bl_options = {'INTERNAL'}
    action: EnumProperty(items=[('run', '开始计算', ''), ('candidate', '核对候选', ''), ('folder', '任务目录', '')])

    def execute(self, context):
        scene = context.scene
        settings = scene.orderflow_local_pipeline
        reference = _absolute(scene.orderflow.reference_video)
        try:
            if self.action == 'run':
                active = _local_processes.get(settings.job)
                if active and active.poll() is None:
                    raise ValueError('当前流水线还在计算。')
                job, args = local_pipeline.launch_spec(
                    mode=settings.mode, interpreter=_absolute(settings.python), runner=_absolute(settings.runner),
                    output=_absolute(settings.output), source=reference, runtime=_absolute(settings.runtime),
                    first=settings.first, last=settings.last, profile=_absolute(settings.profile),
                    anchors=_absolute(settings.anchors), binding=settings.binding)
                with job.with_suffix('.log').open('xb') as log:
                    _local_processes[str(job)] = _native_popen(args, stdout=log, stderr=subprocess.STDOUT,
                                                               env=motion._env(), cwd=Path(args[2]).parent)
                settings.job = str(job)
                settings.message = '本地流水线已在后台启动。'
            elif not settings.job:
                raise ValueError('请选择本地候选任务。')
            elif self.action == 'candidate':
                path = local_pipeline.candidate(_absolute(settings.job), reference)
                scene.orderflow_scene_tools.asset = str(path)
                scene.orderflow_scene_tools.collection = ''
                settings.message = '候选 SHA 已核对，可在场景与模型区查看和追加集合。'
            else:
                job = Path(_absolute(settings.job))
                bpy.ops.wm.path_open(filepath=str(job if job.is_dir() else job.parent))
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            settings.message = str(exc)
            self.report({'ERROR'}, settings.message)
            return {'CANCELLED'}
        return {'FINISHED'}


class ORDERFLOW_PT_scene_tools(bpy.types.Panel):
    bl_label = '场景与模型'
    bl_idname = 'ORDERFLOW_PT_scene_tools'
    bl_parent_id = 'ORDERFLOW_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '接单助手'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout, settings = self.layout, context.scene.orderflow_scene_tools
        layout.prop(settings, 'kind')
        layout.prop(settings, 'dimensions')
        if settings.kind == 'OPENING':
            layout.prop(settings, 'opening')
        layout.operator('orderflow.scene_tool', text='在游标处搭建白模').action = 'blockout'
        layout.prop(settings, 'asset')
        row = layout.row(align=True)
        row.prop(settings, 'collection')
        row.operator('orderflow.scene_tool', text='读取集合').action = 'scan'
        layout.operator('orderflow.scene_tool', text='追加本地集合').action = 'append'
        if settings.message:
            layout.label(text=settings.message)


class ORDERFLOW_PT_auto_box(bpy.types.Panel):
    bl_label = '多人骨架与身份校正'
    bl_idname = 'ORDERFLOW_PT_auto_box'
    bl_parent_id = 'ORDERFLOW_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '接单助手'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout, settings = self.layout, context.scene.orderflow_auto_box
        layout.prop(settings, 'backend')
        layout.prop(settings, 'shot')
        row = layout.row(align=True)
        row.prop(settings, 'first')
        row.prop(settings, 'last')
        layout.operator('orderflow.auto_box', text='新建多人任务').action = 'new'
        layout.prop(settings, 'job')
        row = layout.row(align=True)
        row.operator('orderflow.auto_box', text='检测').action = 'detect'
        row.operator('orderflow.auto_box', text='生成审核图').action = 'review'
        row.operator('orderflow.auto_box', text='查看审核图').action = 'review_folder'
        box = layout.box()
        row = box.row(align=True)
        row.prop(settings, 'actor')
        row.prop(settings, 'track')
        row = box.row(align=True)
        row.prop(settings, 'anchor_first')
        row.prop(settings, 'anchor_last')
        box.prop(settings, 'evidence')
        row = box.row(align=True)
        row.operator('orderflow.auto_box', text='确认身份锚').action = 'set'
        row.operator('orderflow.auto_box', text='撤销区间').action = 'clear'
        row = layout.row(align=True)
        row.operator('orderflow.auto_box', text='推理姿态').action = 'pose'
        row.operator('orderflow.auto_box', text='按新锚点补算').action = 'correct'
        layout.prop(settings, 'review_frames')
        row = layout.row(align=True)
        row.operator('orderflow.auto_box', text='画原片骨架').action = 'skeleton'
        row.operator('orderflow.auto_box', text='导入参考骨架').action = 'reference'
        layout.operator('orderflow.auto_box', text='打开任务目录').action = 'folder'
        layout.label(text=settings.message)


class ORDERFLOW_PT_local_pipeline(bpy.types.Panel):
    bl_label = '本地自动白模流水线'
    bl_idname = 'ORDERFLOW_PT_local_pipeline'
    bl_parent_id = 'ORDERFLOW_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '接单助手'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout, settings = self.layout, context.scene.orderflow_local_pipeline
        for field in ('mode', 'python', 'runner', 'runtime', 'output'):
            layout.prop(settings, field)
        row = layout.row(align=True)
        row.prop(settings, 'first')
        row.prop(settings, 'last')
        if settings.mode == 'people-scene':
            for field in ('profile', 'anchors', 'binding'):
                layout.prop(settings, field)
        layout.operator('orderflow.local_pipeline', text='后台计算').action = 'run'
        layout.prop(settings, 'job')
        row = layout.row(align=True)
        row.operator('orderflow.local_pipeline', text='核对候选工程').action = 'candidate'
        row.operator('orderflow.local_pipeline', text='打开任务目录').action = 'folder'
        layout.label(text=settings.message)


_CLASSES = (OrderFlowSceneTools, OrderFlowAutoBox, OrderFlowLocalPipeline, ORDERFLOW_OT_scene_tool,
            ORDERFLOW_OT_auto_box, ORDERFLOW_OT_local_pipeline, ORDERFLOW_PT_scene_tools,
            ORDERFLOW_PT_auto_box, ORDERFLOW_PT_local_pipeline)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.orderflow_scene_tools = bpy.props.PointerProperty(type=OrderFlowSceneTools)
    bpy.types.Scene.orderflow_auto_box = bpy.props.PointerProperty(type=OrderFlowAutoBox)
    bpy.types.Scene.orderflow_local_pipeline = bpy.props.PointerProperty(type=OrderFlowLocalPipeline)


def unregister():
    del bpy.types.Scene.orderflow_local_pipeline
    del bpy.types.Scene.orderflow_auto_box
    del bpy.types.Scene.orderflow_scene_tools
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
