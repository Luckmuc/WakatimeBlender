import bpy

from .wakatime_blender import __init__ as addon_impl


bl_info = {
	"name": "WakaTime",
	"category": "Development",
	"author": "Luckmuc",
	"version": (2, 0, 2),
	"blender": (2, 93, 0),
	"description": "Hackatime/Wakatime integration for Blender",
	"tracker_url": "https://github.com/allista/WakatimeBlender/issues",
}

__version__ = ".".join((f"{n}" for n in bl_info["version"]))


def _menu(self, _context):
	self.layout.operator("ui.wakatime_blender_preferences")
	self.layout.operator("ui.download_wakatime_client")


def register():
	addon_impl.set_addon_version(__version__)
	addon_impl.register()
	try:
		bpy.types.TOPBAR_MT_app_system.remove(_menu)
	except Exception:
		pass
	bpy.types.TOPBAR_MT_app_system.append(_menu)


def unregister():
	try:
		bpy.types.TOPBAR_MT_app_system.remove(_menu)
	except Exception:
		pass
	addon_impl.unregister()
