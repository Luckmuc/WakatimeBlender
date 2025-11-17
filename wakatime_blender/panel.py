import os

import bpy
from . import __init__ as wakatime_addon
from . import settings

class WAKATIME_PT_TrackedTime(bpy.types.Panel):
    bl_label = "Wakatime"
    bl_idname = "WAKATIME_PT_TrackedTime"
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Wakatime'

    @classmethod
    def poll(cls, context):
        return True

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Wakatime Sync", icon="TIME")

        state = wakatime_addon.tracking_state()
        status = state.get("sync_status", "Sync idle")
        if status.startswith("Sync "):
            icon = "CHECKMARK"
        elif "Error" in status:
            icon = "ERROR"
        else:
            icon = "TIME"
        col.label(text=f"Last sync: {status}", icon=icon)

        col.separator()

        # Tracking status section
        tracking_box = col.box()
        tracking_active = state.get("active", False)
        reason = state.get("reason", "")
        tracked_seconds = int(state.get("tracked_seconds", 0))

        if tracking_active:
            tracking_box.label(text=f"Tracking time: {wakatime_addon.format_tracking_time(tracked_seconds)}", icon="PLAY")
            remaining = max(0.0, state.get("idle_timeout", 0.0) - state.get("idle_seconds", 0.0))
            tracking_box.label(text=f"Idle pause in: {int(remaining)}s", icon="TIME")
        else:
            reason_map = {
                "unsaved": "Save your Blend file to start tracking",
                "idle": "Paused because Blender is idle",
                "unfocused": "Paused while Blender window is unfocused",
                "api-key": "Configure your Wakatime API key",
                "": "Tracking paused",
            }
            tracking_box.alert = True
            tracking_box.label(text=reason_map.get(reason, "Tracking paused"), icon="PAUSE")

        if not state.get("file_saved", True):
            warn = col.box()
            warn.alert = True
            warn.label(text="Save your .blend file to enable tracking", icon="ERROR")

        col.separator()

        setup_box = col.box()
        setup_box.alert = True
        setup_box.label(
            text="Run Force Sync once before tracking to download the WakaTime CLI runtime.",
            icon="INFO",
        )

        col.separator()
        actions = col.row(align=True)
        actions.operator("ui.wakatime_blender_preferences", text="Preferences", icon="PREFERENCES")
        actions.operator("ui.download_wakatime_client", text="Force Sync", icon="FILE_REFRESH")

        docs_row = col.row(align=True)
        docs_row.operator("wm.url_open", text="Dashboard", icon="URL").url = str(settings.api_server_url().rstrip('/'))
        docs_row.operator("wm.url_open", text="Docs", icon="HELP").url = "https://wakatime.com/help"

        col.separator()

        timeline_dir = wakatime_addon.timeline_directory()
        timeline_box = col.box()
        timeline_box.label(text="Timeline Logs", icon="FILE_SCRIPT")
        timeline_box.label(text=f"Folder: {timeline_dir}", icon="FILE_FOLDER")
        open_op = timeline_box.operator("wm.path_open", text="Open Timeline Folder", icon="FILE_FOLDER")
        open_op.filepath = timeline_dir
        latest_log = wakatime_addon.timeline_latest_log()
        if latest_log:
            timeline_box.label(text=f"Today: {os.path.basename(latest_log)}", icon="TEXT")

        col.separator()
        col.label(text="Offline activity continues syncing automatically.", icon="INFO")

def register():
    pass

def unregister():
    pass