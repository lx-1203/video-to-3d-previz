"""Create a source-audio sequence comparison from a verified motion candidate.

The inspection camera and display colors are preview overrides. This command
does not reconstruct the reference scene or certify visual correctness.
"""
import argparse
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import sys


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def artifact(path):
    path = Path(path).resolve()
    return {'path': str(path), 'sha256': sha256(path), 'bytes': path.stat().st_size}


def validate_artifact(record):
    if not isinstance(record, dict):
        raise ValueError('Expected a registered artifact')
    path = Path(record['path']).resolve()
    if (not path.is_file() or path.stat().st_size != record['bytes']
            or sha256(path) != record['sha256']):
        raise ValueError('Artifact SHA/size mismatch: ' + str(path))
    return path


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def source_clock(request):
    rate = request['source_fps']
    num, den = rate['num'], rate['den']
    if any(isinstance(x, bool) or not isinstance(x, int) or x <= 0 for x in (num, den)):
        raise ValueError('source_fps requires positive integer num/den')
    fps = Fraction(num, den)
    if fps > 32767:
        raise ValueError('Frame rate exceeds Blender range')
    start = Fraction(str(request['interval']['start_seconds']))
    duration = Fraction(str(request['interval']['duration']))
    if start < 0 or not 0 < duration <= 60:
        raise ValueError('Invalid source interval')
    count = max(1, math.ceil(float(duration * fps) - 1e-9))
    return fps, count, [start + i / fps for i in range(count)]


def validate_request(request):
    source_clock(request)
    for key in ('candidate', 'video', 'profile'):
        validate_artifact(request[key])
    if request.get('parameters') is not None:
        validate_artifact(request['parameters'])
    for key in ('blender', 'ffmpeg'):
        if not Path(request[key]).is_file():
            raise ValueError('Missing ' + key + ': ' + str(request[key]))


def validate_candidate_rate(fps, fps_base, expected):
    # Blender stores fps_base as float32; a fixed 1e-5 tolerance rejects valid
    # high rates such as 240000/1001. One ppm admits that storage roundoff.
    if fps_base <= 0 or not math.isclose(fps/fps_base, float(expected), rel_tol=1e-6, abs_tol=1e-6):
        raise ValueError('Candidate frame rate differs from source clock')


def load_parameters(record):
    """Allow the exact numeric NumPy types used by the pinned GEM release."""
    if record is None:
        return None
    import numpy as np
    import torch
    path = validate_artifact(record)
    payload = path.read_bytes()
    if len(payload) != record['bytes'] or hashlib.sha256(payload).hexdigest() != record['sha256']:
        raise ValueError('Parameters changed while loading')
    core = np._core if hasattr(np, '_core') else np.core
    allowed = [core.multiarray._reconstruct, np.ndarray, np.dtype]
    allowed.extend(type(np.dtype(kind)) for kind in (np.float32, np.int32, np.int64, np.bool_))
    with torch.serialization.safe_globals(allowed):
        result = torch.load(io.BytesIO(payload), map_location='cpu', weights_only=True)
    if not isinstance(result, dict):
        raise ValueError('GEM parameters must be an object')
    return result


def tracking_for_frames(parameters, frame_count, fps):
    """Map the pinned GEM video frontend's 30Hz tracking to preview times."""
    unknown = [{'box_xyxy': None, 'observed_primary': None, 'tracking_frame0': None}
               for _ in range(frame_count)]
    if parameters is None or not parameters.get('tracking'):
        return {'available': False, 'observed_count': None, 'filled_ranges_frame0': [],
                'note': 'No registered tracking evidence; target identity and observations are unknown.'}, unknown
    import numpy as np
    tracking = parameters['tracking']
    boxes = np.asarray(tracking['boxes_xyxy'], dtype=float)
    observed = np.asarray(tracking['observed_primary'])
    if (boxes.ndim != 2 or boxes.shape[1] != 4 or observed.ndim != 1
            or len(boxes) != len(observed) or observed.dtype != np.bool_
            or not len(boxes) or not np.isfinite(boxes).all()
            or (boxes[:, 2:] < boxes[:, :2]).any()):
        raise ValueError('Malformed tracking metadata')
    # This worker accepts current GEM motion-video cache tracking, normalized
    # by that frontend to 30Hz, not an arbitrary estimator's tracking clock.
    entries = []
    for index in range(frame_count):
        source_index = int(Fraction(index * 30, 1) / fps)
        if source_index >= len(boxes):
            raise ValueError('Tracking coverage is shorter than the preview')
        entries.append({'box_xyxy': boxes[source_index].tolist(),
                        'observed_primary': bool(observed[source_index]),
                        'tracking_frame0': source_index})
    filled = [i for i, entry in enumerate(entries) if not entry['observed_primary']]
    ranges = []
    for index in filled:
        if ranges and index == ranges[-1][1] + 1:
            ranges[-1][1] = index
        else:
            ranges.append([index, index])
    return {'available': True, 'tracking_fps': 30, 'preview_frames': frame_count,
            'observed_count': frame_count - len(filled), 'filled_ranges_frame0': ranges,
            'note': 'Observed boxes are not accuracy or verified identity. Filled boxes mean tracking loss, not proof that the person left the frame.'}, entries


def encoding_command(ffmpeg, source, output, fps, start, requested_duration, count):
    duration = float(Fraction(count, 1) / fps)
    # Even -ss 0 triggers an input seek and can discard AAC decoder preroll.
    # Decode from the natural beginning for zero; nonzero clips seek once.
    seek = ['-ss', str(float(start))] if start > 0 else []
    return [str(ffmpeg), '-hide_banner', '-loglevel', 'warning', '-n',
            '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', '960x1080', '-r', str(fps),
            '-i', 'pipe:0'] + seek + ['-i', str(source),
            '-map', '0:v:0', '-map', '1:a:0?', '-t', str(duration),
            '-c:v', 'libx264', '-crf', '18', '-preset', 'fast', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '192k',
            '-af', f'atrim=duration={float(requested_duration)},asetpts=PTS-STARTPTS,apad=whole_dur={duration}',
            '-movflags', '+faststart', str(output)]


def video_samples(path, times, hold_period):
    """Match GEM's accurate seek: first available frame at/after clip start.

    Source and preview clocks have the same declared rate. A non-frame-aligned
    start therefore keeps the first available frame, rather than introducing
    the preceding source frame that GEM discarded with its input -ss option.
    Actual sampled timestamps are retained in the manifest.
    """
    import av
    with av.open(str(path)) as container:
        decoder = iter(container.decode(video=0))
        current = next(decoder)
        if current.pts is None:
            raise ValueError('Source video lacks timestamps')
        origin = float(current.pts * current.time_base)
        current_time = 0.0
        following = next(decoder, None)
        for desired in times:
            desired = float(desired)
            while following is not None and current_time < desired - 1e-7:
                if following.pts is None:
                    raise ValueError('Source video lacks timestamps')
                next_time = float(following.pts * following.time_base) - origin
                if next_time <= current_time:
                    raise ValueError('Source video timestamps are not increasing')
                current, current_time = following, next_time
                following = next(decoder, None)
            if following is None and desired >= current_time + float(hold_period) + 1e-6:
                raise ValueError('Source interval exceeds decoded video')
            yield current.to_image().convert('RGB'), current_time


def render_worker(request):
    import bpy
    from mathutils import Vector
    validate_artifact(request['candidate'])
    profile = read_json(validate_artifact(request['profile']))
    out = Path(request['output_dir']).resolve()
    frames_dir = out / 'frames'
    frames_dir.mkdir()
    bpy.ops.wm.open_mainfile(filepath=request['candidate']['path'], load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    mesh = bpy.data.objects[profile['mesh_object']]
    rig = bpy.data.objects[profile['rig_object']]
    fps, expected_count, _ = source_clock(request)
    if scene.frame_end - scene.frame_start + 1 != expected_count:
        raise ValueError('Candidate frame range differs from requested interval')
    validate_candidate_rate(scene.render.fps, scene.render.fps_base, fps)
    bounds = []
    for frame in range(scene.frame_start, scene.frame_end + 1):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
        coordinates = [evaluated.matrix_world @ v.co for v in evaluated.data.vertices]
        if not coordinates:
            raise ValueError('Candidate visible mesh is empty')
        bounds.extend((Vector([min(v[i] for v in coordinates) for i in range(3)]),
                       Vector([max(v[i] for v in coordinates) for i in range(3)])))
    low = Vector([min(v[i] for v in bounds) for i in range(3)])
    high = Vector([max(v[i] for v in bounds) for i in range(3)])
    center = (low + high) * .5
    camera_data = bpy.data.cameras.new('MotionSequenceInspectionCamera')
    camera = bpy.data.objects.new('MotionSequenceInspectionCamera', camera_data)
    scene.collection.objects.link(camera)
    camera.location = center + Vector((0, 10, 1))
    camera.rotation_euler = (center - camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera_data.type = 'ORTHO'
    camera_data.ortho_scale = max((high.x-low.x)*1.18,
                                (high.z-low.z+(high.y-low.y)*.1)*(960/540)*1.15, .1)
    scene.camera = camera
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.render.resolution_x, scene.render.resolution_y = 960, 540
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGB'
    scene.render.image_settings.compression = 15
    shading = scene.display.shading
    shading.light, shading.color_type, shading.background_type = 'STUDIO', 'MATERIAL', 'WORLD'
    shading.show_shadows, shading.show_cavity, shading.cavity_type = True, True, 'BOTH'
    if scene.world is None:
        scene.world = bpy.data.worlds.new('MotionInspectionWorld')
    scene.world.color = (.055, .065, .08)
    colors_changed = False
    if (profile.get('id') == 'client-l3-bvh-v1' and len(mesh.data.vertices) == 578
            and len(mesh.data.polygons) == 590
            and sum(face.material_index == 1 for face in mesh.data.polygons) == 17):
        for index, material in enumerate(mesh.data.materials):
            if material is not None:
                # Copies keep another object sharing the old material intact.
                material = material.copy()
                mesh.data.materials[index] = material
                material.diffuse_color = (.045, .22, .65, 1) if index == 1 else (.75, .72, .67, 1)
        colors_changed = True
    scene.frame_set(scene.frame_start)
    inspection = out / 'inspection.blend'
    if inspection.exists():
        raise ValueError('Refusing to overwrite inspection blend')
    bpy.ops.wm.save_as_mainfile(filepath=str(inspection), check_existing=False)
    # Render the actual saved inspection scene, not unsaved in-memory state.
    bpy.ops.wm.open_mainfile(filepath=str(inspection), load_ui=False, use_scripts=False)
    scene = bpy.context.scene
    mesh = bpy.data.objects[profile['mesh_object']]
    rig = bpy.data.objects[profile['rig_object']]
    rendered = []
    for index, frame in enumerate(range(scene.frame_start, scene.frame_end + 1)):
        scene.frame_set(frame)
        path = frames_dir / f'{index:04d}.png'
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        rendered.append({'frame': frame, **artifact(path)})
    validate_artifact(request['candidate'])
    write_json(out / 'render_manifest.json', {
        'candidate_sha256': request['candidate']['sha256'], 'inspection': artifact(inspection),
        'frame_start': scene.frame_start, 'frame_end': scene.frame_end,
        'fps': str(fps), 'size': [960, 540], 'rendered': rendered,
        'mesh_vertices': len(mesh.data.vertices), 'mesh_polygons': len(mesh.data.polygons),
        'rig_bones': len(rig.data.bones), 'rig_drivers': len(rig.animation_data.drivers),
        'overrides': {'camera': 'Fixed +Y inspection view, not original reference camera',
                      'engine': 'Workbench studio', 'client_display_palette_applied': colors_changed}})


def font_pair():
    from PIL import ImageFont
    windows_font = Path('C:/Windows/Fonts/msyh.ttc')
    if windows_font.is_file():
        return ImageFont.truetype(str(windows_font), 20), ImageFont.truetype(str(windows_font), 17), True
    return ImageFont.load_default(size=20), ImageFont.load_default(size=17), False


def fit_panel(image):
    from PIL import Image, ImageOps
    fitted = ImageOps.contain(image, (960, 540), Image.Resampling.LANCZOS)
    panel = Image.new('RGB', (960, 540), (0, 0, 0))
    panel.paste(fitted, ((960-fitted.width)//2, (540-fitted.height)//2))
    return panel


def encode_and_verify(request, out, rendered, entries):
    import av
    from PIL import Image, ImageDraw
    fps, count, times = source_clock(request)
    duration = Fraction(count, 1) / fps
    font, small, chinese = font_pair()
    target = out / 'comparison.mp4'
    cmd = encoding_command(request['ffmpeg'], request['video']['path'], target, fps,
                           times[0], request['interval']['duration'], count)
    source_times, posters, contacts = [], [], []
    sheet = None
    log_path = out / 'encode.log'
    with log_path.open('wb') as log:
        process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        try:
            samples = video_samples(request['video']['path'], times, 1/fps)
            for index, item in enumerate(rendered):
                ref, timestamp = next(samples)
                source_times.append(timestamp)
                entry = entries[index]
                observed = entry['observed_primary']
                color = (40, 230, 95) if observed is True else ((255, 160, 30) if observed is False else (215, 220, 230))
                if entry['box_xyxy'] is not None:
                    ImageDraw.Draw(ref).rectangle(entry['box_xyxy'], outline=color, width=4)
                ref = fit_panel(ref)
                with Image.open(validate_artifact(item)) as render:
                    model = render.convert('RGB')
                if model.size != (960, 540):
                    raise ValueError('Unexpected render dimensions')
                canvas = Image.new('RGB', (960, 1080))
                canvas.paste(ref, (0, 0)); canvas.paste(model, (0, 540))
                draw = ImageDraw.Draw(canvas)
                draw.rectangle((0, 0, 959, 37), fill=(12, 17, 23))
                draw.rectangle((0, 540, 959, 577), fill=(12, 17, 23))
                if chinese:
                    state = '检测到主要目标' if observed is True else ('跟踪丢失，算法补齐' if observed is False else '跟踪证据未提供')
                    title = '原视频｜' + state
                    caption = '自动动作 → 指定模型｜检查机位，场景未还原'
                else:
                    title = 'REFERENCE | ' + ('Observed detection' if observed is True else ('Tracking lost / filled box' if observed is False else 'Tracking unknown'))
                    caption = 'MOTION CANDIDATE | inspection camera; scene not reconstructed'
                draw.text((14, 6), title, font=font, fill=color)
                draw.text((14, 546), caption, font=font, fill=(236, 239, 243))
                draw.text((735, 1025), f'{float(times[index]):.2f}s | +{index/float(fps):.2f}s', font=small, fill='white')
                process.stdin.write(canvas.tobytes())
                if index in {0, count//4, count//2, count*3//4, count-1}:
                    path = out / f'comparison_{index:04d}.png'
                    canvas.save(path); posters.append({'frame0': index, **artifact(path)})
                if index % 25 == 0:
                    sheet = Image.new('RGB', (1600, 1025), (15, 19, 25))
                x, y = index % 5 * 320, (index % 25)//5 * 205
                sheet.paste(model.resize((320, 180)), (x, y))
                ImageDraw.Draw(sheet).text((x+5, y+182), f'frame {index+1} | +{index/float(fps):.3f}s', font=small, fill='white')
                if index % 25 == 24 or index == count-1:
                    path = out / f'contact_sheet_{len(contacts)+1}.jpg'
                    sheet.save(path, quality=94); contacts.append(artifact(path))
            process.stdin.close()
            if process.wait(timeout=120) != 0:
                raise RuntimeError('FFmpeg encode failed; see ' + str(log_path))
        except BaseException:
            if process.stdin and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass  # Preserve the originating encode/write exception.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=10)
            raise
        finally:
            samples.close()
    decoded_times = []
    with av.open(str(target)) as container:
        stream = container.streams.video[0]
        video_info = {'width': stream.width, 'height': stream.height, 'fps': str(stream.average_rate),
                      'codec': stream.codec_context.name}
        for frame in container.decode(video=0):
            decoded_times.append(float(frame.pts * frame.time_base))
    if len(decoded_times) != count or any(abs(t-i/float(fps)) > 1e-5 for i, t in enumerate(decoded_times)):
        raise ValueError('Final video decoded frame count/timing mismatch')
    with av.open(str(request['video']['path'])) as container:
        source_audio = bool(container.streams.audio)
    with av.open(str(target)) as container:
        audio_count = len(container.streams.audio)
        audio_frames = sum(1 for _ in container.decode(audio=0)) if audio_count else 0
    if source_audio and not audio_frames:
        raise ValueError('Source audio was not included in the comparison')
    return {'video': artifact(target), 'contacts': contacts, 'posters': posters,
            'decoded_frames': count, 'video_info': video_info, 'all_pts_match': True,
            'audio_streams': audio_count, 'decoded_audio_frames': audio_frames,
            'audio_listened': False, 'source_sample_times_seconds': source_times,
            'source_sampling_convention': 'First available source frame at/after requested timestamps; accurate-seek convention matching the GEM clip start',
            'duration_seconds': float(duration), 'encoding_command': cmd}


def create_preview(request, request_path):
    validate_request(request)
    out = Path(request['output_dir']).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any((out/name).exists() for name in ('report.json', 'render_manifest.json', 'inspection.blend', 'comparison.mp4', 'frames')):
        raise ValueError('Refusing to overwrite an existing preview attempt')
    fps, count, _ = source_clock(request)
    parameters = load_parameters(request.get('parameters'))
    quality, entries = tracking_for_frames(parameters, count, fps)
    with (out/'blender.log').open('wb') as log:
        subprocess.run([request['blender'], '--background', '--factory-startup', '--disable-autoexec',
                        '--python-exit-code', '1', '--python', str(Path(__file__).resolve()), '--',
                        '--blender-worker', '--request', str(Path(request_path).resolve())],
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800)
    manifest_path = out / 'render_manifest.json'
    manifest = read_json(manifest_path)
    rendered = manifest['rendered']
    if len(rendered) != count:
        raise ValueError('Render sequence frame count mismatch')
    for item in rendered:
        path = validate_artifact(item)
        if not path.is_relative_to(out):
            raise ValueError('Render escaped output directory')
    media = encode_and_verify(request, out, rendered, entries)
    validate_request(request)
    scope = {'kind': 'sequence', 'frames': count, 'fps': str(fps),
             'source_interval': request['interval'], 'inspection_camera': True,
             'note': 'Sequence preview only; no automatic visual acceptance or whole-order certification.'}
    manifest.update(scope=scope, tracking_quality=quality, media=media,
                    input_sha256={key: request[key]['sha256'] if request.get(key) else None
                                  for key in ('candidate', 'video', 'profile', 'parameters')},
                    worker_sha256=sha256(__file__))
    write_json(manifest_path, manifest)
    outputs = {'comparison_video': media['video'], 'inspection_blend': manifest['inspection'],
               'preview_manifest': artifact(manifest_path)}
    outputs.update({f'contact_sheet_{i+1}': record for i, record in enumerate(media['contacts'])})
    for record in outputs.values():
        if not validate_artifact(record).is_relative_to(out):
            raise ValueError('Preview artifact escaped output directory')
    report = {'ok': True, 'input_sha256': manifest['input_sha256'], 'artifacts': outputs,
              'scope': scope, 'tracking_quality': quality}
    write_json(out/'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--blender-worker', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
    request = read_json(args.request)
    if args.blender_worker:
        render_worker(request)
    else:
        report = create_preview(request, args.request)
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
