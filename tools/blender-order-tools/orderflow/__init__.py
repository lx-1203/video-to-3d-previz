"""OrderFlow Blender add-on; pure-Python tools may import this package safely."""

bl_info = {
    "name": "接单助手 / OrderFlow",
    "author": "OrderFlow",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > 接单助手",
    "description": "辅助焦距与动作参考、后台试渲与交稿打包",
    "category": "Import-Export",
}


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
