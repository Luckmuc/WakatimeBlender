import bpy
from bpy.props import StringProperty
from . import settings
from .utils import u

DEFAULT_API_SERVER_URL = "https://api.wakatime.com/"


class PreferencesDialog(bpy.types.Operator):
    bl_idname = "ui.wakatime_blender_preferences"
    bl_label = "Wakatime Preferences"
    bl_description = "Configure Wakatime for Blender"
    bl_options = {"REGISTER", "INTERNAL"}

    # Properties with proper annotations for Blender 2.8+
    api_key: StringProperty(
        name="API Key", 
        default="", 
        description="Your Wakatime API key"
    )
    api_server_url: StringProperty(
        name="API Server URL", 
        default="",
        description="Wakatime API server URL"
    )

    @classmethod
    def show(cls):
        try:
            bpy.ops.ui.wakatime_blender_preferences('INVOKE_DEFAULT')
        except Exception:
            pass

    @classmethod
    def ensure_props(cls):
        # Properties are now defined with annotations, so they should persist
        # This method is kept for compatibility but no longer needs to recreate properties
        pass

    def execute(self, _context):
        try:
            # Validate API key
            if not self.api_key.strip():
                self.report({'ERROR'}, "API Key is required. Get yours from https://wakatime.com/api-key")
                return {"CANCELLED"}
            
            # Validate API server URL
            if not self.api_server_url.strip():
                self.report({'ERROR'}, "API Server URL is required")
                return {"CANCELLED"}
            
            if not self.api_server_url.startswith(("http://", "https://")):
                self.report({'ERROR'}, "API Server URL must start with http:// or https://")
                return {"CANCELLED"}
            
            # Save settings with the utility function to ensure proper encoding
            normalized_api_url = u(self.api_server_url).strip()
            settings.set_api_key(u(self.api_key))
            settings.set_api_server_url(normalized_api_url)
            self.api_server_url = normalized_api_url
            settings.ensure_offline_defaults()
            
            self.report({'INFO'}, "Wakatime preferences saved successfully")
            return {"FINISHED"}
            
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save preferences: {str(e)}")
            return {"CANCELLED"}

    def invoke(self, context, _event):
        # Ensure properties are set up
        self.__class__.ensure_props()
        
        try:
            settings.load()
            # Load existing values from settings
            self.api_key = settings.api_key()
            stored_url = settings.get("api_server_url", "")
            if stored_url == settings.DEFAULT_API_SERVER_URL:
                stored_url = ""
            self.api_server_url = stored_url
            
            # Show the dialog with a reasonable width
            return context.window_manager.invoke_props_dialog(self, width=520)
            
        except Exception as e:
            # If there's an error, show a simple message dialog
            self.report({'ERROR'}, f"Failed to load preferences: {str(e)}")
            return {'CANCELLED'}

    def draw(self, _context):
        layout = self.layout
        
        # Check if properties are available
        if not all(hasattr(self, prop) for prop in ["api_key", "api_server_url"]):
            layout.label(text="Error: Properties not initialized properly", icon="ERROR")
            layout.label(text="Please try closing and reopening this dialog")
            return
        
        col = layout.column(align=True)
        col.label(text="Wakatime Configuration", icon="PREFERENCES")
        col.separator()
        
        # API Configuration section
        box = col.box()
        box.label(text="API Settings", icon="WORLD")
        box.prop(self, "api_key", icon="KEY_HLT")
        box.prop(self, "api_server_url", icon="URL")
        
        col.separator()
        
        maintenance = col.box()
        maintenance.label(text="Maintenance", icon="FILE_REFRESH")
        maintenance.operator("ui.download_wakatime_client", text="Force Sync", icon="FILE_REFRESH")
        info_box = maintenance.box()
        info_box.scale_y = 0.8
        info_box.label(text="Force download the Wakatime runtime if the CLI is missing", icon="INFO")
