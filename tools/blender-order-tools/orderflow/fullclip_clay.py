"""Give editable structural parts separate clay finishes and record their geometry."""

from collections import Counter, defaultdict
import bmesh
import bpy


def finish_group(kind):
    label = kind.casefold()
    if "ribbed fixture" in label:
        return "fixture"
    if "brace" in label:
        return "bracing"
    if "seat" in label or "chair" in label:
        if "back" in label:
            return "seat back"
        if "cushion" in label or "pad" in label:
            return "seat cushion"
        return "seat support"
    if "deck" in label or "walkway" in label or "platform" in label:
        return "floor"
    if "wall" in label:
        return "wall surface"
    if "rail" in label:
        return "railing"
    if any(word in label for word in ("frame", "mullion", "grating", "column", "beam", "pillar")):
        return "frame"
    if "backing" in label:
        return "background"
    if "shell" in label:
        return "wall surface"
    return "solid set"


def finish_scene(scene):
    # Diffuse colors are linear values. The studio rig and contact shadows reveal
    # shape, while adjacent parts retain distinct values in the neutral palette.
    finishes = {
        "floor": (0.54, 0.52, 0.49),
        "wall surface": (0.41, 0.43, 0.46),
        "background": (0.30, 0.33, 0.37),
        "seat back": (0.72, 0.76, 0.80),
        "seat cushion": (0.50, 0.56, 0.62),
        "seat support": (0.29, 0.31, 0.34),
        "railing": (0.48, 0.50, 0.53),
        "frame": (0.70, 0.72, 0.75),
        "solid set": (0.55, 0.58, 0.61),
        "fixture": (0.45, 0.55, 0.58),
        "bracing": (0.38, 0.40, 0.42),
    }
    materials = {}
    counts = defaultdict(Counter)
    for obj in scene.objects:
        if obj.type != "MESH" or "reconstruction_kind" not in obj:
            continue
        group = finish_group(obj["reconstruction_kind"])
        if group not in materials:
            mat = bpy.data.materials.new("White model / " + group)
            mat.diffuse_color = (*finishes[group], 1)
            mat.roughness = 0.78
            mat.use_nodes = True
            shader = mat.node_tree.nodes.get("Principled BSDF")
            shader.inputs["Base Color"].default_value = mat.diffuse_color
            shader.inputs["Roughness"].default_value = mat.roughness
            materials[group] = mat
        if "grille_luma" in obj:
            # Quantized neutral clay preserves the observed foreground/background
            # value ordering across the aperture without baking image textures.
            tone = round((0.12 + 0.63 * float(obj["grille_luma"])) * 48) / 48
            key = ("grille", tone)
            if key not in materials:
                mat = materials[group].copy()
                mat.diffuse_color = (tone, tone, tone, 1.0)
                mat.node_tree.nodes.get("Principled BSDF").inputs[
                    "Base Color"
                ].default_value = mat.diffuse_color
                materials[key] = mat
            obj.data.materials.clear()
            obj.data.materials.append(materials[key])
        elif obj.get("background_luminance_only"):
            # Retain only low-frequency neutral light on an explicitly inferred
            # backing. Material slots keep the original per-face assignment.
            for index, old in enumerate(tuple(obj.data.materials)):
                gray = sum(v * w for v, w in zip(old.diffuse_color, (0.2126, 0.7152, 0.0722)))
                value = round(max(0.17, min(0.57, 0.16 + 0.48 * gray)) * 80) / 80
                key = ("backing light", value)
                if key not in materials:
                    mat = materials[group].copy()
                    mat.diffuse_color = (value, value, value, 1.0)
                    mat.node_tree.nodes.get("Principled BSDF").inputs[
                        "Base Color"
                    ].default_value = mat.diffuse_color
                    materials[key] = mat
                obj.data.materials[index] = materials[key]
        else:
            obj.data.materials.clear()
            obj.data.materials.append(materials[group])
        obj["white_model_component"] = group
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            solid = (
                bool(bm.faces)
                and all(edge.is_manifold for edge in bm.edges)
                and abs(bm.calc_volume()) > 1.0e-10
            )
            edge_lengths = [edge.calc_length() for edge in bm.edges if edge.calc_length() > 1.0e-5]
        finally:
            bm.free()
        counts[group]["meshes"] += 1
        counts[group]["closed_solids"] += int(solid)
        if (
            solid
            and edge_lengths
            and group in ("seat back", "seat cushion", "seat support", "frame", "railing")
        ):
            bevel = obj.modifiers.new("Shallow edge bevel", "BEVEL")
            bevel.width = min(edge_lengths) * 0.08
            bevel.segments = 1
            bevel.limit_method = "ANGLE"
            bevel.harden_normals = True
    return {kind: dict(values) for kind, values in counts.items()}


def light_scene(scene):
    shade = scene.display.shading
    shade.light = "STUDIO"
    shade.studio_light = "paint.sl"
    shade.color_type = "MATERIAL"
    shade.show_shadows = True
    shade.show_cavity = True
    shade.cavity_type = "WORLD"
    shade.cavity_ridge_factor = 1.0
    shade.cavity_valley_factor = 1.1
    shade.show_specular_highlight = False
    shade.background_type = "VIEWPORT"
    shade.background_color = (0.24, 0.26, 0.29)
