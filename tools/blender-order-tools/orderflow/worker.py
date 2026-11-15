"""Background Blender entry point. Only this job's snapshot is edited."""
import argparse
from fractions import Fraction
from pathlib import Path
import shutil
import sys
import traceback

if __package__:
    from . import core, media
else:
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    from orderflow import core, media


def _visual_signature(bpy):
    # Capture structural values rather than IDs, which legitimately change on reopen.
    return {
        'objects': {obj.name: {'matrix': [list(row) for row in obj.matrix_world], 'type':obj.type,
                              'data':obj.data.name if obj.data else None,
                              'materials':[slot.material.name if slot.material else None for slot in obj.material_slots]}
                    for obj in bpy.data.objects},
        'meshes': {mesh.name: {'vertices':[list(vertex.co) for vertex in mesh.vertices],
                              'polygons':[list(face.vertices) for face in mesh.polygons]}
                   for mesh in bpy.data.meshes},
        'materials': {mat.name: list(mat.diffuse_color) for mat in bpy.data.materials},
        'cameras': {cam.name: {'lens':cam.lens,'sensor_width':cam.sensor_width,'type':cam.type,'ortho_scale':cam.ortho_scale}
                    for cam in bpy.data.cameras},
    }


def unsupported_compositor_outputs(scene):
    """Inspect only the render scene's enabled compositor, including groups.

    File Output nodes are conservatively rejected even when muted or unlinked:
    their side effects must never escape this job's explicit output paths.
    """
    if not getattr(scene.render, 'use_compositing', True):
        return []
    tree = getattr(scene, 'compositing_node_group', None)
    if tree is None and getattr(scene, 'use_nodes', False):
        tree = getattr(scene, 'node_tree', None)
    problems, visited = [], set()

    def inspect(group):
        if group is None:
            return
        identity = group.as_pointer() if hasattr(group, 'as_pointer') else id(group)
        if identity in visited:
            return
        visited.add(identity)
        for node in group.nodes:
            if node.type == 'OUTPUT_FILE':
                problems.append(f'{scene.name} / {group.name} / {node.name}：暂不支持合成器 File Output 文件写出节点，请在导出前移除或关闭场景合成。')
            if node.type == 'GROUP':
                inspect(getattr(node, 'node_tree', None))

    inspect(tree)
    return problems


def unsupported_simulations(bpy):
    """Conservative first-release policy: these simulations are not portable yet."""
    problems = []
    types = {'CLOTH', 'SOFT_BODY', 'FLUID', 'PARTICLE_SYSTEM', 'DYNAMIC_PAINT', 'OCEAN'}
    for obj in bpy.data.objects:
        for modifier in obj.modifiers:
            if modifier.type in types and modifier.show_render:
                problems.append(f'{obj.name} / {modifier.name}：暂不支持模拟缓存（{modifier.type}），请先转换为不依赖模拟缓存的工程。')
        # Particle point caches may exist even when their UI modifier is absent.
        for system in obj.particle_systems:
            if system.point_cache is not None and not any(m.type == 'PARTICLE_SYSTEM' for m in obj.modifiers):
                problems.append(f'{obj.name} / {system.name}：暂不支持粒子模拟缓存。')
    for scene in bpy.data.scenes:
        world = scene.rigidbody_world
        if world and getattr(world, 'enabled', True):
            problems.append(f'{scene.name}：暂不支持刚体模拟缓存。')
    return problems


def migrate_dependencies(bpy, job_dir):
    simulations = unsupported_simulations(bpy)
    if simulations:
        raise ValueError('；'.join(simulations))
    dependencies={}
    migration=[]
    if len(bpy.data.libraries):
        raise ValueError('Linked Blender libraries need a local, self-contained source before export; no library is silently rewritten')

    def copy_file(source, *, group=None):
        source=Path(source).resolve()
        if not source.is_file(): raise ValueError(f'Missing external dependency: {source}')
        digest=core.sha256(source)
        destination=job_dir/'dependencies'/(group or digest[:16])/source.name
        destination.parent.mkdir(parents=True,exist_ok=True)
        if destination.exists():
            if core.sha256(destination)!=digest: raise ValueError('Dependency name collision')
        else: shutil.copy2(source,destination)
        relative=destination.relative_to(job_dir).as_posix()
        dependencies[relative]={'path':relative,'sha256':digest}
        return destination

    def migrate(block):
        value=block.filepath
        if not value or value=='<builtin>' or getattr(block,'packed_file',None) or getattr(block,'packed_files',None): return
        if getattr(block,'source','') in ('GENERATED','VIEWER'): return
        source=Path(bpy.path.abspath(value,library=block.library)).resolve()
        if getattr(block,'source','') in ('SEQUENCE','TILED'):
            # Preserve all actual siblings of numbered sequences/UDIMs, without renumbering.
            import re
            pattern=re.sub(r'\d+(?=\.[^.]+$)', '*', source.name).replace('<UDIM>','*')
            files=sorted(source.parent.glob(pattern))
            if not files: raise ValueError(f'Missing image sequence: {source}')
            group=__import__('hashlib').sha256(str(source).encode()).hexdigest()[:16]
            for path in files: copy_file(path,group=group)
            destination=job_dir/'dependencies'/group/source.name
        else: destination=copy_file(source)
        block.filepath='//'+destination.relative_to(job_dir).as_posix()
        migration.append({'kind':block.bl_rna.identifier,'name':block.name,'source':str(source),'destination':block.filepath})

    for collection in (bpy.data.images,bpy.data.sounds,bpy.data.fonts,bpy.data.movieclips,bpy.data.cache_files,bpy.data.volumes):
        for block in collection:
            if hasattr(block,'filepath'): migrate(block)
    for scene in bpy.data.scenes:
        editor=scene.sequence_editor
        if not editor: continue
        strips=getattr(editor,'strips_all',getattr(editor,'sequences_all',[]))
        for strip in strips:
            if strip.type=='IMAGE':
                directory=Path(bpy.path.abspath(strip.directory)).resolve()
                group=__import__('hashlib').sha256(str(directory).encode()).hexdigest()[:16]
                for element in strip.elements: copy_file(directory/element.filename,group=group)
                strip.directory='//dependencies/'+group+'/'
            elif strip.type=='MOVIE':
                destination=copy_file(bpy.path.abspath(strip.filepath))
                strip.filepath='//'+destination.relative_to(job_dir).as_posix()
    core.atomic_json(job_dir/'dependency_migration.json',{'nonvisual_path_migration':migration,'dependencies':list(dependencies.values())})
    return list(dependencies.values())


def run_job(job_path):
    import bpy
    job_path=Path(job_path).resolve()
    job_dir=job_path.parent
    spec=core.load_job(job_path)
    media.validate_inputs(spec,job_dir)
    media.check_cancel(job_dir)
    for name in ('scene.blend','render_manifest.json','frames'):
        if (job_dir/name).exists(): raise FileExistsError(f'Job has already started: {name}')
    core.write_status(job_dir, 'queued', message='Checking media Python and FFmpeg before rendering', visual_status='unverified')
    media.run_process([spec['media_python'], str(Path(__file__).with_name('media.py')), '--check-env', '--job', str(job_path)], job_dir/'environment_check.log', job_dir)
    core.write_status(job_dir,'rendering',message='Opening frozen snapshot',visual_status='unverified',mode=spec['mode'])
    bpy.ops.wm.open_mainfile(filepath=spec['snapshot_blend'],load_ui=False,use_scripts=False)
    scene=bpy.context.scene
    compositor_errors = unsupported_compositor_outputs(scene)
    if compositor_errors:
        raise ValueError('；'.join(compositor_errors))
    camera=bpy.data.objects.get(spec['camera'])
    if camera is None or camera.type!='CAMERA' or camera.name not in scene.objects: raise ValueError('Requested camera is not in the render scene')
    if scene.camera!=camera: raise ValueError('Requested camera differs from the frozen active camera; choose it before saving the snapshot')
    actual_fps=Fraction(scene.render.fps,1)/Fraction(str(scene.render.fps_base))
    expected_fps=Fraction(spec['fps_num'],spec['fps_den'])
    if abs(float(actual_fps-expected_fps))>1e-5: raise ValueError('Scene fps differs from the frozen job')
    signature=_visual_signature(bpy)
    render_engine=scene.render.engine
    material_mode=scene.display.shading.color_type
    dependencies=migrate_dependencies(bpy,job_dir)
    # Output parameters only. Preserve engine, shading/material modes, transforms and colors.
    scene.render.resolution_x=spec['width']
    scene.render.resolution_y=spec['height']
    scene.render.resolution_percentage=100
    if hasattr(scene.render.image_settings, 'media_type'):
        scene.render.image_settings.media_type='IMAGE'
    scene.render.image_settings.file_format='PNG'
    scene.render.image_settings.color_mode='RGBA'
    scene.render.image_settings.color_depth='8'
    scene.render.use_file_extension=True
    scene.frame_start=spec['frame_start']
    scene.frame_end=spec['frame_end']
    scene_path=job_dir/'scene.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_path),relative_remap=False)
    bpy.ops.wm.open_mainfile(filepath=str(scene_path),load_ui=False,use_scripts=False)
    scene=bpy.context.scene
    if _visual_signature(bpy)!=signature or scene.render.engine!=render_engine or scene.display.shading.color_type!=material_mode:
        raise ValueError('Saved/reopened scene changed structural visual data')
    for item in dependencies:
        if core.sha256(job_dir/item['path'])!=item['sha256']: raise ValueError('Migrated dependency hash mismatch')
    scene_sha=core.sha256(scene_path)
    frames=job_dir/'frames'
    frames.mkdir(exist_ok=False)
    count=spec['frame_end']-spec['frame_start']+1
    for index,frame in enumerate(range(spec['frame_start'],spec['frame_end']+1)):
        media.check_cancel(job_dir)
        core.write_status(job_dir,'rendering',frame=frame,completed_frames=index,total_frames=count,mode=spec['mode'],visual_status='unverified')
        scene.frame_set(frame)
        scene.render.filepath=str(frames/f'frame_{frame:06d}.png')
        bpy.ops.render.render(write_still=True)
    records=core.inspect_png_frames(frames,spec['frame_start'],spec['frame_end'],spec['width'],spec['height'])
    if core.sha256(scene_path)!=scene_sha: raise ValueError('Saved scene changed during rendering')
    core.atomic_json(job_dir/'render_manifest.json',dict(schema_version=1,job_id=spec['job_id'],scene_sha256=scene_sha,snapshot_sha256=spec['input_sha256']['snapshot_blend'],blender_version_string=bpy.app.version_string,blender_version=list(bpy.app.version),engine=render_engine,material_mode=material_mode,frame_start=spec['frame_start'],frame_end=spec['frame_end'],fps_num=spec['fps_num'],fps_den=spec['fps_den'],width=spec['width'],height=spec['height'],dependencies=dependencies,frames=records,visual_status='unverified'))
    core.write_status(job_dir,'encoding',message='Launching configured media Python',visual_status='unverified',mode=spec['mode'])
    media.run_process([spec['media_python'],str(Path(__file__).with_name('media.py')),'--job',str(job_path)],job_dir/'media_worker.log',job_dir)
    if core.read_status(job_dir)['state']!='complete': raise RuntimeError('Media worker returned without technical completion')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        run_job(args.job)
        return 0
    except media.Cancelled:
        return 2
    except Exception as exc:
        job_dir=args.job.resolve().parent
        core.write_status(job_dir,'failed',error=str(exc),visual_status='unverified')
        (job_dir/'worker_error.log').write_text(traceback.format_exc(),encoding='utf-8')
        print(f'RENDER FAILED: {exc}',file=sys.stderr)
        return 1


if __name__=='__main__':
    arguments=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    raise SystemExit(main(arguments))
