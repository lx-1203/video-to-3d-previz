"""Dimensioned blockout geometry and explicitly selected local Blender assets."""
import math
from pathlib import Path


def blockout_parts(kind, dimensions, opening):
    width, depth, height = dimensions
    if any(not math.isfinite(value) or value <= 0 for value in dimensions):
        raise ValueError("宽、深、高必须为正数。")
    if kind != 'OPENING':
        return [((0, 0, -height / 2 if kind == 'FLOOR' else height / 2), dimensions)]
    gap, clear = opening
    if not (0 < gap < width and 0 < clear < height):
        raise ValueError("门洞净宽、净高须小于墙体宽高。")
    post = (width - gap) / 2
    offset = (gap + post) / 2
    return [((-offset, 0, height / 2), (post, depth, height)),
            ((offset, 0, height / 2), (post, depth, height)),
            ((0, 0, (clear + height) / 2), (gap, depth, height - clear))]


def add_blockout(scene, kind, dimensions, opening):
    import bpy

    parts = blockout_parts(kind, dimensions, opening)
    label = {'FLOOR': '地面', 'WALL': '墙体', 'OPENING': '门洞', 'BOX': '道具白模'}[kind]
    group = bpy.data.collections.new('白模搭建 · ' + label)
    scene.collection.children.link(group)
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    for index, (center, size) in enumerate(parts, 1):
        name = f'{label} {index}'
        mesh = bpy.data.meshes.new(name)
        vertices = [tuple(c * dimension / 2 for c, dimension in zip(corner, size))
                    for corner in [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
                                   (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]]
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        obj.location = tuple(a + b for a, b in zip(scene.cursor.location, center))
        group.objects.link(obj)
    return group


def local_collection(scene, path, name=None):
    import bpy

    path = Path(path)
    if not path.is_file() or path.suffix.lower() != '.blend':
        raise ValueError("请选择本地 Blender 资产文件。")
    if bpy.context.preferences.filepaths.use_scripts_auto_execute:
        raise ValueError("读取 Blender 附件前须关闭 Blender 的自动运行 Python 脚本选项。")
    if bpy.data.filepath and Path(bpy.data.filepath).resolve() == path.resolve():
        raise ValueError("请选择当前工程以外的资产文件。")
    with bpy.data.libraries.load(str(path), link=False) as (source, destination):
        if name is None:
            return list(source.collections)
        if name not in source.collections:
            raise ValueError("该资产文件中没有此集合。")
        destination.collections = [name]
    collection = destination.collections[0]
    if collection is None:
        raise ValueError("Blender 未能追加该集合。")
    scene.collection.children.link(collection)
    return collection
