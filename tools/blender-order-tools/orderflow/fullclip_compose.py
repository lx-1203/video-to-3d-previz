"""Compose the solved set, cameras and editable template animation in a fresh Blender file."""

import bpy
import numpy as np
from fractions import Fraction
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orderflow import core, fullclip_scene, fullclip_binding


def evidence_scalar(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"Unexpected evidence value {type(value).__name__}")


def prepare_workspace(scene, info, shots):
    """Open on the source framing with a visible actor ready for manual editing."""
    scene.frame_set(scene.frame_start)
    for n, shot in enumerate(shots, 1):
        scene.timeline_markers.new(f"镜头 {n:02d}", frame=shot["first"] + 1)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                view = area.spaces.active
                view.region_3d.view_perspective = "CAMERA"
                view.shading.color_type = "MATERIAL"
    for name in info["editable_tracks"].values():
        rig = bpy.data.objects[name]
        if not rig.hide_viewport and rig.visible_get():
            bpy.ops.object.select_all(action="DESELECT")
            model = next((child for child in rig.children if child.type == "MESH"), rig)
            model.select_set(True)
            bpy.context.view_layer.objects.active = model
            break


def compose(params, foreground_gauge=None):
    root = params.parent
    cfg = json.loads(params.read_bytes())
    people = json.loads((root / "people.json").read_bytes())
    timeline = json.loads((root / "timeline.json").read_bytes())
    if (
        people["source_sha256"] != cfg["source_sha256"]
        or len(people["frames"]) != timeline["frames"]
    ):
        raise ValueError("People data does not represent the full source")
    for i, frame in enumerate(people["frames"]):
        if frame["source_frame"] != i or Fraction(frame["pts"]) * Fraction(
            frame["time_base"]
        ) != Fraction(timeline["pts"][i]):
            raise ValueError(f"People/source PTS mismatch at frame {i}")
    body_sizes = json.loads((root / "body-sizes.json").read_bytes())
    if (
        body_sizes["source_sha256"] != cfg["source_sha256"]
        or len(body_sizes["frames"]) != timeline["frames"]
    ):
        raise ValueError("Body-size observations do not cover the same source")
    scene = bpy.context.scene
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    scene.frame_start = 1
    scene.frame_end = timeline["frames"]
    fps = Fraction(timeline["fps"])
    scene.render.fps = round(float(fps))
    scene.render.fps_base = scene.render.fps / float(fps)
    h = int(cfg["height"])
    scene.render.resolution_y = h
    scene.render.resolution_x = max(2, round(h * timeline["width"] / timeline["height"] / 2) * 2)
    scene.render.resolution_percentage = 100
    from orderflow.fullclip_gauge import scale_from_people

    manifest = json.loads(Path(cfg["scene_manifest"]).read_bytes())
    scales, evidence = scale_from_people(
        manifest, people, cfg["component_contract"], cfg["height_m"]
    )
    if foreground_gauge:
        for first, correction in foreground_gauge.items():
            previous = scales[first]
            scales[first] = previous * correction["factor"]
            evidence[first].update(
                initial_world_scale=previous,
                world_scale=scales[first],
                foreground_calibration=correction,
            )
    camera = fullclip_scene.build_scene(scene, manifest, shot_world_scale=scales)
    scene.camera = camera
    info = fullclip_binding.bind(
        scene,
        camera,
        people,
        cfg["template"],
        cfg["component_contract"],
        cfg["height_m"],
        manifest=manifest,
        body_sizes=body_sizes,
    )
    if foreground_gauge is None:
        from orderflow.fullclip_guardrail import preflight_gauge

        correction = preflight_gauge(scene, camera, root / "guardrails.json", info)
        if correction:
            # Rebuild from measurements at the refined metric scale; all character
            # mesh dimensions, camera projections and native parenting stay intact.
            bpy.ops.wm.read_factory_settings(use_empty=True)
            return compose(params, correction)
    from orderflow.fullclip_background_pose import refine_background_pose

    info["background_pose"] = refine_background_pose(
        scene, camera, people, info, cfg["component_contract"]
    )
    from orderflow.fullclip_guardrail_actor_visibility import fit_observed_actor_depth

    info["observed_actor_depth"] = fit_observed_actor_depth(
        scene, camera, people, info, root / "guardrails.json", source_config=cfg
    )
    from orderflow.fullclip_body_size import fit_handover

    info["handover_ik"] = fit_handover(
        scene, camera, people, body_sizes, root / "props.json", info
    )
    from orderflow.fullclip_walkway import fit_walkway

    info["gallery_support"] = fit_walkway(scene, manifest, people, info, cfg["height_m"])
    info["foreground_depth"] = fullclip_scene.finalize_foreground(scene, camera, manifest, info)
    from orderflow.fullclip_grille_bind import register_fixed_grilles

    info["grille_registration"] = register_fixed_grilles(scene, camera)
    from orderflow.fullclip_lower_tier import fit_lower_tier

    info["partial_tier_depth"] = fit_lower_tier(scene, camera, people, info)
    from orderflow.fullclip_keyframes import reduce_pose_keys

    info["pose_keys"] = {
        name: reduce_pose_keys(bpy.data.objects[name]) for name in info["editable_tracks"].values()
    }
    from orderflow.fullclip_props import bind_props

    info["visible_props"] = bind_props(scene, camera, root / "props.json", info)
    from orderflow.fullclip_body_size import reconcile_handover_props

    info["visible_props"]["handover_refinement"] = reconcile_handover_props(
        scene, camera, root / "props.json", info["visible_props"], info["handover_ik"]
    )
    info["scene_meter_calibration"] = evidence
    for rig_name in info["editable_tracks"].values():
        rig = bpy.data.objects[rig_name]
        for ob in [rig, *rig.children]:
            if not ob.animation_data or not ob.animation_data.action:
                continue
            action = ob.animation_data.action
            curves = getattr(action, "fcurves", None)
            if curves is None:
                curves = [
                    f
                    for layer in action.layers
                    for strip in layer.strips
                    for bag in strip.channelbags
                    for f in bag.fcurves
                ]
            for curve in curves:
                for key in curve.keyframe_points:
                    key.interpolation = (
                        "CONSTANT" if curve.data_path.startswith("hide_") else "LINEAR"
                    )
    scene.render.engine = "BLENDER_WORKBENCH"
    from orderflow.fullclip_clay import finish_scene, light_scene

    info["geometry_components"] = finish_scene(scene)
    from orderflow.fullclip_guardrail import bind_guardrails

    info["guardrails"] = bind_guardrails(scene, camera, root / "guardrails.json", info)
    light_scene(scene)
    scene.view_settings.view_transform = "Standard"
    scene.display.render_aa = "FXAA"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = "//frames/######"
    scene["source_sha256"] = cfg["source_sha256"]
    scene["estimated_scene"] = True
    scene["local_visual_inference"] = True
    scene["language_model_calls"] = 0
    prepare_workspace(scene, info, manifest["shots"])
    bpy.context.preferences.filepaths.save_version = 0
    blend = root / "preview.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend), compress=True, relative_remap=False)
    core.atomic_json(
        root / "binding.json",
        json.loads(
            json.dumps(
                dict(
                    info,
                    scene_sha256=core.sha256(blend),
                    template_sha256=core.sha256(cfg["template"]),
                ),
                default=evidence_scalar,
                allow_nan=False,
            )
        ),
    )


if __name__ == "__main__":
    compose(Path(sys.argv[sys.argv.index("--") + 1]))
