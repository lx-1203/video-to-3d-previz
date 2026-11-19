"""OrderFlow Blender add-on; pure-Python tools may import this package safely."""

bl_info = {
    "name": "接单助手 / OrderFlow",
    "author": "OrderFlow",
    "version": (0, 3, 1),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > 接单助手",
    "description": "白模搭建、相机焦距、多人骨架、动作参考、渲染与交付",
    "category": "Import-Export",
}


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
