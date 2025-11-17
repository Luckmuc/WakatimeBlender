import os
import textwrap
from configparser import ConfigParser
from functools import lru_cache
from typing import Any, Callable, Optional, TypeVar

USER_HOME = os.path.expanduser("~")
PLUGIN_DIR = os.path.dirname(os.path.realpath(__file__))
CLI_WRAPPER_PATH = os.path.join(PLUGIN_DIR, "cli_wrapper.py")
RESOURCES_DIR = os.path.join(USER_HOME, ".wakatime")
API_CLIENT_DIR = os.path.join(RESOURCES_DIR, "wakatime-runtime")
# using the legacy python client to avoid managing platform-specific binaries
API_CLIENT_URL = "https://github.com/wakatime/legacy-python-cli/archive/master.zip"

# Default API server URL
DEFAULT_API_SERVER_URL = "https://api.wakatime.com/"
DEFAULT_SYNC_OFFLINE_ACTIVITY = "100"

LEGACY_CLIENT_SUBDIR = "legacy-python-cli-master"
LEGACY_CLIENT_ALT_SUBDIRS = ("legacy-python-cli-main",)
MODERN_CLIENT_SUBDIRS = ("wakatime-master", "wakatime-main")
EXPECTED_API_CLIENT_PATH = os.path.join(
    API_CLIENT_DIR, LEGACY_CLIENT_SUBDIR, "wakatime", "cli.py"
)

COMPAT_MARKER = "# wakatime_blender collections compatibility shim"


def _preferred_client_paths():
    """Return candidate paths for the wakatime CLI in priority order."""
    candidates = [EXPECTED_API_CLIENT_PATH]
    for subdir in LEGACY_CLIENT_ALT_SUBDIRS:
        candidates.append(os.path.join(API_CLIENT_DIR, subdir, "wakatime", "cli.py"))
    for subdir in MODERN_CLIENT_SUBDIRS:
        candidates.append(os.path.join(API_CLIENT_DIR, subdir, "wakatime", "cli.py"))
    return candidates


@lru_cache(maxsize=1)
def api_client_path() -> Optional[str]:
    """Locate the wakatime CLI that ships with the add-on download."""
    for path in _preferred_client_paths():
        if os.path.isfile(path):
            return path

    if os.path.isdir(API_CLIENT_DIR):
        for root, _dirs, files in os.walk(API_CLIENT_DIR):
            if "cli.py" in files and os.path.basename(root) == "wakatime":
                return os.path.join(root, "cli.py")
    return None


def reset_api_client_path_cache() -> None:
    """Clear the cached CLI path after download or removal."""
    api_client_path.cache_clear()


def ensure_cli_compatibility() -> None:
    """Create a sitecustomize module to patch collections aliases for legacy CLI."""
    cli_path = api_client_path()
    if not cli_path:
        return

    compat_dir = os.path.dirname(cli_path)
    compat_file = os.path.join(compat_dir, "sitecustomize.py")

    if os.path.isfile(compat_file):
        try:
            with open(compat_file, "r", encoding="utf-8") as fh:
                existing = fh.read()
            if COMPAT_MARKER in existing and "Callable" in existing:
                return
        except Exception:
            return

    shim = textwrap.dedent(
        f"""{COMPAT_MARKER}
try:
    import collections as _collections
    import collections.abc as _abc
    for _attr in ("Mapping", "MutableMapping", "Sequence", "MutableSequence", "MutableSet", "Callable"):
        if not hasattr(_collections, _attr):
            setattr(_collections, _attr, getattr(_abc, _attr))
except Exception:
    pass
"""
    )

    try:
        with open(compat_file, "w", encoding="utf-8") as fh:
            fh.write(shim)
    except Exception as exc:
        print(f"[Wakatime] Warning: Could not create compatibility shim at {compat_file}: {exc}")


def cli_wrapper_path() -> str:
    return CLI_WRAPPER_PATH

def _strip_heartbeats_suffix(url: str) -> str:
    if not url:
        return ""
    cleaned = url.strip()
    while cleaned.endswith('/'):
        cleaned = cleaned[:-1]
    suffixes = (
        "/users/current/heartbeats.bulk",
        "/heartbeats.bulk",
        "/heartbeats",
    )
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return cleaned


def _normalize_api_v1_base(raw_url: str) -> str:
    base = _strip_heartbeats_suffix(raw_url or DEFAULT_API_SERVER_URL)
    base = base.rstrip('/')
    if base.endswith('/api/v1') or base.endswith('/v1'):
        return base
    if base.endswith('/api'):
        return base + '/v1'
    return base + '/api/v1'


def api_v1_base_url() -> str:
    """Return the normalized base URL for Wakatime's v1 API."""
    return _normalize_api_v1_base(api_server_url())


def api_heartbeats_url_for_value(raw_url: str) -> str:
    base = _normalize_api_v1_base(raw_url).rstrip('/')
    if base.endswith('/users/current/heartbeats.bulk'):
        return base
    if base.endswith('/users/current'):
        return base + '/heartbeats.bulk'
    return base + '/users/current/heartbeats.bulk'


def api_heartbeats_url() -> str:
    """Return the full v1 heartbeats endpoint used by the CLI."""
    endpoint = api_heartbeats_url_for_value(api_server_url())
    _ensure_api_url_synced(endpoint)
    return endpoint


def _ensure_api_url_synced(expected_endpoint: str) -> None:
    global _api_url_synced
    if _api_url_synced:
        return
    if not _loaded:
        load()
    try:
        current = _cfg.get(_section, "api_url", fallback="").strip()
    except Exception:
        current = ""
    if current != expected_endpoint:
        try:
            _cfg.set(_section, "api_url", expected_endpoint)
            save()
        except Exception:
            pass
    _api_url_synced = True
# default wakatime config for legacy python client
FILENAME = os.path.join(USER_HOME, ".wakatime.cfg")
# default section in wakatime config
_section = "settings"

_cfg = ConfigParser()
_cfg.optionxform = str
if not _cfg.has_section(_section):
    _cfg.add_section(_section)
if not _cfg.has_option(_section, "debug"):
    _cfg.set(_section, "debug", str(False))

_loaded = False
_api_url_synced = False


def _get_blender_prefs():
    """Get Blender addon preferences if available"""
    try:
        import bpy
        addon_name = __package__ if __package__ else 'wakatime_blender'
        addon = bpy.context.preferences.addons.get(addon_name, None)
        if addon and hasattr(addon, 'preferences'):
            return addon.preferences
    except:
        pass
    return None


def _get_pref_value(prefs, attr_name, default=None):
    """Safely get a preference value, handling _PropertyDeferred objects"""
    try:
        if hasattr(prefs, attr_name):
            value = getattr(prefs, attr_name)
            
            # Handle _PropertyDeferred objects by checking if they contain a _PropertyDeferred signature
            str_value = str(value)
            if '_PropertyDeferred' in str_value:
                # This is a _PropertyDeferred object, extract the default value
                try:
                    # Look for the 'default' key in the string representation
                    import re
                    match = re.search(r"'default':\s*'([^']*)'", str_value)
                    if match:
                        default_value = match.group(1)
                        if default_value and default_value != 'None':
                            return default_value.strip()
                except:
                    pass
                # If we can't extract the default, return the fallback
                return default
            else:
                # Normal value, convert to string and strip
                if str_value and str_value != 'None':
                    return str_value.strip()
        return default
    except:
        return default


def _enforce_offline_defaults() -> None:
    changed = False
    try:
        current_offline = ""
        if _cfg.has_option(_section, "offline"):
            current_offline = _cfg.get(_section, "offline").strip().lower()
        if current_offline != "true":
            _cfg.set(_section, "offline", "true")
            changed = True

        current_sync = ""
        if _cfg.has_option(_section, "sync_offline_activity"):
            current_sync = _cfg.get(_section, "sync_offline_activity").strip()
        if not current_sync:
            _cfg.set(_section, "sync_offline_activity", DEFAULT_SYNC_OFFLINE_ACTIVITY)
            changed = True
        else:
            try:
                if current_sync.lower() == "none" or int(current_sync) <= 0:
                    raise ValueError
            except ValueError:
                _cfg.set(_section, "sync_offline_activity", DEFAULT_SYNC_OFFLINE_ACTIVITY)
                changed = True
    except Exception:
        return

    if changed:
        try:
            save()
        except Exception:
            pass


def load():
    global _loaded
    global _api_url_synced
    try:
        # Try to read the config file, but handle BOM issues
        with open(FILENAME, 'r', encoding='utf-8-sig') as f:
            config_content = f.read()
        
        # Write it back without BOM if it had one
        with open(FILENAME, 'w', encoding='utf-8') as f:
            f.write(config_content)
            
        _cfg.read(FILENAME, "utf-8")
        _loaded = True
        _api_url_synced = False
        _enforce_offline_defaults()
    except Exception as e:
        print(f"[Wakatime] [ERROR] Unable to read {FILENAME}\n{repr(e)}")
        # Continue without config file - we'll use Blender preferences instead
        _loaded = True
    _api_url_synced = False


def save():
    try:
        with open(FILENAME, "w", encoding="utf-8") as out:
            _cfg.write(out)
    except Exception as e:
        print(f"[Wakatime] Warning: Could not save config to {FILENAME}: {e}")
        # Continue anyway - Blender preferences are the primary storage


def set(option: str, value: str) -> None:
    try:
        _cfg.set(_section, option, value)
        save()
    except Exception as e:
        print(f"[Wakatime] Warning: Could not save setting {option}: {e}")
        # Continue anyway - Blender preferences are the primary storage


def set_api_server_url(raw_url: str) -> None:
    global _api_url_synced
    sanitized = (raw_url or "").strip()
    base_value = _strip_heartbeats_suffix(sanitized or DEFAULT_API_SERVER_URL) or DEFAULT_API_SERVER_URL.rstrip('/')
    set("api_server_url", base_value)
    endpoint = api_heartbeats_url_for_value(base_value)
    set("api_url", endpoint)
    ensure_offline_defaults()
    _api_url_synced = True
    prefs = _get_blender_prefs()
    if prefs and hasattr(prefs, "api_server_url"):
        try:
            if getattr(prefs, "api_server_url") != sanitized:
                setattr(prefs, "api_server_url", sanitized)
        except Exception:
            pass


def ensure_offline_defaults() -> None:
    if not _loaded:
        load()
        return
    _enforce_offline_defaults()


def get(option: str, default: Any = None) -> str:
    # First try to get from Blender preferences
    prefs = _get_blender_prefs()
    if prefs:
        if option == "api_key":
            value = _get_pref_value(prefs, 'api_key')
            if value:
                return value
        elif option == "api_server_url":
            value = _get_pref_value(prefs, 'api_server_url')
            if value:
                return value
        
    
    # Fall back to config file
    if not _loaded:
        load()
    return _cfg.get(_section, option, fallback=default)

def api_server_url() -> str:
    # Prefer Blender preferences when explicitly set to a custom value
    prefs = _get_blender_prefs()
    pref_value = None
    if prefs:
        pref_value = _get_pref_value(prefs, 'api_server_url')
        if pref_value:
            pref_value = _strip_heartbeats_suffix(pref_value.strip())

    # Always read config for backward compatibility and operator-based updates
    global _loaded
    if not _loaded:
        load()

    cfg_value = _strip_heartbeats_suffix(_cfg.get(_section, "api_server_url", fallback="").strip())
    if not cfg_value:
        cfg_value = _strip_heartbeats_suffix(_cfg.get(_section, "api_url", fallback="").strip())  # Some configs use api_url instead
    if cfg_value:
        cfg_value = cfg_value.strip()

    # If the blender preference is customized (non-default), use it
    if pref_value and pref_value not in {"", DEFAULT_API_SERVER_URL}:
        return pref_value

    # Otherwise prefer the config value when available
    if cfg_value:
        return cfg_value

    # Fall back to Blender preference even if default, then to global default
    if pref_value:
        return pref_value

    return DEFAULT_API_SERVER_URL


def get_bool(option: str) -> bool:
    return get(option, "").lower() in {"y", "yes", "t", "true", "1"}


_T = TypeVar("_T", bound=Any)


def parse(
    option: str, transform: Callable[[str], _T], default: Optional[_T] = None
) -> Optional[_T]:
    try:
        return transform(get(option))
    except Exception:
        return default


def debug() -> bool:
    return get_bool("debug")


def api_key() -> str:
    # First try to get from Blender preferences
    prefs = _get_blender_prefs()
    if prefs:
        value = _get_pref_value(prefs, 'api_key')
        if value:
            return value
    
    # Fall back to config file
    return get("api_key", "")


def set_api_key(new_key: str) -> None:
    set("api_key", new_key)
    ensure_offline_defaults()


def sync_offline_activity_amount() -> str:
    if not _loaded:
        load()
    try:
        value = _cfg.get(_section, "sync_offline_activity", fallback=DEFAULT_SYNC_OFFLINE_ACTIVITY).strip()
    except Exception:
        value = DEFAULT_SYNC_OFFLINE_ACTIVITY
    if not value:
        return DEFAULT_SYNC_OFFLINE_ACTIVITY
    try:
        if value.lower() == "none" or int(value) <= 0:
            raise ValueError
    except ValueError:
        return DEFAULT_SYNC_OFFLINE_ACTIVITY
    return value
