import datetime
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from queue import Empty, Queue
from subprocess import PIPE, Popen, STDOUT
from typing import List, Optional, Tuple

import bpy
from . import settings
from .log import DEBUG, ERROR, INFO, log
from .state_store import load_tracked_seconds, save_tracked_seconds
from .utils import u


@lru_cache(maxsize=128)
def simple_project_name(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0].strip() or "Blender"
    if not base.lower().endswith("[blender]"):
        name = f"{base} [blender]"
    else:
        name = base
    log(INFO, "Project name in Wakatime: {}", name)
    return name


@dataclass
class HeartBeat:
    entity: str
    project: str
    timestamp: float
    is_write: bool = False


class HeartbeatQueue(threading.Thread):
    POLL_INTERVAL = 1

    def __init__(self, version: str) -> None:
        super().__init__()
        self.daemon = True
        self._version = version
        self._queue = Queue()
        self._last_hb: Optional[HeartBeat] = None
        self._lock = threading.Lock()
        self._running = True
        self._total_tracked_seconds = 0
        self._last_tracked_time = None
        self._current_day = datetime.date.today()
        restored_seconds, restored = load_tracked_seconds(self._current_day)
        if restored:
            self._total_tracked_seconds = restored_seconds
            log(INFO, "Restored tracked seconds for {}: {}s", self._current_day, restored_seconds)
        else:
            save_tracked_seconds(self._current_day, 0)
        self._last_access_wall = datetime.datetime.now()

    def get_tracked_time(self):
        return self._total_tracked_seconds

    def get_tracked_time_live(self):
        """Get live tracked time including current active session"""
        now = datetime.datetime.now()
        base_time = self._total_tracked_seconds
        
        # Add time from current active session if we're actively working
        if self._last_tracked_time is not None:
            delta = (now - self._last_tracked_time).total_seconds()
            # If less than 2 minutes since last activity, count it as active time
            if 0 < delta < 120:
                base_time += int(delta)
        
        return base_time

    def _enough_time_passed(self, now, is_write):
        # More responsive throttling for live tracking:
        # - Writes (saves): 2 seconds
        # - Regular activity: 30 seconds (reduced from 60 for better live tracking)
        threshold = 2 if is_write else 30
        return self._last_hb is None or (now - self._last_hb.timestamp > threshold)

    def enqueue(self, filename: str, is_write=False):
        log(DEBUG, "enqueue called - file: {} (write: {})", filename, is_write)
        timestamp = time.time()
        last_file = self._last_hb.entity if self._last_hb is not None else ""
        
        # Always update tracking time for live display, even if we don't send a heartbeat
        now = datetime.datetime.now()
        
        # Daily reset check
        today = datetime.date.today()
        if today != self._current_day:
            self._total_tracked_seconds = 0
            self._current_day = today
            save_tracked_seconds(self._current_day, 0)
            log(INFO, "Daily tracked time reset ({}).", today)
            
        # Update live time tracking
        if self._last_tracked_time is not None:
            delta = (now - self._last_tracked_time).total_seconds()
            if 0 < delta < 600:  # Ignore huge gaps (idle, sleep, etc.)
                self._total_tracked_seconds += int(delta)
                save_tracked_seconds(self._current_day, self._total_tracked_seconds)
                log(DEBUG, "Tracked time updated: +{}s (total: {}s)", int(delta), self._total_tracked_seconds)
        self._last_tracked_time = now
        
        # Check if we should send an actual heartbeat
        if not filename:
            log(DEBUG, "Skipping heartbeat: empty filename")
            return
            
        if filename == last_file and not self._enough_time_passed(timestamp, is_write):
            log(DEBUG, "Heartbeat throttled for {} (write: {})", filename, is_write)
            return
            
        project_name = simple_project_name(filename)
        self._last_hb = HeartBeat(filename, project_name, timestamp, is_write)
        self._queue.put_nowait(self._last_hb)
        log(DEBUG, "Heartbeat queued for {} (queue size: {})", project_name, self._queue.qsize())
        save_tracked_seconds(self._current_day, self._total_tracked_seconds)

    def shutdown(self):
        self._stop()
        save_tracked_seconds(self._current_day, self._total_tracked_seconds)
        self._queue.put_nowait(None)

    @property
    def running(self):
        with self._lock:
            return self._running

    def _stop(self):
        with self._lock:
            self._running = False

    def _send_to_wakatime(
        self, heartbeat: HeartBeat, extra_heartbeats: Optional[List[HeartBeat]] = None
    ):
        # Check if API key is configured
        api_key = settings.api_key()
        if not api_key:
            log(ERROR, "Wakatime API key not configured. Please set your API key in preferences.")
            return
            
        # Check if API server URL is valid
        api_url = settings.api_server_url().rstrip("/")
        if not api_url or not api_url.startswith(("http://", "https://")):
            log(ERROR, "Invalid Wakatime API server URL: {}. Please check your configuration.", api_url)
            return
            
        client_path = settings.api_client_path()
        if not client_path:
            log(
                ERROR,
                "Wakatime CLI not found. Open Preferences > Force Sync to download the runtime.",
            )
            return

        heartbeats_url = settings.api_heartbeats_url()
        sync_amount = settings.sync_offline_activity_amount()
        settings.ensure_offline_defaults()

        ua = f"blender/{bpy.app.version_string.split()[0]} blender-wakatime/{self._version}"
        wrapper_path = settings.cli_wrapper_path()

        cmd = [
            sys.executable,
            wrapper_path,
            client_path,
            "--entity",
            heartbeat.entity,
            "--time",
            f"{heartbeat.timestamp:f}",
            "--plugin",
            ua,
            "--api-url",
            heartbeats_url
        ]
        cmd.extend(["--sync-offline-activity", sync_amount])
        # Always send as explicit project name
        cmd.extend(["--project", heartbeat.project])
        if heartbeat.is_write:
            cmd.append("--write")
        if settings.debug():
            cmd.append("--verbose")
        if extra_heartbeats:
            cmd.append("--extra-heartbeats")
            stdin = PIPE
        else:
            stdin = None
        log(DEBUG, " ".join(cmd))
        try:
            process = Popen(cmd, stdin=stdin, stdout=PIPE, stderr=STDOUT)
            inp = None
            if extra_heartbeats:
                inp = "{0}\n".format(
                    json.dumps([hb.__dict__ for hb in extra_heartbeats])
                )
                inp = inp.encode("utf-8")
            output, err = process.communicate(input=inp)
            output = u(output)
            retcode = process.poll()
            if (not retcode or retcode == 102) and not output:
                log(DEBUG, "Heartbeat sent successfully")
            elif retcode == 104:  # wrong API key
                log(ERROR, "Invalid Wakatime API key. Please check your API key in preferences.")
                log(ERROR, "You can get your API key from: https://wakatime.com/api-key")
                settings.set("api_key", "")
            elif retcode == 103:  # config file error
                log(ERROR, "Wakatime configuration error. Please check your API key and server URL.")
            elif retcode == 105:  # timeout error
                log(ERROR, "Wakatime API timeout. Please check your internet connection and API server URL.")
            else:
                log(ERROR, "Wakatime API error (code: {})", retcode)
            if retcode and retcode != 102:
                log(
                    DEBUG if retcode == 102 else ERROR,
                    "wakatime-core exited with status: {}",
                    retcode,
                )
            if output:
                log(ERROR, "wakatime-core output: {}", u(output))
        except FileNotFoundError:
            log(ERROR, "Wakatime CLI not found. Please ensure the wakatime client is properly installed.")
        except Exception as e:
            log(ERROR, "Failed to send heartbeat to Wakatime: {}", str(e))

    def run(self):
        log(INFO, "Wakatime heartbeat queue started")
        while self.running:
            time.sleep(self.POLL_INTERVAL)
            
            # Check API key configuration
            if not settings.api_key():
                # Only log this occasionally to avoid spam
                if hasattr(self, '_last_api_key_warning'):
                    if time.time() - self._last_api_key_warning < 300:  # 5 minutes
                        continue
                self._last_api_key_warning = time.time()
                log(ERROR, "Wakatime API key not configured. Please set your API key in Blender preferences.")
                continue
                
            try:
                heartbeat = self._queue.get_nowait()
                log(DEBUG, "Processing heartbeat from queue")
            except Empty:
                continue
            if heartbeat is None:
                log(INFO, "Wakatime heartbeat queue stopping")
                return
            extra_heartbeats = []
            try:
                while True:
                    extra = self._queue.get_nowait()
                    if extra is None:
                        self._stop()
                        break
                    extra_heartbeats.append(extra)
            except Empty:
                pass
            
            log(DEBUG, "Sending heartbeat: {} -> {}", heartbeat.entity, heartbeat.project)
            try:
                self._send_to_wakatime(heartbeat, extra_heartbeats)
            except Exception as e:
                log(ERROR, "Failed to process heartbeat: {}", str(e))


def sync_offline_activity() -> Tuple[bool, str]:
    """Sync offline activity with the configured Wakatime server."""
    api_key = settings.api_key()
    if not api_key:
        return False, "API key not configured."

    client_path = settings.api_client_path()
    if not client_path:
        from .wakatime_downloader import WakatimeDownloader

        downloader = WakatimeDownloader()
        downloader.run()
        client_path = settings.api_client_path()
        status = downloader.status()
        if status:
            log(INFO, status[1])
        if not client_path:
            return False, "Unable to locate wakatime CLI for offline sync."

    settings.ensure_cli_compatibility()
    settings.ensure_offline_defaults()

    heartbeats_url = settings.api_heartbeats_url()
    sync_amount = settings.sync_offline_activity_amount()
    wrapper_path = settings.cli_wrapper_path()

    ua = "blender/{0} blender-wakatime/{1}".format(
        bpy.app.version_string.split()[0],
        "1.0.0",
    )

    cmd = [
        sys.executable,
        wrapper_path,
        client_path,
        "--api-url",
        heartbeats_url,
        "--sync-offline-activity",
        sync_amount,
        "--plugin",
        ua,
    ]

    if settings.debug():
        cmd.append("--verbose")

    try:
        process = Popen(cmd, stdout=PIPE, stderr=STDOUT)
        output, _err = process.communicate()
        output_text = u(output)
        retcode = process.poll()
        if output_text:
            log(INFO, "Offline sync output: {}", output_text)
        if retcode in (0, 102):
            return True, "Offline activity synced."
        return False, f"Offline sync failed (code {retcode})."
    except Exception as exc:
        return False, f"Offline sync error: {exc}"
