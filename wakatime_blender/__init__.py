import ctypes
import os
import sys
import time
from typing import Optional, Tuple

import bpy
from bpy.app.handlers import persistent

from . import settings
from .log import ERROR, WARNING, log
from .panel import WAKATIME_PT_TrackedTime, register as panel_register, unregister as panel_unregister
from .preferences import PreferencesDialog
from .timeline_logger import TIMELINE_DIR, latest_log_path, log_event as timeline_log_event, log_operator_event
from .wakatime_downloader import ForceWakatimeDownload

try:
	from .heartbeat_queue import HeartbeatQueue, sync_offline_activity as _real_sync_offline_activity
except ImportError:
	from .heartbeat_queue import HeartbeatQueue  # type: ignore  # minimal fallback

	def sync_offline_activity():
		print("[Wakatime] Warning: sync_offline_activity not available in heartbeat_queue; skipping offline sync")
		return False, "Offline sync unavailable"
else:
	def sync_offline_activity():
		return _real_sync_offline_activity()

_statusbar_timer = None
_tracking_timer = None
_cached_time_display = "Tracking paused • Sync idle"
_sync_status = "Sync idle"
_tracking_status = "Tracking paused"
_sync_timer_registered = False
_addon_version = "0.0.0"
_heartbeat_queue: Optional[HeartbeatQueue] = None
_last_activity_monotonic = time.monotonic()
_idle_timeout = 30.0
_tracking_active = False
_tracking_reason = "unsaved"
_last_focus_state = True
_last_depsgraph_ping = 0.0
_handlers_registered = False
_missing_handlers = set()
_pref_warnings = set()
_startup_verified = False
_event_watchers_enabled = False
_event_watcher_windows = set()
_event_watcher_last_error = {}


def _verify_minimum_environment() -> bool:
	global _startup_verified
	if _startup_verified:
		return True
	required_handlers = ["depsgraph_update_post", "save_post"]
	missing = [name for name in required_handlers if _handler_list(name) is None]
	if missing:
		for name in missing:
			log(ERROR, "Blender handler '{}' unavailable. WakaTime tracking disabled.", name)
		_startup_verified = False
		return False
	_startup_verified = True
	return True


def _blend_filepath() -> str:
	"""Return the active blend filepath, tolerating restricted contexts."""
	for provider in (
		lambda: bpy.data.filepath,
		lambda: bpy.context.blend_data.filepath,
	):
		try:
			value = provider()
			if isinstance(value, str):
				return value
		except Exception:
			continue
	return ""


def _handler_list(name: str):
	try:
		return getattr(bpy.app.handlers, name)
	except AttributeError:
		return None


def _resolve_pref_float(raw_value, minimum: float, default: float) -> float:
	try:
		value = float(raw_value)
		if minimum is not None:
			return max(minimum, value)
		return value
	except (TypeError, ValueError):
		text = str(raw_value)
		if "_PropertyDeferred" in text:
			try:
				import re
				match = re.search(r"'default':\s*([\d\.]+)", text)
				if match:
					return float(match.group(1))
			except Exception:
				pass
	return default


def set_addon_version(version: str) -> None:
	"""Store the running add-on version for status and CLI metadata."""
	global _addon_version
	_addon_version = version
	if _heartbeat_queue is not None:
		_heartbeat_queue._version = version  # pylint: disable=protected-access


def addon_version() -> str:
	return _addon_version


def last_sync_status() -> str:
	return _sync_status


def tracking_state() -> dict:
	tracked_seconds = _heartbeat_queue.get_tracked_time_live() if _heartbeat_queue else 0
	idle_seconds = max(0.0, time.monotonic() - _last_activity_monotonic)
	return {
		"active": _tracking_active,
		"reason": _tracking_reason,
		"tracked_seconds": tracked_seconds,
		"idle_seconds": idle_seconds,
		"idle_timeout": _idle_timeout,
		"file_saved": bool(_blend_filepath()),
		"focused": _last_focus_state,
		"sync_status": _sync_status,
	}


def timeline_directory() -> str:
	return TIMELINE_DIR


def timeline_latest_log() -> Optional[str]:
	return latest_log_path()


def _notify_statusbar_change() -> None:
	try:
		for window in bpy.context.window_manager.windows:
			for area in window.screen.areas:
				if area.type in {'STATUSBAR', 'TEXT_EDITOR'}:
					area.tag_redraw()
	except Exception:
		pass


def format_tracking_time(raw_seconds: int) -> str:
	seconds = max(0, int(raw_seconds))
	hours, remainder = divmod(seconds, 3600)
	minutes, secs = divmod(remainder, 60)
	return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _compose_tracking_message() -> str:
	if _tracking_active and _heartbeat_queue:
		tracked = _heartbeat_queue.get_tracked_time_live()
		return f"Tracking {format_tracking_time(tracked)}"
	if _tracking_reason == "unsaved":
		return "Paused: Save file"
	if _tracking_reason == "idle":
		return "Paused: Idle"
	if _tracking_reason == "unfocused":
		return "Paused: Blender unfocused"
	if _tracking_reason == "api-key":
		return "Paused: Configure API key"
	if _tracking_reason == "disabled":
		return "Tracking unavailable"
	return "Tracking paused"


def _refresh_statusbar_cache() -> None:
	global _tracking_status, _cached_time_display
	_tracking_status = _compose_tracking_message()
	_cached_time_display = f"{_tracking_status} • {_sync_status}"
	_notify_statusbar_change()


def _is_blender_focused() -> bool:
	global _last_focus_state
	try:
		if sys.platform.startswith("win"):
			user32 = ctypes.windll.user32  # type: ignore[attr-defined]
			hwnd = user32.GetForegroundWindow()
			if not hwnd:
				_last_focus_state = False
				return False
			pid = ctypes.c_ulong()
			user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
			focused = pid.value == os.getpid()
			_last_focus_state = focused
			return focused
		if sys.platform == "darwin":
			try:
				from AppKit import NSWorkspace  # type: ignore
			except Exception:
				_last_focus_state = True
				return True
			app = NSWorkspace.sharedWorkspace().frontmostApplication()
			focused = bool(app and app.processIdentifier() == os.getpid())
			_last_focus_state = focused
			return focused
		focused = bpy.context.window is not None
		_last_focus_state = focused
		return focused
	except Exception:
		return _last_focus_state


def _compute_tracking_condition() -> Tuple[bool, str]:
	if not _blend_filepath():
		return False, "unsaved"
	if not settings.api_key():
		return False, "api-key"
	if not _is_blender_focused():
		return False, "unfocused"
	if time.monotonic() - _last_activity_monotonic >= _idle_timeout:
		return False, "idle"
	return True, ""


def _set_tracking_state(active: bool, reason: str) -> None:
	global _tracking_active, _tracking_reason
	previous_active = _tracking_active
	previous_reason = _tracking_reason
	if active == previous_active and reason == previous_reason:
		return
	_tracking_active = active
	_tracking_reason = reason
	if active:
		if not previous_active:
			timeline_log_event("tracking resumed")
			_enqueue_current_file(is_write=False)
	else:
		if previous_active or reason != previous_reason:
			timeline_log_event(f"tracking paused ({reason})")
	_refresh_statusbar_cache()


def _update_tracking_state() -> bool:
	active, reason = _compute_tracking_condition()
	_set_tracking_state(active, reason)
	return active


def _enqueue_current_file(is_write: bool = False) -> None:
	if not _tracking_active or _heartbeat_queue is None:
		return
	global _last_activity_monotonic
	_last_activity_monotonic = time.monotonic()
	filepath = _blend_filepath()
	if not filepath:
		return
	_heartbeat_queue.enqueue(filepath, is_write=is_write)


def _record_operator_activity(operator: Optional[object]) -> None:
	global _last_activity_monotonic
	_last_activity_monotonic = time.monotonic()
	if operator is not None:
		log_operator_event(operator)
	if _update_tracking_state():
		_enqueue_current_file(is_write=False)


def _record_general_activity() -> None:
	global _last_activity_monotonic
	_last_activity_monotonic = time.monotonic()
	was_active = _tracking_active
	if _update_tracking_state() and not was_active:
		_enqueue_current_file(is_write=False)


def _should_watch_event(event) -> bool:
	if event.type == 'TIMER':
		return False
	if event.value in {'PRESS', 'RELEASE', 'CLICK', 'DOUBLE_CLICK'}:
		return True
	if event.value == 'NOTHING' and event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE'}:
		return True
	if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'ACTIONZONE_AREA', 'ACTIONZONE_REGION'}:
		return True
	return False


def _build_window_override(window):
	override = {}
	override['window'] = window
	wm = getattr(bpy.context, 'window_manager', None)
	if wm is not None:
		override['window_manager'] = wm
	screen = getattr(window, "screen", None)
	if not screen:
		return None
	override['screen'] = screen
	workspace = getattr(window, "workspace", None)
	if workspace is not None:
		override['workspace'] = workspace
	area = next((a for a in screen.areas if a.type not in {'EMPTY', 'OUTLINER'}), None)
	if area is None and screen.areas:
		area = screen.areas[0]
	if area is None:
		return None
	override['area'] = area
	region = next((r for r in area.regions if r.type in {'WINDOW', 'HEADER'}), None)
	if region is None and area.regions:
		region = area.regions[-1]
	if region is None:
		return None
	override['region'] = region
	view_layer = getattr(window, 'view_layer', None)
	if view_layer is None:
		view_layer = getattr(bpy.context, 'view_layer', None)
	if view_layer is not None:
		override['view_layer'] = view_layer
	scene = getattr(window, 'scene', None)
	if scene is None:
		scene = getattr(bpy.context, 'scene', None)
	if scene is not None:
		override['scene'] = scene
	override['blend_data'] = bpy.data
	return override


def _ensure_event_watchers() -> None:
	if not _event_watchers_enabled:
		return
	wm = getattr(bpy.context, "window_manager", None)
	if wm is None:
		return
	now = time.monotonic()
	for window in wm.windows:
		pointer = window.as_pointer()
		if pointer in _event_watcher_windows:
			continue
		override = _build_window_override(window)
		if not override:
			continue
		try:
			bpy.ops.wm.wakatime_event_watcher(override, 'INVOKE_DEFAULT')
			_event_watcher_last_error.pop(pointer, None)
		except Exception as exc:
			last_logged = _event_watcher_last_error.get(pointer, 0.0)
			if now - last_logged > 10.0:
				log(WARNING, "Unable to start event watcher: {}", exc)
				_event_watcher_last_error[pointer] = now


def _stop_event_watchers() -> None:
	global _event_watchers_enabled
	_event_watchers_enabled = False
	_event_watcher_windows.clear()
	_event_watcher_last_error.clear()


def _record_save_activity(filepath: Optional[str]) -> None:
	global _last_activity_monotonic
	_last_activity_monotonic = time.monotonic()
	if filepath:
		filename = os.path.basename(filepath)
		timeline_log_event(f"file saved {filename}")
	else:
		timeline_log_event("file saved")
	if _update_tracking_state():
		_enqueue_current_file(is_write=True)


@persistent
def _operator_post_handler(context):
	try:
		operator = getattr(context, "active_operator", None)
	except Exception:
		operator = None
	try:
		_record_operator_activity(operator)
	except Exception:
		pass


@persistent
def _depsgraph_update_handler(_scene, _depsgraph):
	global _last_depsgraph_ping
	now = time.monotonic()
	if now - _last_depsgraph_ping < 0.5:
		return
	_last_depsgraph_ping = now
	try:
		_record_general_activity()
	except Exception:
		pass


@persistent
def _save_post_handler(filepath):
	try:
		_record_save_activity(filepath)
	except Exception:
		pass


def _register_handlers() -> None:
	global _handlers_registered, _missing_handlers
	if _handlers_registered:
		return
	for name, handler in (
		("operator_post", _operator_post_handler),
		("depsgraph_update_post", _depsgraph_update_handler),
		("save_post", _save_post_handler),
	):
		handler_list = _handler_list(name)
		if handler_list is None:
			if name not in _missing_handlers:
				log(WARNING, "Blender handler '{}' unavailable; related tracking features disabled.", name)
				_missing_handlers.add(name)
			continue
		if handler not in handler_list:
			handler_list.append(handler)
	_handlers_registered = True


def _unregister_handlers() -> None:
	global _handlers_registered
	for name, handler in (
		("operator_post", _operator_post_handler),
		("depsgraph_update_post", _depsgraph_update_handler),
		("save_post", _save_post_handler),
	):
		handler_list = _handler_list(name)
		if not handler_list:
			continue
		try:
			if handler in handler_list:
				handler_list.remove(handler)
		except Exception:
			pass
	_handlers_registered = False


def _start_heartbeat_queue() -> None:
	global _heartbeat_queue
	if _heartbeat_queue is None:
		queue = HeartbeatQueue(_addon_version)
		queue.start()
		_heartbeat_queue = queue


def _stop_heartbeat_queue() -> None:
	global _heartbeat_queue
	if _heartbeat_queue is None:
		return
	try:
		_heartbeat_queue.shutdown()
		_heartbeat_queue.join(timeout=1.0)
	except Exception:
		pass
	_heartbeat_queue = None


def _sync_timer_fn():
	global _sync_status
	settings.ensure_offline_defaults()
	success, message = sync_offline_activity()
	timestamp = time.strftime("%H:%M:%S")
	if success:
		_sync_status = f"Sync {timestamp}"
	else:
		_sync_status = "Sync Error"
		print(f"[Wakatime] {message}")
	_refresh_statusbar_cache()
	return 30.0


def _start_sync_timer() -> None:
	global _sync_timer_registered
	if not _sync_timer_registered:
		bpy.app.timers.register(_sync_timer_fn, first_interval=5.0, persistent=True)
		_sync_timer_registered = True


def _stop_sync_timer() -> None:
	global _sync_timer_registered
	if _sync_timer_registered:
		try:
			bpy.app.timers.unregister(_sync_timer_fn)
		except Exception:
			pass
		_sync_timer_registered = False


def _statusbar_timer_fn():
	prefs = WakatimeAddonPreferences.get_prefs()
	interval = 5.0
	if prefs and hasattr(prefs, "statusbar_refresh_interval"):
		try:
			interval = _resolve_pref_float(prefs.statusbar_refresh_interval, 1.0, interval)
		except Exception:
			if "statusbar_refresh_interval" not in _pref_warnings:
				log(WARNING, "Falling back to default status bar refresh interval.")
				_pref_warnings.add("statusbar_refresh_interval")
	_refresh_statusbar_cache()
	return interval


def _tracking_timer_fn():
	_update_tracking_state()
	_refresh_statusbar_cache()
	_ensure_event_watchers()
	return 1.0


class WAKATIME_OT_EventWatcher(bpy.types.Operator):
	bl_idname = "wm.wakatime_event_watcher"
	bl_label = "WakaTime Event Watcher"
	bl_options = {'INTERNAL'}

	_timer = None
	_window_pointer = None
	_last_activity_ping = 0.0
	_last_mouse = (-1, -1)

	def execute(self, context):
		if not context.window:
			return {'CANCELLED'}
		if not _event_watchers_enabled:
			return {'CANCELLED'}
		wm = context.window_manager
		self._window_pointer = context.window.as_pointer()
		try:
			self._timer = wm.event_timer_add(1.0, window=context.window)
		except Exception:
			self._window_pointer = None
			return {'CANCELLED'}
		wm.modal_handler_add(self)
		_event_watcher_windows.add(self._window_pointer)
		self._last_activity_ping = time.monotonic()
		self._last_mouse = (-1, -1)
		return {'RUNNING_MODAL'}

	def invoke(self, context, _event):
		return self.execute(context)

	def modal(self, context, event):
		if not _event_watchers_enabled:
			self.cancel(context)
			return {'CANCELLED'}
		if not context.window:
			self.cancel(context)
			return {'CANCELLED'}
		if context.window.as_pointer() != self._window_pointer:
			self.cancel(context)
			return {'CANCELLED'}
		if event.type == 'TIMER':
			return {'PASS_THROUGH'}
		if not _should_watch_event(event):
			return {'PASS_THROUGH'}
		if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
			current = (event.mouse_x, event.mouse_y)
			if current == self._last_mouse:
				return {'PASS_THROUGH'}
			self._last_mouse = current
		now = time.monotonic()
		if now - self._last_activity_ping < 0.1:
			return {'PASS_THROUGH'}
		self._last_activity_ping = now
		_record_general_activity()
		return {'PASS_THROUGH'}

	def cancel(self, context):
		wm = getattr(context, "window_manager", None)
		if wm and self._timer is not None:
			try:
				wm.event_timer_remove(self._timer)
			except Exception:
				pass
		self._timer = None
		if self._window_pointer in _event_watcher_windows:
			_event_watcher_windows.discard(self._window_pointer)
		self._window_pointer = None
		return {'CANCELLED'}


def _draw_statusbar(self, _context):
	prefs = WakatimeAddonPreferences.get_prefs()
	if not prefs or not prefs.enable_statusbar:
		return
	layout = self.layout
	row = layout.row(align=True)
	row.operator('ui.wakatime_blender_preferences', text=_cached_time_display, icon='TIME')
	dash_op = row.operator('wm.url_open', text='', icon='URL')
	dash_op.url = str(settings.api_server_url().rstrip('/'))


class WakatimeAddonPreferences(bpy.types.AddonPreferences):
	bl_idname = __package__ if __package__ else 'wakatime_blender'

	def _update_api_key(self, _context):
		from . import settings as _s
		if self.api_key.strip():
			_s.set_api_key(self.api_key)
		else:
			print("[Wakatime] Warning: Empty API key provided")

	def _update_api_server(self, _context):
		from . import settings as _s
		if self.api_server_url.strip() and self.api_server_url.startswith(("http://", "https://")):
			_s.set_api_server_url(self.api_server_url)
		else:
			print(f"[Wakatime] Warning: Invalid API server URL: {self.api_server_url}")

	enable_statusbar = bpy.props.BoolProperty(name="Show Status Bar Time", default=True, description="Display tracked Wakatime time in Blender status bar")
	statusbar_refresh_interval = bpy.props.IntProperty(name="Refresh Interval (s)", default=1, min=1, max=600, description="How often to refresh the status bar tracked time")
	api_key = bpy.props.StringProperty(name="API Key", default="", update=_update_api_key, description="Your Wakatime API key")
	api_server_url = bpy.props.StringProperty(name="API Server URL", default="", update=_update_api_server, description="Wakatime API server URL")

	def _load_settings(self):
		try:
			from . import settings as _s
			_s.load()
			_s.ensure_offline_defaults()
			self.api_key = _s.api_key()
			stored_url = _s.get("api_server_url", "")
			if stored_url == _s.DEFAULT_API_SERVER_URL:
				stored_url = ""
			self.api_server_url = stored_url
		except Exception as exc:
			print(f"[Wakatime] Error loading settings: {exc}")

	@staticmethod
	def get_prefs():
		addon_name = __package__ if __package__ else 'wakatime_blender'
		addon = bpy.context.preferences.addons.get(addon_name, None)
		if addon and hasattr(addon, 'preferences'):
			return addon.preferences
		return None

	def draw(self, _context):
		self._load_settings()
		layout = self.layout
		col = layout.column(align=True)

		box = col.box()
		box.label(text="Status Bar Settings", icon="STATUSBAR")
		box.prop(self, 'enable_statusbar')
		box.prop(self, 'statusbar_refresh_interval')

		col.separator()

		api_box = col.box()
		api_box.label(text="Wakatime API Settings", icon="WORLD")
		api_box.prop(self, 'api_key', icon="KEY_HLT")
		api_box.prop(self, 'api_server_url', icon="URL")

		col.separator()
		info = col.box()
		info.label(text="Tracking pauses if Blender is idle (30s), unfocused, or unsaved.", icon="INFO")
		info.label(text="Timeline log folder: {}".format(TIMELINE_DIR), icon="FILE_SCRIPT")
		col.label(text='Click the time in status bar to open dashboard.', icon="INFO")


classes = [
	# Event watcher must register before UI classes so ops available during setup.
	WAKATIME_OT_EventWatcher,
	PreferencesDialog,
	ForceWakatimeDownload,
	WAKATIME_PT_TrackedTime,
	WakatimeAddonPreferences,
]


def register():
	global _statusbar_timer, _tracking_timer, _cached_time_display, _last_activity_monotonic, _event_watchers_enabled
	for cls in reversed(classes):
		if hasattr(bpy.types, cls.__name__):
			try:
				bpy.utils.unregister_class(cls)
			except Exception:
				pass
	for cls in classes:
		try:
			bpy.utils.register_class(cls)
		except ValueError:
			pass
	panel_register()
	_last_activity_monotonic = time.monotonic()
	_cached_time_display = "Tracking paused • Sync idle"

	try:
		prefs = WakatimeAddonPreferences.get_prefs()
		if prefs:
			prefs._load_settings()  # pylint: disable=protected-access
	except Exception:
		pass

	if not _verify_minimum_environment():
		_event_watchers_enabled = False
		_stop_event_watchers()
		_set_tracking_state(False, "disabled")
		_refresh_statusbar_cache()
		return
	_event_watchers_enabled = True
	_start_heartbeat_queue()
	_register_handlers()
	_update_tracking_state()
	_refresh_statusbar_cache()
	if _tracking_reason == "unsaved":
		timeline_log_event("tracking paused (unsaved)")

	try:
		bpy.types.STATUSBAR_HT_header.append(_draw_statusbar)
	except Exception:
		pass
	_statusbar_timer = bpy.app.timers.register(_statusbar_timer_fn, first_interval=1.0, persistent=True)
	_tracking_timer = bpy.app.timers.register(_tracking_timer_fn, first_interval=1.0, persistent=True)
	_ensure_event_watchers()
	_start_sync_timer()


def unregister():
	global _statusbar_timer, _tracking_timer, _cached_time_display
	_stop_event_watchers()
	_stop_sync_timer()
	if _statusbar_timer is not None:
		try:
			bpy.app.timers.unregister(_statusbar_timer_fn)
		except Exception:
			pass
		_statusbar_timer = None
	if _tracking_timer is not None:
		try:
			bpy.app.timers.unregister(_tracking_timer_fn)
		except Exception:
			pass
		_tracking_timer = None
	_unregister_handlers()
	_stop_heartbeat_queue()
	try:
		bpy.types.STATUSBAR_HT_header.remove(_draw_statusbar)
	except Exception:
		pass
	panel_unregister()
	for cls in reversed(classes):
		try:
			bpy.utils.unregister_class(cls)
		except Exception:
			pass
	_cached_time_display = "Tracking paused • Sync idle"
