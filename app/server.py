import base64
import copy
import ctypes
import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import smtplib
import ssl
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse, urlsplit, urlunsplit
from urllib import error as urllib_error, request as urllib_request

from modules.event_receiver import EventReceiverModule
from modules.hikvision_sdk import HikvisionSdk
from modules.mqtt_module import MqttModule
from modules.rid_module import RidModule
from modules.s3_receiver import LocalS3Module
from modules.stream_module import StreamModule


APP_NAME = "AERO SYNC"
# Internal credit: jocy.john
FOOTER = "AERO SYNC | Designed & Developed by AERO NEX FZCO | 婕?2025 Aero Nex FZCO. All Rights Reserved. | Contact us : Support@aeronex.ae"
ABOUT_TEXT = "AERO SYNC | Designed & Developed by AERO NEX FZCO | 婕?2025 Aero Nex FZCO. All Rights Reserved. | Contact us : Support@aeronex.ae"
SECRET_MASK = "********"
BASE_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BASE_DIR / "app"
STATIC_DIR = APP_DIR / "static"
DATA_DIR = BASE_DIR / "data"
CONFIG_DIR = BASE_DIR / "config"
DEFAULT_STORAGE_ROOT = BASE_DIR / "syncdata"
LOG_DIR = DATA_DIR / "logs"
CERT_DIR = DATA_DIR / "certs"
RUNTIME_DIR = DATA_DIR / "runtime"
INSTANCE_LOCK_FILE = RUNTIME_DIR / "aero_sync.lock"
SETTINGS_FILE = DATA_DIR / "settings.json"
USERS_FILE = DATA_DIR / "users.json"
LICENSE_FILE = CONFIG_DIR / "license.json"
LEGACY_LICENSE_FILE = DATA_DIR / "license.json"
LICENSE_PUBLIC_KEY_FILE = APP_DIR / "license_public_key.json"
AUDIT_FILE = LOG_DIR / "audit.log"
DIAGNOSTICS_FILE = LOG_DIR / "diagnostics.log"
JSON_LOCK = threading.RLock()
SERVICE_LOCK = threading.RLock()

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
CERT_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

INSTANCE_LOCK_HANDLE = None


def acquire_single_instance_lock():
    global INSTANCE_LOCK_HANDLE
    INSTANCE_LOCK_HANDLE = INSTANCE_LOCK_FILE.open("a+")
    try:
        if os.name == "nt":
            import msvcrt
            INSTANCE_LOCK_HANDLE.seek(0)
            msvcrt.locking(INSTANCE_LOCK_HANDLE.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(INSTANCE_LOCK_HANDLE.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        running_pid = ""
        try:
            INSTANCE_LOCK_HANDLE.seek(0)
            running_pid = (INSTANCE_LOCK_HANDLE.read() or "").strip()
        except OSError:
            # Windows can deny reads from the byte range held by the first instance.
            pass
        detail = f" PID {running_pid}" if running_pid else ""
        print(f"AERO SYNC is already running{detail}. Second instance stopped.")
        sys.exit(2)
    INSTANCE_LOCK_HANDLE.seek(0)
    INSTANCE_LOCK_HANDLE.truncate()
    INSTANCE_LOCK_HANDLE.write(str(os.getpid()))
    INSTANCE_LOCK_HANDLE.flush()

DEFAULT_PORTS = {
    "dashboard_https": 19000,
    "http_redirect": 19001,
    "event_api": 19002,
    "mqtt_broker": 19003,
    "local_s3": 19004,
    "stream_bridge": 19005,
    "internal_api": 19006,
    "dfr": 19007,
}

DEFAULT_PERMISSIONS = {
    "Admin": [
        "dashboard",
        "events",
        "mqtt",
        "rid",
        "media_s3",
        "live_streams",
        "live_map",
        "nvr_sync",
        "dfr_view",
        "dfr_settings",
        "openapi",
        "logs",
        "backup",
        "settings",
        "email",
        "users",
        "reports",
        "license",
    ],
    "Support": ["dashboard", "events", "mqtt", "rid", "media_s3", "live_streams", "live_map", "nvr_sync", "dfr_view", "openapi", "logs"],
    "User": ["dashboard", "events", "mqtt", "rid", "media_s3", "live_streams", "live_map", "dfr_view", "openapi"],
}

DEFAULT_SETTINGS = {
    "storage": {
        "data_root_path": str(DEFAULT_STORAGE_ROOT.resolve()),
        "use_module_subfolders": True,
    },
    "network": {
        "local_ip": "",
        "wan_ip": "",
    },
    "fh2": {
        "mode": "cloud",
        "profiles": {
            "cloud": {},
            "onprem": {},
        },
    },
    "ports": DEFAULT_PORTS,
    "security": {
        "failed_login_limit": 5,
        "session_timeout_minutes": 1440,
        "ssl_mode": "self-signed",
        "custom_cert_path": "",
        "custom_key_path": "",
    },
    "modules": {
        "event_receiver": {
            "allow_unsigned_events": True,
            "event_db_path": str((DATA_DIR / "events.db").resolve()),
            "log_path": str((LOG_DIR / "event_receiver.log").resolve()),
        },
        "mqtt": {
            "host": "127.0.0.1",
            "username": "aeronex",
            "password": "aeronex",
            "topic": "#",
            "capture_log_path": str((LOG_DIR / "mqtt_capture.log").resolve()),
            "dashboard_log_path": str((LOG_DIR / "mqtt_dashboard.log").resolve()),
        },
        "local_s3": {
            "bucket_type": "Self-Hosted S3 Protocol Storage",
            "bucket": "aeronex",
            "access_key": "aeronex",
            "secret_key": "aeronex",
            "endpoint": "",
            "region": "us-east-1",
            "preset_path": "",
            "storage_path": str((DATA_DIR / "storage").resolve()),
            "log_path": str((LOG_DIR / "local_s3.log").resolve()),
        },
        "live_streams": {
            "layout": 4,
            "dwell_seconds": 0,
            "save_path": str((DATA_DIR / "recordings").resolve()),
            "channels": [
                {
                    "channel": i,
                    "name": f"Channel {i:02d}",
                    "rtsp_url": "",
                    "enabled": False,
                    "status": "offline",
                }
                for i in range(1, 21)
            ],
        },
        "map": {
            "mode": "online",
            "online_tile_url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            "offline_tile_path": str((DATA_DIR / "maps" / "tiles").resolve()),
            "default_lat": 25.2048,
            "default_lng": 55.2708,
            "default_zoom": 12,
            "online_timeout_seconds": 90,
            "trail_enabled": True,
            "trail_retention_days": 7,
            "refresh_seconds": 5,
        },
        "nvr_sync": {
            "enabled": False,
            "auto_assign": True,
            "sdk_status": "sdk_not_configured",
            "nvrs": [],
            "mappings": [],
            "sync_log": [],
        },
        "openapi": {
            "enabled": True,
            "active_connection_id": "",
            "connections": [],
        },
        "dfr": {
            "enabled": True,
            "retry_max": 3,
            "common": {
                "fh2_endpoint": "",
                "workflow_uuid": "",
                "organization_key": "",
                "alert_level": 3,
            },
            "projects": [],
            "scylla": {
                "enabled": False,
                "bearer_token": "",
                "default_project_id": "",
            },
            "hikvision": {
                "enabled": False,
                "auth_mode": "none",
                "token": "",
                "default_project_id": "",
                "docks": [],
                "cameras": [],
            },
            "log_path": str((LOG_DIR / "dfr" / "dfr.log").resolve()),
        },
        "email": {
            "enabled": False,
            "smtp_host": "",
            "smtp_port": 587,
            "security": "starttls",
            "username": "",
            "password": "",
            "from_addresses": "",
            "default_recipients": "",
            "cc_recipients": "",
            "bcc_recipients": "",
            "template_subject": "Operation Center Report - {date}",
            "template_body": "Dear Team,\n\nPlease find attached the Operation Center report.\n\nRegards,\nOperation Center",
            "default_attachment_formats": ["csv", "json"],
            "templates": [
                {
                    "id": "daily_operations",
                    "name": "Daily Operations",
                    "from_address": "",
                    "to": "",
                    "cc": "",
                    "bcc": "",
                    "subject": "Operation Center Daily Report - {date}",
                    "body": "Dear Team,\n\nPlease find attached the daily Operation Center report.\n\nRows: {rows}\n\nRegards,\nOperation Center",
                    "section": "all",
                    "device": "",
                    "formats": ["pdf", "xlsx"],
                    "schedule_enabled": False,
                    "schedule_frequency": "daily",
                    "schedule_time": "08:00",
                    "schedule_day": 1,
                    "last_sent_at": "",
                },
                {
                    "id": "support_activity",
                    "name": "Support Activity",
                    "from_address": "",
                    "to": "",
                    "cc": "",
                    "bcc": "",
                    "subject": "Operation Center Support Activity - {date}",
                    "body": "Dear Support Team,\n\nPlease find attached the user activity and system report.\n\nRegards,\nOperation Center",
                    "section": "user activity",
                    "device": "",
                    "formats": ["csv", "json"],
                    "schedule_enabled": False,
                    "schedule_frequency": "daily",
                    "schedule_time": "08:00",
                    "schedule_day": 1,
                    "last_sent_at": "",
                },
            ],
        },
    },
    "roles": DEFAULT_PERMISSIONS,
    "backup": {
        "backup_path": str((DATA_DIR / "backups").resolve()),
        "auto_backup": True,
        "frequency": "daily",
        "retention_days": 2,
        "last_backup_at": "",
    },
    "log_retention": {
        "daily_rotation": True,
        "compress_old_logs": False,
        "module_retention_days": 30,
        "mqtt_capture_retention_days": 30,
        "audit_retention_days": 30,
        "event_db_retention_days": 180,
        "max_log_size_mb": 100,
        "drive_usage_limit_percent": 80,
        "last_cleanup_at": "",
    },
}

sessions = {}
MODULES = {}
INTERNAL_SERVER = None
INTERNAL_THREAD = None
DFR_SERVER = None
DFR_THREAD = None
DFR_WORKER_THREAD = None
DFR_STOP_EVENT = threading.Event()
EMAIL_THREAD = None
LOG_RETENTION_THREAD = None
CPU_LAST = None
NETWORK_LAST = None
HIKVISION_SDK = HikvisionSdk(BASE_DIR)
GPU_CACHE = {"time": 0, "items": []}
RESOURCE_CACHE = {"time": 0, "data": None}
LINE_COUNT_CACHE = {}
RESOURCE_REFRESHING = False


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def startup_trace(message):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "startup_trace.log").open("a", encoding="utf-8", errors="replace") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")
    except Exception:
        pass


def load_json(path, default):
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return merge_defaults(data, default)
        except Exception:
            return default.copy()
    return default.copy()


def write_json_unlocked(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    last_error = None
    for _ in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05)
    raise last_error


def save_json(path, data):
    with JSON_LOCK:
        write_json_unlocked(path, data)


def merge_defaults(data, default):
    if isinstance(default, dict):
        result = dict(default)
        if isinstance(data, dict):
            for key, value in data.items():
                result[key] = merge_defaults(value, default.get(key)) if key in default else value
        return result
    return data if data is not None else default


def masked_for_client(value):
    return SECRET_MASK if value else ""


def mask_client_secrets(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {"password", "secret_key", "rtsp_password", "token", "bearer_token", "organization_key", "user_token"}:
                result[key] = masked_for_client(item)
            else:
                result[key] = mask_client_secrets(item)
        return result
    if isinstance(value, list):
        return [mask_client_secrets(item) for item in value]
    return value


def restore_masked_secret(incoming, current, keys):
    cursor = incoming
    old_cursor = current
    for key in keys[:-1]:
        if not isinstance(cursor, dict) or key not in cursor:
            return
        cursor = cursor.get(key)
        old_cursor = old_cursor.get(key, {}) if isinstance(old_cursor, dict) else {}
    final_key = keys[-1]
    if isinstance(cursor, dict) and cursor.get(final_key) == SECRET_MASK:
        cursor[final_key] = old_cursor.get(final_key, "") if isinstance(old_cursor, dict) else ""


def mask_log_text(message):
    text = str(message)
    for marker in (
        "password",
        "secret_key",
        "rtsp_password",
        "token",
        "bearer_token",
        "organization_key",
        "authorization",
        "x-dfr-token",
    ):
        lowered = text.lower()
        index = lowered.find(marker)
        while index >= 0:
            sep_index = min([pos for pos in (text.find(":", index), text.find("=", index)) if pos >= 0] or [-1])
            if sep_index >= 0 and sep_index - index <= 40:
                end = len(text)
                for sep in (",", "}", "\n"):
                    pos = text.find(sep, sep_index + 1)
                    if pos >= 0:
                        end = min(end, pos)
                text = text[: sep_index + 1] + " " + SECRET_MASK + text[end:]
                lowered = text.lower()
                index = lowered.find(marker, sep_index + len(SECRET_MASK))
            else:
                index = lowered.find(marker, index + len(marker))
    return text


def data_root_paths(root_value):
    try:
        root = Path(root_value).expanduser() if root_value else DATA_DIR
    except Exception:
        root = DATA_DIR
    return {
        ("modules", "event_receiver", "event_db_path"): root / "events" / "events.db",
        ("modules", "event_receiver", "log_path"): root / "events" / "event_receiver.log",
        ("modules", "mqtt", "capture_log_path"): root / "mqtt" / "mqtt_capture.log",
        ("modules", "mqtt", "dashboard_log_path"): root / "mqtt" / "mqtt_dashboard.log",
        ("modules", "local_s3", "storage_path"): root / "s3" / "storage",
        ("modules", "local_s3", "log_path"): root / "s3" / "local_s3.log",
        ("modules", "live_streams", "save_path"): root / "live_streams" / "recordings",
        ("modules", "map", "offline_tile_path"): root / "maps" / "tiles",
        ("modules", "dfr", "log_path"): root / "logs" / "dfr" / "dfr.log",
        ("backup", "backup_path"): root / "backups",
    }


def apply_data_root_paths(data, root_value):
    for keys, target_path in data_root_paths(root_value).items():
        current = data
        for key in keys[:-1]:
            current = current.setdefault(key, {})
        current[keys[-1]] = str(target_path.resolve())
        target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        storage_root = Path(root_value).expanduser() if root_value else DATA_DIR
    except Exception:
        storage_root = DATA_DIR
    for folder in ("config", "users", "certificates", "reports", "email", "logs"):
        (storage_root / folder).mkdir(parents=True, exist_ok=True)


def copy_path_if_missing(source, target):
    source = Path(source)
    target = Path(target)
    if not source.exists() or source.resolve() == target.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, dirs_exist_ok=True)
    elif not target.exists():
        shutil.copy2(source, target)


def migrate_data_root(old_root_value, new_root_value):
    try:
        old_root = Path(old_root_value).expanduser() if old_root_value else DATA_DIR
        new_root = Path(new_root_value).expanduser() if new_root_value else DATA_DIR
    except Exception:
        return
    try:
        if old_root.resolve() == new_root.resolve():
            return
    except Exception:
        pass
    for folder in ("events", "mqtt", "s3", "live_streams", "maps", "backups", "config", "users", "certificates", "reports", "email", "logs", "dfr"):
        copy_path_if_missing(old_root / folder, new_root / folder)
    for source in (old_root / "events.db",):
        copy_path_if_missing(source, new_root / source.name)


def path_can_be_created(path_value, is_file=False):
    if not path_value:
        return False
    try:
        path = Path(path_value).expanduser()
        target = path.parent if is_file else path
        target.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def repair_path_value(current_value, fallback_path, is_file=False):
    if path_can_be_created(current_value, is_file=is_file):
        return str(Path(current_value).expanduser())
    fallback_path = Path(fallback_path)
    target = fallback_path.parent if is_file else fallback_path
    target.mkdir(parents=True, exist_ok=True)
    return str(fallback_path.resolve())


def is_syncdata_from_other_install(path_value):
    if not path_value:
        return False
    try:
        path = Path(path_value).expanduser().resolve()
        return path.name.lower() == "syncdata" and path.parent != BASE_DIR.resolve()
    except Exception:
        return False


def fh2_mode_label(mode):
    return "FH2 On-Prem" if str(mode).lower() == "onprem" else "FH2 Cloud"


def fh2_profile_paths(data, mode):
    root = Path((data.get("storage") or {}).get("data_root_path") or DEFAULT_STORAGE_ROOT).expanduser().resolve()
    key = "onprem" if str(mode).lower() == "onprem" else "cloud"
    base = root / "fh2" / key
    return {
        "event_db_path": str((base / "events" / "events.db").resolve()),
        "event_log_path": str((base / "events" / "event_receiver.log").resolve()),
        "mqtt_capture_log_path": str((base / "mqtt" / "mqtt_capture.log").resolve()),
        "mqtt_dashboard_log_path": str((base / "mqtt" / "mqtt_dashboard.log").resolve()),
    }


def ensure_fh2_profiles(data):
    fh2 = data.setdefault("fh2", {})
    mode = str(fh2.get("mode") or "cloud").strip().lower()
    mode = mode if mode in ("cloud", "onprem") else "cloud"
    fh2["mode"] = mode
    profiles = fh2.setdefault("profiles", {})
    modules = data.setdefault("modules", {})
    current_event = copy.deepcopy(modules.get("event_receiver") or {})
    current_mqtt = copy.deepcopy(modules.get("mqtt") or {})
    for key in ("cloud", "onprem"):
        profile = profiles.setdefault(key, {})
        paths = fh2_profile_paths(data, key)
        if key == mode and not profile:
            profile["event_receiver"] = copy.deepcopy(current_event)
            profile["mqtt"] = copy.deepcopy(current_mqtt)
            migrations = (
                (current_event.get("event_db_path"), paths["event_db_path"]),
                (current_event.get("log_path"), paths["event_log_path"]),
                (current_mqtt.get("capture_log_path"), paths["mqtt_capture_log_path"]),
                (current_mqtt.get("dashboard_log_path"), paths["mqtt_dashboard_log_path"]),
            )
            for source_value, target_value in migrations:
                try:
                    source_path = Path(source_value).expanduser().resolve() if source_value else None
                    target_path = Path(target_value).expanduser().resolve()
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    if source_path and source_path.exists() and source_path.is_file() and not target_path.exists():
                        shutil.copy2(source_path, target_path)
                except Exception:
                    pass
            profile["event_receiver"]["event_db_path"] = paths["event_db_path"]
            profile["event_receiver"]["log_path"] = paths["event_log_path"]
            profile["mqtt"]["capture_log_path"] = paths["mqtt_capture_log_path"]
            profile["mqtt"]["dashboard_log_path"] = paths["mqtt_dashboard_log_path"]
        event_cfg = profile.setdefault("event_receiver", {})
        mqtt_cfg = profile.setdefault("mqtt", {})
        event_cfg.setdefault("allow_unsigned_events", current_event.get("allow_unsigned_events", True))
        event_cfg.setdefault("event_db_path", paths["event_db_path"])
        event_cfg.setdefault("log_path", paths["event_log_path"])
        mqtt_cfg.setdefault("host", current_mqtt.get("host", "127.0.0.1"))
        mqtt_cfg.setdefault("username", current_mqtt.get("username", "aeronex"))
        mqtt_cfg.setdefault("password", current_mqtt.get("password", "aeronex"))
        mqtt_cfg.setdefault("topic", current_mqtt.get("topic", "#"))
        mqtt_cfg.setdefault("capture_log_path", paths["mqtt_capture_log_path"])
        mqtt_cfg.setdefault("dashboard_log_path", paths["mqtt_dashboard_log_path"])
    return fh2


def store_active_fh2_profile(data):
    fh2 = ensure_fh2_profiles(data)
    mode = fh2["mode"]
    profile = fh2["profiles"][mode]
    modules = data.setdefault("modules", {})
    profile["event_receiver"] = copy.deepcopy(modules.get("event_receiver") or {})
    profile["mqtt"] = copy.deepcopy(modules.get("mqtt") or {})


def activate_fh2_profile(data, mode, store_current=True):
    fh2 = ensure_fh2_profiles(data)
    target = str(mode or "cloud").strip().lower()
    if target not in ("cloud", "onprem"):
        raise ValueError("Invalid FH2 mode")
    if store_current:
        store_active_fh2_profile(data)
    fh2["mode"] = target
    profile = fh2["profiles"][target]
    modules = data.setdefault("modules", {})
    modules["event_receiver"] = copy.deepcopy(profile.get("event_receiver") or {})
    modules["mqtt"] = copy.deepcopy(profile.get("mqtt") or {})
    for path_key in ("event_db_path", "log_path"):
        value = modules["event_receiver"].get(path_key)
        if value:
            Path(value).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    for path_key in ("capture_log_path", "dashboard_log_path"):
        value = modules["mqtt"].get(path_key)
        if value:
            Path(value).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return target


def repair_portable_paths(data):
    """Normalize settings shape without overwriting user-selected storage paths."""
    data.setdefault("storage", {})
    requested_data_root = data["storage"].get("data_root_path") or DEFAULT_STORAGE_ROOT
    root_rebased = is_syncdata_from_other_install(requested_data_root) or not path_can_be_created(requested_data_root)
    data["storage"]["data_root_path"] = (
        str(DEFAULT_STORAGE_ROOT.resolve())
        if root_rebased
        else str(Path(requested_data_root).expanduser().resolve())
    )
    data["storage"].setdefault("use_module_subfolders", True)
    fh2 = ensure_fh2_profiles(data)
    activate_fh2_profile(data, fh2["mode"], store_current=False)
    security = data.setdefault("security", {})
    security.setdefault("failed_login_limit", 5)
    security.setdefault("session_timeout_minutes", 60)
    security.setdefault("ssl_mode", "self-signed")
    security.setdefault("custom_cert_path", "")
    security.setdefault("custom_key_path", "")
    modules = data.setdefault("modules", {})
    modules.setdefault("event_receiver", {})
    modules["event_receiver"].pop("fh2_org_token", None)
    modules.setdefault("mqtt", {})
    modules.setdefault("local_s3", {})
    modules.setdefault("live_streams", {})
    modules.setdefault("map", {})
    modules.setdefault("nvr_sync", {})
    modules.setdefault("openapi", {})
    modules["openapi"].setdefault("enabled", True)
    modules["openapi"].setdefault("active_connection_id", "")
    modules["openapi"].setdefault("connections", [])
    # Backward compatibility: migrate the older Cloud/On-Prem profile model.
    legacy_profiles = modules["openapi"].pop("profiles", {}) or {}
    if not modules["openapi"]["connections"] and isinstance(legacy_profiles, dict):
        for profile_name in ("cloud", "onprem"):
            legacy = legacy_profiles.get(profile_name) or {}
            if any(str(legacy.get(k) or "").strip() for k in ("base_url", "user_token", "project_uuid")):
                modules["openapi"]["connections"].append({
                    "id": secrets.token_hex(8),
                    "name": "FH2 Cloud" if profile_name == "cloud" else "FH2 On-Prem",
                    "platform": profile_name,
                    "api_version": "v2",
                    "enabled": True,
                    "base_url": str(legacy.get("base_url") or ""),
                    "user_token": str(legacy.get("user_token") or ""),
                    "project_uuid": str(legacy.get("project_uuid") or ""),
                    "timeout_seconds": int(legacy.get("timeout_seconds") or 30),
                    "verify_ssl": bool(legacy.get("verify_ssl", True)),
                })
    if modules["openapi"]["connections"] and not modules["openapi"].get("active_connection_id"):
        modules["openapi"]["active_connection_id"] = str(modules["openapi"]["connections"][0].get("id") or "")
    for index, profile in enumerate(modules["openapi"]["connections"]):
        profile.setdefault("id", secrets.token_hex(8))
        profile.setdefault("name", f"OpenAPI Connection {index + 1}")
        profile.setdefault("platform", "cloud")
        profile.setdefault("api_version", "v2")
        profile.setdefault("enabled", True)
        profile.setdefault("base_url", "")
        profile.setdefault("user_token", "")
        profile.setdefault("project_uuid", "")
        profile.setdefault("timeout_seconds", 30)
        profile.setdefault("verify_ssl", True)
    modules.setdefault("dfr", {})
    modules["dfr"].setdefault("enabled", True)
    modules["dfr"].setdefault("retry_max", 3)
    modules["dfr"].setdefault("common", {"fh2_endpoint": "", "workflow_uuid": "", "organization_key": "", "alert_level": 3})
    modules["dfr"]["common"].setdefault("workflow_uuid", "")
    modules["dfr"]["common"].setdefault("alert_level", 3)
    modules["dfr"].setdefault("projects", [])
    modules["dfr"].setdefault("scylla", {"enabled": False, "bearer_token": "", "default_project_id": ""})
    modules["dfr"].setdefault("hikvision", {"enabled": False, "auth_mode": "none", "token": "", "default_project_id": "", "docks": [], "cameras": []})
    modules["dfr"]["hikvision"].setdefault("docks", [])
    modules["dfr"]["hikvision"].setdefault("cameras", [])
    modules["dfr"].setdefault("log_path", str((LOG_DIR / "dfr" / "dfr.log").resolve()))
    modules["nvr_sync"].setdefault("enabled", False)
    modules["nvr_sync"].setdefault("auto_assign", True)
    modules["nvr_sync"].setdefault("sdk_status", "sdk_not_configured")
    modules["nvr_sync"].setdefault("nvrs", [])
    modules["nvr_sync"].setdefault("mappings", [])
    modules["nvr_sync"].setdefault("sync_log", [])
    for keys, target_path in data_root_paths(data["storage"].get("data_root_path") or DEFAULT_STORAGE_ROOT).items():
        current = data
        for key in keys[:-1]:
            current = current.setdefault(key, {})
        is_file = bool(Path(target_path).suffix)
        current[keys[-1]] = str(Path(target_path).resolve()) if root_rebased else repair_path_value(current.get(keys[-1]), target_path, is_file=is_file)
    data.setdefault("backup", {})
    roles = data.setdefault("roles", {})
    for role in ("Admin", "Support", "User"):
        permissions = roles.setdefault(role, list(DEFAULT_PERMISSIONS.get(role, [])))
        if role == "Admin":
            roles[role] = list(DEFAULT_PERMISSIONS["Admin"])
            continue
        if isinstance(permissions, list) and "live_map" not in permissions:
            permissions.append("live_map")
        if isinstance(permissions, list) and "rid" not in permissions:
            permissions.append("rid")
        if role in ("Admin", "Support") and isinstance(permissions, list) and "nvr_sync" not in permissions:
            permissions.append("nvr_sync")
        if isinstance(permissions, list) and "dfr_view" not in permissions:
            permissions.append("dfr_view")
        if isinstance(permissions, list) and "openapi" not in permissions:
            permissions.append("openapi")
    return data


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 250000)
    return f"pbkdf2_sha256${salt}${base64.b64encode(dk).decode('ascii')}"


def verify_password(password, stored):
    try:
        method, salt, digest = stored.split("$", 2)
        if method != "pbkdf2_sha256":
            return False
        expected = hash_password(password, salt).split("$", 2)[2]
        return hmac.compare_digest(expected, digest)
    except Exception:
        return False


def default_users():
    return {
        "admin": {
            "username": "admin",
            "name": "Administrator",
            "email": "",
            "display_name": "Administrator",
            "role": "Admin",
            "password_hash": hash_password("admin123"),
            "failed_attempts": 0,
            "locked": False,
            "must_change_password": True,
            "created_at": utc_now(),
        }
    }


def load_config_file(path, default):
    with JSON_LOCK:
        if path.exists():
            try:
                with path.open("r", encoding="utf-8-sig") as f:
                    raw = json.load(f)
            except Exception:
                raw = default.copy()
        else:
            raw = default.copy()
        data = merge_defaults(raw, default)
        data = repair_portable_paths(data)
        if data != raw or not path.exists():
            write_json_unlocked(path, data)
        return data


def sanitize_users_data(raw):
    result = {}
    source = raw if isinstance(raw, dict) else {}
    for key, item in source.items():
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or key or "").strip()
        if not username or not item.get("password_hash"):
            continue
        result[username] = {
            "username": username,
            "name": str(item.get("name") or item.get("display_name") or username).strip(),
            "email": str(item.get("email") or "").strip(),
            "display_name": str(item.get("display_name") or item.get("name") or username).strip(),
            "role": str(item.get("role") or "User").strip() or "User",
            "password_hash": item.get("password_hash", ""),
            "failed_attempts": int(item.get("failed_attempts", 0) or 0),
            "locked": bool(item.get("locked", False)),
            "must_change_password": bool(item.get("must_change_password", False)),
            "created_at": item.get("created_at", utc_now()),
        }
    if "admin" not in result:
        result.update(default_users())
    return result


def load_users_file():
    with JSON_LOCK:
        if USERS_FILE.exists():
            try:
                with USERS_FILE.open("r", encoding="utf-8-sig") as f:
                    raw = json.load(f)
            except Exception:
                raw = default_users()
        else:
            raw = default_users()
        data = sanitize_users_data(raw)
        if data != raw or not USERS_FILE.exists():
            write_json_unlocked(USERS_FILE, data)
        return data


def settings():
    return load_config_file(SETTINGS_FILE, DEFAULT_SETTINGS)


def users():
    return load_users_file()


def public_users():
    result = []
    for item in users().values():
        username = str(item.get("username") or "").strip()
        if not username:
            continue
        result.append({
            "username": username,
            "name": item.get("name") or item.get("display_name", ""),
            "email": item.get("email", ""),
            "display_name": item.get("display_name", ""),
            "role": item.get("role", "User"),
            "failed_attempts": item.get("failed_attempts", 0),
            "locked": bool(item.get("locked", False)),
            "must_change_password": bool(item.get("must_change_password", False)),
            "created_at": item.get("created_at", ""),
        })
    return sorted(result, key=lambda x: x["username"].lower())


def command_output(command, timeout=5):
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.DEVNULL, timeout=timeout).strip()
    except Exception:
        return ""


def first_nonempty(lines):
    for line in lines:
        value = str(line or "").strip()
        if value and value.lower() not in ("uuid", "processorid", "serialnumber", "to be filled by o.e.m.", "none", "null"):
            return value
    return ""


def machine_hardware_values():
    board = ""
    cpu = ""
    if os.name == "nt":
        board = first_nonempty(command_output([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_ComputerSystemProduct).UUID"
        ]).splitlines())
        if not board:
            board = first_nonempty(command_output([
                "powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_BaseBoard).SerialNumber"
            ]).splitlines())
        cpu = first_nonempty(command_output([
            "powershell", "-NoProfile", "-Command",
            "(Get-CimInstance Win32_Processor | Select-Object -First 1).ProcessorId"
        ]).splitlines())
        if not cpu:
            cpu = first_nonempty(command_output([
                "powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"
            ]).splitlines())
    else:
        for candidate in ("/sys/class/dmi/id/product_uuid", "/sys/class/dmi/id/board_serial"):
            try:
                board = Path(candidate).read_text(encoding="utf-8", errors="ignore").strip()
                if board:
                    break
            except Exception:
                pass
        try:
            cpuinfo = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
            cpu_lines = []
            for line in cpuinfo.splitlines():
                if line.lower().startswith(("serial", "model name", "vendor_id", "cpu family", "model", "stepping")):
                    cpu_lines.append(line.strip())
            cpu = "|".join(cpu_lines[:12])
        except Exception:
            cpu = ""
    return {"board": board.strip(), "cpu": cpu.strip()}


def current_machine_code():
    values = machine_hardware_values()
    if not values["board"] or not values["cpu"]:
        return "", "hardware_id_unavailable"
    raw = f"AERO_SYNC|{values['board'].lower()}|{values['cpu'].lower()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest().upper()
    grouped = "-".join(digest[i:i + 4] for i in range(0, 32, 4))
    return f"AS-{grouped}", "ok"


def canonical_payload(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def rsa_verify_pkcs1_sha256(payload, signature_b64, public_key):
    try:
        n = int(str(public_key["n"]), 16)
        e = int(public_key.get("e", 65537))
        signature = base64.b64decode(str(signature_b64), validate=True)
        k = (n.bit_length() + 7) // 8
        if len(signature) != k:
            return False
        decoded = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
        digest = hashlib.sha256(canonical_payload(payload)).digest()
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + digest
        if len(digest_info) + 11 > k:
            return False
        expected = b"\x00\x01" + (b"\xff" * (k - len(digest_info) - 3)) + b"\x00" + digest_info
        return hmac.compare_digest(decoded, expected)
    except Exception:
        return False


def parse_license_expiry(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.lower() in ("never", "permanent", "perpetual"):
        return datetime.max.replace(tzinfo=timezone.utc)
    try:
        if len(raw) == 10:
            return datetime.fromisoformat(raw + "T23:59:59+00:00")
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def load_license_public_key():
    try:
        if not LICENSE_PUBLIC_KEY_FILE.exists():
            return None
        with LICENSE_PUBLIC_KEY_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def recover_legacy_license_file():
    if LICENSE_FILE.exists() or not LEGACY_LICENSE_FILE.exists():
        return
    try:
        LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LEGACY_LICENSE_FILE, LICENSE_FILE)
    except Exception:
        pass


def load_license_file():
    try:
        recover_legacy_license_file()
        if not LICENSE_FILE.exists():
            return None
        with LICENSE_FILE.open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def license_status():
    machine_code, machine_state = current_machine_code()
    base = {
        "machine_code": machine_code,
        "machine_state": machine_state,
        "status": "missing",
        "message": "License not found",
        "product": APP_NAME,
        "company": "",
        "email": "",
        "license_id": "",
        "license_type": "",
        "edition": "Advanced",
        "edition_source": "legacy_default",
        "issued_at": "",
        "expires_at": "",
        "days_remaining": None,
    }
    if machine_state != "ok":
        base["status"] = "hardware_id_unavailable"
        base["message"] = "Machine code cannot be generated from motherboard and CPU."
        return base
    license_doc = load_license_file()
    if not license_doc:
        return base
    payload = license_doc.get("payload") if isinstance(license_doc, dict) else None
    signature = license_doc.get("signature") if isinstance(license_doc, dict) else ""
    if not isinstance(payload, dict) or not signature:
        base["status"] = "invalid"
        base["message"] = "License file format is invalid"
        return base
    public_key = load_license_public_key()
    if not public_key:
        base["status"] = "invalid"
        base["message"] = "License public key is missing"
        return base
    if not rsa_verify_pkcs1_sha256(payload, signature, public_key):
        base["status"] = "invalid"
        base["message"] = "License signature is invalid"
        return base
    if str(payload.get("product") or "") not in ("AERO SYNC", APP_NAME):
        base["status"] = "invalid"
        base["message"] = "License product does not match AERO SYNC"
        return base
    if str(payload.get("machine_code") or "").strip() != machine_code:
        base["status"] = "invalid_machine"
        base["message"] = "License belongs to a different machine"
        return base
    expires = parse_license_expiry(payload.get("expires_at"))
    if not expires:
        base["status"] = "invalid"
        base["message"] = "License expiry is invalid"
        return base
    now = datetime.now(timezone.utc)
    days_remaining = None if expires == datetime.max.replace(tzinfo=timezone.utc) else max(0, (expires - now).days)
    base.update({
        "company": str(payload.get("company") or payload.get("customer") or ""),
        "email": str(payload.get("email") or ""),
        "license_id": str(payload.get("license_id") or ""),
        "license_type": str(payload.get("license_type") or ""),
        "edition": "Advanced" if str(payload.get("edition") or "").strip().lower() == "advanced" else ("Basic" if str(payload.get("edition") or "").strip().lower() == "basic" else "Advanced"),
        "edition_source": "license" if str(payload.get("edition") or "").strip().lower() in ("basic", "advanced") else "legacy_default",
        "issued_at": str(payload.get("issued_at") or ""),
        "expires_at": str(payload.get("expires_at") or ""),
        "days_remaining": days_remaining,
    })
    if expires < now:
        base["status"] = "expired"
        base["message"] = "License expired"
        return base
    base["status"] = "valid"
    base["message"] = "License valid"
    return base


def active_license_edition():
    status = license_status()
    return "Advanced" if str(status.get("edition") or "").strip().lower() == "advanced" else "Basic"


def advanced_license_enabled():
    return active_license_edition() == "Advanced"


def advanced_license_error():
    return {
        "ok": False,
        "error": "Advanced license required",
        "license_required_edition": "Advanced",
        "license": license_status(),
    }


def decode_license_text(text):
    raw = str(text or "").strip()
    raw = raw.lstrip("\ufeff").strip()
    if not raw:
        raise ValueError("License content is empty. Paste the full ASLIC-START ... ASLIC-END code or import the .lic file.")
    if "ASLIC-START" in raw:
        if "ASLIC-END" not in raw:
            raise ValueError("License code is incomplete. ASLIC-END is missing.")
        body = raw.split("ASLIC-START", 1)[1].split("ASLIC-END", 1)[0]
        compact = "".join(body.split())
        if not compact:
            raise ValueError("License code body is empty.")
        compact += "=" * (-len(compact) % 4)
        try:
            raw = base64.urlsafe_b64decode(compact.encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise ValueError("License code is not valid. Copy the complete code from the generator.") from exc
    try:
        return json.loads(raw)
    except Exception as exc:
        raise ValueError("License content is not valid. Paste ASLIC-START ... ASLIC-END or import a valid .lic file.") from exc


def save_license_text(text):
    doc = decode_license_text(text)
    if not isinstance(doc, dict) or "payload" not in doc or "signature" not in doc:
        raise ValueError("License file format is invalid")
    with JSON_LOCK:
        write_json_unlocked(LICENSE_FILE, doc)
    status = license_status()
    if status["status"] not in ("valid", "expired"):
        try:
            LICENSE_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        raise ValueError(status["message"])
    return status


def audit(message, username="-", level="INFO"):
    line = f"{utc_now()} [{level}] user={username} {mask_log_text(message)}\n"
    with AUDIT_FILE.open("a", encoding="utf-8") as f:
        f.write(line)


def diagnostic(message, username="-", level="INFO"):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = f"{utc_now()} [{level}] user={username} {mask_log_text(message)}\n"
        with DIAGNOSTICS_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def client_ip(handler):
    forwarded = handler.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    try:
        return handler.client_address[0]
    except Exception:
        return "-"


def user_agent(handler):
    return (handler.headers.get("User-Agent", "") or "-")[:180]


def audit_request(handler, message, username=None, level="INFO"):
    user = username or current_user(handler) or "-"
    audit(f"{message} ip={client_ip(handler)} ua={user_agent(handler)}", user, level)


def windows_cpu_percent():
    global CPU_LAST
    if os.name != "nt":
        try:
            return round(os.getloadavg()[0] * 100 / (os.cpu_count() or 1), 1)
        except Exception:
            return None

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

    idle = FILETIME()
    kernel = FILETIME()
    user = FILETIME()
    ok = ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    if not ok:
        return None

    def to_int(ft):
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    current = (to_int(idle), to_int(kernel), to_int(user))
    if not CPU_LAST:
        CPU_LAST = current
        return None
    idle_delta = current[0] - CPU_LAST[0]
    total_delta = (current[1] - CPU_LAST[1]) + (current[2] - CPU_LAST[2])
    CPU_LAST = current
    if total_delta <= 0:
        return None
    return round(max(0, min(100, (1 - idle_delta / total_delta) * 100)), 1)


def memory_status():
    if os.name != "nt":
        return {"available": False}

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        return {"available": False}
    used = stat.ullTotalPhys - stat.ullAvailPhys
    return {
        "available": True,
        "percent": int(stat.dwMemoryLoad),
        "total": stat.ullTotalPhys,
        "used": used,
        "free": stat.ullAvailPhys,
    }


def disk_status(label, path):
    try:
        p = Path(path).resolve()
        p.mkdir(parents=True, exist_ok=True) if not p.exists() and p.suffix == "" else None
        total, used, free = shutil.disk_usage(str(p if p.exists() else p.parent))
        return {
            "label": label,
            "path": str(p),
            "total": total,
            "used": used,
            "free": free,
            "percent": round((used / total) * 100, 1) if total else 0,
        }
    except Exception as exc:
        return {"label": label, "path": str(path), "error": str(exc)}


def gpu_status():
    now = time.time()
    if GPU_CACHE["items"] and now - GPU_CACHE["time"] < 300:
        return GPU_CACHE["items"]
    items = []
    if os.name == "nt":
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ]
            output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL, timeout=4)
            for line in output.splitlines():
                name = line.strip()
                if name:
                    items.append({"name": name, "usage": None})
        except Exception:
            pass
    GPU_CACHE["items"] = items or [{"name": "Not available", "usage": None}]
    GPU_CACHE["time"] = now
    return GPU_CACHE["items"]


def placeholder_resources():
    return {
        "cpu": {"percent": None, "cores": os.cpu_count()},
        "memory": {"available": False},
        "gpu": [{"name": "Checking", "usage": None}],
        "disks": [],
        "network": {"download_bps": 0, "upload_bps": 0, "clients": active_client_count(), "source": "pending"},
    }


def refresh_resource_cache(cfg):
    global RESOURCE_REFRESHING
    try:
        server_resources(cfg, max_age_seconds=0, allow_probe=True)
    finally:
        RESOURCE_REFRESHING = False


def server_resources(cfg, max_age_seconds=15, allow_probe=True):
    global RESOURCE_REFRESHING
    now = time.time()
    if RESOURCE_CACHE["data"] and now - RESOURCE_CACHE["time"] < max_age_seconds:
        return RESOURCE_CACHE["data"]
    if not allow_probe:
        if not RESOURCE_REFRESHING:
            RESOURCE_REFRESHING = True
            threading.Thread(target=refresh_resource_cache, args=(cfg,), daemon=True).start()
        return RESOURCE_CACHE["data"] or placeholder_resources()
    mem = memory_status()
    live_cfg = cfg["modules"].get("live_streams", {})
    s3_cfg = cfg["modules"].get("local_s3", {})
    backup_cfg = cfg.get("backup", {})
    disks = [
        disk_status("Install", BASE_DIR),
        disk_status("S3 Storage", s3_cfg.get("storage_path") or DATA_DIR / "storage"),
        disk_status("Recordings", live_cfg.get("save_path") or DATA_DIR / "recordings"),
        disk_status("Backups", backup_cfg.get("backup_path") or DATA_DIR / "backups"),
    ]
    data = {
        "cpu": {"percent": windows_cpu_percent(), "cores": os.cpu_count()},
        "memory": mem,
        "gpu": gpu_status(),
        "disks": disks,
        "network": network_status(cfg),
    }
    RESOURCE_CACHE["data"] = data
    RESOURCE_CACHE["time"] = now
    return data


def iso_age_seconds(value):
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
    except Exception:
        return None


def health_snapshot(cfg):
    started = time.perf_counter()
    events = event_data(cfg, 1)
    mqtt = mqtt_data(cfg, 10, include_payload=False)
    media = media_data(cfg, 20)
    dfr = dfr_data(cfg, 10)
    nvr = nvr_sync_status(cfg)
    resources = server_resources(cfg, max_age_seconds=60, allow_probe=False)
    last_event = events.get("last_event") or {}
    mqtt_messages = mqtt.get("messages") or []
    latest_mqtt = mqtt_messages[0] if mqtt_messages else {}
    dfr_last = (dfr.get("events") or [{}])[0]
    return {
        "generated_at": utc_now(),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "modules": module_status(),
        "freshness": {
            "event_api": {
                "count": events.get("count", 0),
                "last_at": last_event.get("received_at"),
                "age_sec": iso_age_seconds(last_event.get("received_at")),
                "last_type": last_event.get("event_type") or "",
                "available": events.get("available", False),
            },
            "mqtt": {
                "count": mqtt.get("count", 0),
                "recent": len(mqtt_messages),
                "last_topic": latest_mqtt.get("topic") or "",
                "last_time": latest_mqtt.get("time") or "",
                "available": mqtt.get("available", False),
            },
            "s3": {
                "count": media.get("count", 0),
                "bytes": media.get("bytes", 0),
                "recent": len(media.get("files") or []),
                "available": media.get("available", False),
            },
            "dfr": {
                "today": dfr.get("today_count", 0),
                "queue": len([e for e in dfr.get("events", []) if e.get("status") == "Event Received"]),
                "last_status": dfr_last.get("status") or "",
                "last_at": dfr_last.get("received_at") or "",
                "age_sec": iso_age_seconds(dfr_last.get("received_at")),
            },
            "nvr": {
                "enabled": nvr.get("enabled", False),
                "servers": len(nvr.get("servers") or []),
                "used_channels": nvr.get("used_channels", 0),
                "free_channels": nvr.get("free_channels", 0),
            },
        },
        "network": resources.get("network", {}),
        "clients": active_client_count(),
    }


def validate_certificate_pair(cert_path, key_path):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))


def ensure_certificate(cfg=None):
    cfg = cfg or settings()
    security = cfg.get("security", {})
    if security.get("ssl_mode") == "custom":
        cert = Path(security.get("custom_cert_path") or "")
        key = Path(security.get("custom_key_path") or "")
        if cert.exists() and key.exists():
            try:
                validate_certificate_pair(cert, key)
                return cert, key
            except Exception as exc:
                audit(f"custom HTTPS certificate invalid, falling back to self-signed: {exc}", "system", "WARN")

    cert = CERT_DIR / "operation-center-selfsigned.crt"
    key = CERT_DIR / "operation-center-selfsigned.key"
    if cert.exists() and key.exists():
        return cert, key

    openssl_candidates = [
        "openssl",
        r"C:\Program Files\Git\mingw64\bin\openssl.exe",
        r"C:\Program Files\Git\usr\bin\openssl.exe",
    ]
    for openssl in openssl_candidates:
        try:
            subprocess.run(
                [
                    openssl,
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-sha256",
                    "-days",
                    "3650",
                    "-nodes",
                    "-keyout",
                    str(key),
                    "-out",
                    str(cert),
                    "-subj",
                    "/CN=Operation Center",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            audit("generated self-signed certificate", "system")
            return cert, key
        except Exception:
            pass

    ps_script = f"""
$ErrorActionPreference = "Stop"
$rsa = [System.Security.Cryptography.RSA]::Create(2048)
$subject = "CN=Operation Center"
$hash = [System.Security.Cryptography.HashAlgorithmName]::SHA256
$padding = [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
$req = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new($subject, $rsa, $hash, $padding)
$cert = $req.CreateSelfSigned([System.DateTimeOffset]::Now.AddDays(-1), [System.DateTimeOffset]::Now.AddYears(10))
$certBytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
$keyBytes = $rsa.ExportPkcs8PrivateKey()
$certPem = "-----BEGIN CERTIFICATE-----`n" + [System.Convert]::ToBase64String($certBytes, [System.Base64FormattingOptions]::InsertLineBreaks) + "`n-----END CERTIFICATE-----`n"
$keyPem = "-----BEGIN PRIVATE KEY-----`n" + [System.Convert]::ToBase64String($keyBytes, [System.Base64FormattingOptions]::InsertLineBreaks) + "`n-----END PRIVATE KEY-----`n"
[System.IO.File]::WriteAllText({json.dumps(str(cert))}, $certPem, [System.Text.Encoding]::ASCII)
[System.IO.File]::WriteAllText({json.dumps(str(key))}, $keyPem, [System.Text.Encoding]::ASCII)
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        audit("generated self-signed certificate", "system")
        return cert, key
    except Exception:
        print("ERROR: Could not generate self-signed certificate.")
        print("Use Windows PowerShell 5+ or the final installer with bundled certificate tools.")
        raise


def cookie_value(headers):
    raw = headers.get("Cookie", "")
    for part in raw.split(";"):
        if "=" in part:
            key, value = part.strip().split("=", 1)
            if key == "oc_session":
                return value
    return ""


def current_user(handler):
    sid = cookie_value(handler.headers)
    if not sid:
        return None
    sess = sessions.get(sid)
    if not sess:
        diagnostic(f"session_invalid path={getattr(handler, 'path', '-')} ip={client_ip(handler)}", "session", "WARN")
        return None
    timeout = settings()["security"]["session_timeout_minutes"] * 60
    age = time.time() - float(sess.get("last_seen") or 0)
    if age > timeout:
        username = sess.get("user", "-")
        sessions.pop(sid, None)
        diagnostic(f"session_expired path={getattr(handler, 'path', '-')} ip={client_ip(handler)} age_sec={int(age)} timeout_sec={int(timeout)}", username, "WARN")
        return None
    sess["last_seen"] = time.time()
    return sess["user"]



def active_client_count():
    now = time.time()
    try:
        timeout = int(settings().get("security", {}).get("session_timeout_minutes", 60)) * 60
    except Exception:
        timeout = 3600
    count = 0
    for sid, sess in list(sessions.items()):
        last_seen = float(sess.get("last_seen") or 0)
        if now - last_seen > timeout:
            sessions.pop(sid, None)
            continue
        count += 1
    return count


def network_counters(cfg):
    local_ip = str(cfg.get("network", {}).get("local_ip", "") or "").strip()
    try:
        import psutil
        target_name = None
        if local_ip:
            for name, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if getattr(addr, "address", "") == local_ip:
                        target_name = name
                        break
                if target_name:
                    break
        if target_name:
            counters = psutil.net_io_counters(pernic=True).get(target_name)
            if counters:
                return {
                    "rx": int(counters.bytes_recv),
                    "tx": int(counters.bytes_sent),
                    "source": target_name,
                    "local_ip": local_ip,
                }
        counters = psutil.net_io_counters()
        return {
            "rx": int(counters.bytes_recv),
            "tx": int(counters.bytes_sent),
            "source": "all",
            "local_ip": local_ip,
        }
    except Exception:
        pass
    if os.name == "nt":
        try:
            safe_ip = local_ip.replace("'", "''")
            ps = (
                "$localIp='" + safe_ip + "'; "
                "$stats=$null; "
                "if($localIp){$cfg=Get-NetIPConfiguration | Where-Object {$_.IPv4Address.IPAddress -eq $localIp} | Select-Object -First 1; if($cfg){$stats=Get-NetAdapterStatistics -InterfaceIndex $cfg.InterfaceIndex}}; "
                "if(-not $stats){$stats=Get-NetAdapterStatistics}; "
                "$rx=($stats | Measure-Object -Property ReceivedBytes -Sum).Sum; "
                "$tx=($stats | Measure-Object -Property SentBytes -Sum).Sum; "
                "Write-Output ([string]::Format('{0},{1}', [int64]$rx, [int64]$tx))"
            )
            output = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=4,
            ).strip()
            rx, tx = output.split(",", 1)
            return {"rx": int(rx), "tx": int(tx), "source": "windows", "local_ip": local_ip}
        except Exception:
            pass
    return {"rx": 0, "tx": 0, "source": "unavailable", "local_ip": local_ip}


def network_status(cfg):
    global NETWORK_LAST
    now = time.time()
    counters = network_counters(cfg)
    download_bps = 0
    upload_bps = 0
    if NETWORK_LAST and counters.get("source") == NETWORK_LAST.get("source"):
        elapsed = max(0.1, now - float(NETWORK_LAST.get("time") or now))
        download_bps = max(0, (int(counters.get("rx") or 0) - int(NETWORK_LAST.get("rx") or 0)) / elapsed)
        upload_bps = max(0, (int(counters.get("tx") or 0) - int(NETWORK_LAST.get("tx") or 0)) / elapsed)
    NETWORK_LAST = {
        "time": now,
        "rx": int(counters.get("rx") or 0),
        "tx": int(counters.get("tx") or 0),
        "source": counters.get("source"),
    }
    return {
        "download_bps": round(download_bps, 1),
        "upload_bps": round(upload_bps, 1),
        "clients": active_client_count(),
        "source": counters.get("source"),
        "local_ip": counters.get("local_ip"),
    }

def user_permissions(username):
    if not username:
        return []
    us = users().get(username)
    if not us:
        return []
    return settings().get("roles", {}).get(us.get("role", "User"), [])


def visible_urls(cfg):
    ports = cfg["ports"]
    local_ip = cfg["network"].get("local_ip", "").strip()
    wan_ip = cfg["network"].get("wan_ip", "").strip()

    def build(ip):
        return {
            "dashboard": f"https://{ip}:{ports['dashboard_https']}",
            "event_api": f"http://{ip}:{ports['event_api']}/dji/event",
            "event_api_dashboard": f"https://{ip}:{ports['dashboard_https']}/dji/event",
            "s3": f"http://{ip}:{ports['local_s3']}",
            "mqtt": f"{ip}:{ports['mqtt_broker']}",
            "mqtt_status": f"http://{ip}:{ports['internal_api']}/mqtt/device-status",
            "stream": f"https://{ip}:{ports['stream_bridge']}",
        }

    return {
        "local": build(local_ip) if local_ip else None,
        "wan": build(wan_ip) if wan_ip else None,
    }


def safe_path(value):
    if not value:
        return None
    try:
        return Path(value).expanduser()
    except Exception:
        return None


def existing_file_or_default(value, default_path):
    path = safe_path(value)
    if path and path.exists() and path.is_file():
        return path
    return Path(default_path)


def read_tail_text_lines(path, limit=100, chunk_size=65536):
    path = safe_path(path)
    if not path or not path.exists() or not path.is_file():
        return []
    if limit <= 0:
        return []
    try:
        lines = []
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            position = f.tell()
            buffer = b""
            while position > 0 and len(lines) <= limit:
                read_size = min(chunk_size, position)
                position -= read_size
                f.seek(position)
                buffer = f.read(read_size) + buffer
                lines = buffer.splitlines()
        return [line.decode("utf-8", errors="replace") for line in lines[-limit:]]
    except Exception:
        return []


def tail_lines(path, limit=100):
    path = safe_path(path)
    if not path or not path.exists() or not path.is_file():
        return []
    try:
        lines = []
        for line in read_tail_text_lines(path, limit):
            parts = line.rstrip("\n").split("\\n")
            lines.extend(part for part in parts if part)
        clean = []
        for line in lines:
            if "Bad request version" in line or "Bad request syntax" in line:
                clean.append("WARN HTTPS/TLS traffic was sent to HTTP EventAPI port. Use http://IP:9090/dji/event, not https://IP:9090/dji/event.")
                continue
            clean.append("".join(ch if ch in "\t\r\n" or 32 <= ord(ch) < 127 else "." for ch in line))
        return clean[-limit:]
    except Exception:
        return []


def tail_json_lines(path, limit=100):
    path = safe_path(path)
    if not path or not path.exists() or not path.is_file():
        return []
    try:
        return [line.rstrip("\n") for line in read_tail_text_lines(path, limit) if line.strip()][-limit:]
    except Exception:
        return []


def count_file_lines(path):
    path = safe_path(path)
    if not path or not path.exists() or not path.is_file():
        return 0
    try:
        stat = path.stat()
        key = str(path.resolve())
        cached = LINE_COUNT_CACHE.get(key)
        signature = (stat.st_size, stat.st_mtime)
        if cached and cached.get("signature") == signature:
            return cached["count"]
        count = 0
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                count += chunk.count(b"\n")
        LINE_COUNT_CACHE[key] = {"signature": signature, "count": count}
        return count
    except Exception:
        return 0


def event_data(cfg, limit=50):
    configured_path = safe_path(cfg["modules"]["event_receiver"].get("event_db_path"))
    db_path = configured_path or (DATA_DIR / "events.db")
    result = {
        "configured": True,
        "available": False,
        "count": 0,
        "last_event": None,
        "events": [],
        "db_path": str(db_path),
        "legacy_db_path": "",
    }
    if not db_path or not db_path.exists():
        return result
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        result["available"] = True
        result["count"] = con.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
        rows = con.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        for row in rows:
            item = dict(row)
            try:
                item["raw"] = json.loads(item.get("raw_json") or "{}")
            except Exception:
                item["raw"] = None
            result["events"].append(item)
        result["last_event"] = result["events"][0] if result["events"] else None
        con.close()
    except Exception as exc:
        result["error"] = str(exc)
    return result


def event_human_status(events, limit=6):
    summaries = []
    for item in (events or [])[:limit]:
        raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        event_type = str(item.get("event_type") or raw.get("type") or "Event")
        sn = str(item.get("device_sn") or data.get("sn") or "-")
        name = str(data.get("device_callsign") or data.get("converter_name") or data.get("device_name") or sn)
        if event_type == "live_rtsp_start":
            title = "Live Stream Updated"
            message = f"{name} ({sn})"
            level = "ok"
        elif "uploaded" in event_type or "file" in event_type:
            title = "File Event Received"
            message = f"{event_type} | {sn}"
            level = "info"
        elif "error" in event_type.lower() or "fail" in event_type.lower():
            title = "Event Warning"
            message = f"{event_type} | {sn}"
            level = "warn"
        else:
            title = "Event Received"
            message = f"{event_type} | {sn}"
            level = "info"
        summaries.append({
            "level": level,
            "title": title,
            "message": message,
            "time": item.get("received_at"),
            "source": item.get("source_ip") or "",
        })
    return summaries


def mqtt_message_is_registered_rid(msg):
    rid_module = MODULES.get("rid")
    if not rid_module:
        return False
    try:
        payload = json.loads(msg.get("payload") or "{}") if isinstance(msg, dict) else {}
    except Exception:
        payload = {}
    try:
        return bool(rid_module._match_registered((msg or {}).get("topic"), payload))
    except Exception:
        return False


def mqtt_non_rid_count(log_path):
    count = 0
    try:
        with Path(log_path).open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    msg = json.loads(line)
                except Exception:
                    count += 1
                    continue
                if not mqtt_message_is_registered_rid(msg):
                    count += 1
    except Exception:
        return 0
    return count


def mqtt_data(cfg, limit=100, include_payload=True):
    log_path = safe_path(cfg["modules"]["mqtt"].get("capture_log_path"))
    result = {"configured": bool(log_path), "available": False, "count": 0, "topics": {}, "gateways": {}, "messages": []}
    if not log_path or not log_path.exists():
        return result
    scan_limit = max(int(limit or 100) * 50, 5000)
    lines = tail_json_lines(log_path, scan_limit)
    result["available"] = True
    result["count"] = mqtt_non_rid_count(log_path)
    filtered = []
    for line in lines:
        try:
            msg = json.loads(line)
        except Exception:
            msg = {"payload": line, "payload_type": "RAW"}
        if mqtt_message_is_registered_rid(msg):
            continue
        filtered.append(msg)
    for msg in filtered[-max(1, int(limit or 100)):]:
        payload = None
        if include_payload:
            try:
                payload = json.loads(msg.get("payload") or "{}")
            except Exception:
                pass
        elif "payload" in msg:
            msg["payload_preview"] = str(msg.get("payload") or "")[:240]
            msg.pop("payload", None)
        msg["payload_json"] = payload
        topic = msg.get("topic") or "-"
        gateway = payload.get("gateway") if isinstance(payload, dict) else ""
        result["topics"][topic] = result["topics"].get(topic, 0) + 1
        if gateway:
            result["gateways"][gateway] = result["gateways"].get(gateway, 0) + 1
        result["messages"].append(msg)
    result["messages"].reverse()
    return result


def mqtt_raw_search(cfg, query):
    log_path = safe_path(cfg["modules"]["mqtt"].get("capture_log_path"))
    q = str((query.get("q") or [""])[0] or "").strip().lower()
    topic_filter = str((query.get("topic") or [""])[0] or "").strip().lower()
    start_dt = parse_report_date((query.get("from") or [""])[0])
    end_dt = parse_report_date((query.get("to") or [""])[0], True)
    limit = max(1, min(1000, int((query.get("limit") or [250])[0] or 250)))
    scan_limit = max(limit, min(50000, int((query.get("scan") or [10000])[0] or 10000)))
    result = {"available": False, "count": 0, "matched": 0, "messages": [], "source": str(log_path or "")}
    if not log_path or not log_path.exists():
        return result
    result["available"] = True
    result["count"] = mqtt_non_rid_count(log_path)
    rows = []
    for line in tail_json_lines(log_path, scan_limit):
        try:
            msg = json.loads(line)
        except Exception:
            msg = {"time": "", "topic": "", "payload": line, "payload_type": "RAW", "bytes": len(line.encode("utf-8", errors="replace"))}
        if mqtt_message_is_registered_rid(msg):
            continue
        time_value = str(msg.get("time") or "")
        topic = str(msg.get("topic") or "")
        payload = str(msg.get("payload") or "")
        if start_dt or end_dt:
            if not in_report_range(time_value, start_dt, end_dt):
                continue
        if topic_filter and topic_filter not in topic.lower():
            continue
        haystack = f"{time_value}\n{topic}\n{payload}\n{msg.get('payload_type','')}".lower()
        if q and q not in haystack:
            continue
        rows.append(msg)
    result["matched"] = len(rows)
    result["messages"] = list(reversed(rows[-limit:]))
    return result


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(x), math.sqrt(max(0.0, 1 - x)))


def map_history_data(cfg, query):
    device_filter = str((query.get("device") or [""])[0] or "").strip().lower()
    q = str((query.get("q") or [""])[0] or "").strip().lower()
    start_dt = parse_report_date((query.get("from") or [""])[0])
    end_dt = parse_report_date((query.get("to") or [""])[0], True)
    limit = max(10, min(5000, int((query.get("limit") or [1000])[0] or 1000)))
    mqtt = mqtt_data(cfg, 20000)
    points = []
    for msg in reversed(mqtt.get("messages", [])):
        device = map_device_from_message(msg)
        if not device:
            continue
        sn = str(device.get("sn") or "")
        name = str(device.get("name") or "")
        time_value = str(device.get("last_seen") or msg.get("time") or "")
        if device_filter and device_filter not in sn.lower() and device_filter not in name.lower():
            continue
        if not in_report_range(time_value, start_dt, end_dt):
            continue
        haystack = f"{sn} {name} {device.get('kind','')} {msg.get('topic','')}".lower()
        if q and q not in haystack:
            continue
        points.append({
            "sn": sn, "name": name or sn, "kind": device.get("kind") or "device",
            "time": time_value, "lat": device.get("lat"), "lng": device.get("lng"),
            "altitude": device.get("altitude"), "battery": device.get("battery"),
            "heading": device.get("heading"), "topic": msg.get("topic") or "",
        })
        if len(points) >= limit:
            break
    devices = {}
    for p in points:
        bucket = devices.setdefault(p["sn"] or p["name"], {"sn":p["sn"], "name":p["name"], "kind":p["kind"], "points":[]})
        bucket["points"].append(p)
    summaries=[]
    for item in devices.values():
        pts=item["points"]
        distance=0.0
        for prev, cur in zip(pts, pts[1:]):
            try: distance += haversine_m(float(prev["lat"]), float(prev["lng"]), float(cur["lat"]), float(cur["lng"]))
            except Exception: pass
        start_time=pts[0]["time"] if pts else ""
        end_time=pts[-1]["time"] if pts else ""
        duration=0
        a=parse_any_datetime(start_time); b=parse_any_datetime(end_time)
        if a and b: duration=max(0, int((b-a).total_seconds()))
        summaries.append({**item, "point_count":len(pts), "distance_m":round(distance,1), "duration_seconds":duration, "start_time":start_time, "end_time":end_time})
    summaries.sort(key=lambda x: x.get("end_time") or "", reverse=True)
    return {"count":len(points), "devices":summaries, "from":(query.get("from") or [""])[0], "to":(query.get("to") or [""])[0]}


def map_history_snapshot_svg(history, device_key):
    device = next((d for d in history.get("devices", []) if str(d.get("sn") or d.get("name")) == str(device_key)), None)
    if not device or not device.get("points"):
        return None
    pts=device["points"]
    lats=[float(p["lat"]) for p in pts]; lngs=[float(p["lng"]) for p in pts]
    minlat,maxlat=min(lats),max(lats); minlng,maxlng=min(lngs),max(lngs)
    if maxlat-minlat < 1e-8: maxlat=minlat+0.001
    if maxlng-minlng < 1e-8: maxlng=minlng+0.001
    W,H,P=1000,600,60
    def xy(lat,lng):
        x=P+(lng-minlng)/(maxlng-minlng)*(W-2*P)
        y=H-P-(lat-minlat)/(maxlat-minlat)*(H-2*P)
        return x,y
    coords=[xy(float(p["lat"]),float(p["lng"])) for p in pts]
    poly=" ".join(f"{x:.1f},{y:.1f}" for x,y in coords)
    sx,sy=coords[0]; ex,ey=coords[-1]
    title=(device.get("name") or device.get("sn") or "Device").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
<rect width="100%" height="100%" fill="#111827"/><g stroke="#263244" stroke-width="1">{''.join(f'<line x1="{x}" y1="40" x2="{x}" y2="560"/>' for x in range(100,1000,100))}{''.join(f'<line x1="40" y1="{y}" x2="960" y2="{y}"/>' for y in range(100,600,100))}</g>
<text x="60" y="35" fill="#f3f4f6" font-family="Arial" font-size="22" font-weight="bold">AERO SYNC - Map History</text><text x="60" y="58" fill="#9ca3af" font-family="Arial" font-size="14">{title} | {len(pts)} points | {device.get('distance_m',0)} m</text>
<polyline points="{poly}" fill="none" stroke="#60a5fa" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="{sx:.1f}" cy="{sy:.1f}" r="9" fill="#22c55e"/><circle cx="{ex:.1f}" cy="{ey:.1f}" r="9" fill="#ef4444"/><text x="{sx+12:.1f}" y="{sy-8:.1f}" fill="#e5e7eb" font-family="Arial" font-size="13">START</text><text x="{ex+12:.1f}" y="{ey-8:.1f}" fill="#e5e7eb" font-family="Arial" font-size="13">END</text>
<text x="60" y="585" fill="#9ca3af" font-family="Arial" font-size="12">Start: {device.get('start_time','')} | End: {device.get('end_time','')} | Generated by AERO SYNC</text></svg>'''


def first_nested_value(data, keys):
    if not isinstance(data, (dict, list)):
        return None
    wanted = {k.lower() for k in keys}
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in wanted and value not in ("", None):
                return value
        for value in data.values():
            found = first_nested_value(value, wanted)
            if found not in ("", None):
                return found
    else:
        for value in data:
            found = first_nested_value(value, wanted)
            if found not in ("", None):
                return found
    return None


def first_nested_number(data, keys):
    value = first_nested_value(data, keys)
    if value in ("", None):
        return None
    try:
        return float(value)
    except Exception:
        return None


def topic_device_sn(topic):
    parts = [p for p in str(topic or "").split("/") if p]
    if len(parts) >= 3 and parts[0] in ("thing", "sys") and parts[1] == "product":
        return parts[2]
    return ""


def payload_root(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def direct_number(data, keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data and data[key] not in ("", None):
            try:
                return float(data[key])
            except Exception:
                return None
    return None


def device_model_label(model_key="", text=""):
    raw = f"{model_key or ''} {text or ''}".lower()
    if "dock 3" in raw or "dji dock 3" in raw:
        return "Dock 3"
    if "m4td" in raw or "matrice 4td" in raw or model_key == "0-100-1":
        return "Matrice 4TD"
    if "m4t" in raw or "matrice 4t" in raw:
        return "Matrice 4T"
    if "dock" in raw:
        return "Dock"
    if "matrice" in raw or "drone" in raw or "aircraft" in raw or "uav" in raw:
        return "Drone"
    return ""


def message_identity(msg):
    payload = msg.get("payload_json")
    if not isinstance(payload, dict):
        try:
            payload = json.loads(msg.get("payload") or "{}")
        except Exception:
            payload = {}
    root = payload_root(payload)
    topic = msg.get("topic") or ""
    topic_sn = topic_device_sn(topic)
    payload_sn = str(
        payload.get("gateway")
        or payload.get("sn")
        or payload.get("device_sn")
        or payload.get("deviceSn")
        or payload.get("device_sn_code")
        or root.get("gateway")
        or root.get("sn")
        or root.get("device_sn")
        or root.get("deviceSn")
        or root.get("device_sn_code")
        or ""
    ).strip()
    sub_device = root.get("sub_device") if isinstance(root.get("sub_device"), dict) else {}
    sub_device_sn = str(sub_device.get("device_sn") or sub_device.get("deviceSn") or "").strip()
    sn = topic_sn or payload_sn or sub_device_sn or "Unknown Device"
    model_key = str(
        root.get("device_model_key")
        or payload.get("device_model_key")
        or sub_device.get("device_model_key")
        or sub_device.get("deviceModelKey")
        or ""
    ).strip()
    converter = str(
        payload.get("converter_name")
        or payload.get("device_callsign")
        or payload.get("device_name")
        or payload.get("callsign")
        or root.get("converter_name")
        or root.get("device_callsign")
        or root.get("device_name")
        or root.get("callsign")
        or ""
    ).strip()
    topic_lower = topic.lower()
    gateway_sn = str(payload.get("gateway") or root.get("gateway") or "").strip()
    explicit_text = f"{converter} {model_key} {topic_lower}".lower()
    if any(token in explicit_text for token in ("dock", "gateway", "airport", "dock_sn")):
        kind = "dock"
    elif any(token in explicit_text for token in ("matrice", "m4t", "m4td", "drone", "aircraft", "uav")):
        kind = "drone"
    elif gateway_sn and (sn == gateway_sn or not topic_sn):
        kind = "dock"
    else:
        kind = "device"
    model = device_model_label(model_key, explicit_text)
    name = str(first_nested_value(payload, ["device_name", "device_callsign", "callsign", "gateway_name", "converter_name"]) or model or sn).strip()
    status = str(first_nested_value(payload, ["status", "mode_code", "flight_status", "device_status"]) or "online").strip()
    return payload, root, {
        "id": sn,
        "sn": sn,
        "name": name,
        "kind": kind,
        "model": model,
        "model_key": model_key,
        "status": status,
        "topic": topic,
        "last_seen": msg_seen_iso(payload, msg),
    }


def msg_seen_datetime(payload, msg):
    timestamp = first_nested_value(payload, ["timestamp"]) if isinstance(payload, (dict, list)) else None
    try:
        if timestamp not in ("", None):
            value = float(timestamp)
            if value > 100000000000:
                value = value / 1000.0
            return datetime.fromtimestamp(value, timezone.utc)
    except Exception:
        pass
    for key in ("received_at", "last_seen"):
        value = msg.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def msg_seen_iso(payload, msg):
    return msg_seen_datetime(payload, msg).isoformat()


def mark_device_freshness(device, timeout_seconds):
    try:
        seen = datetime.fromisoformat(str(device.get("last_seen", "")).replace("Z", "+00:00"))
        if not seen.tzinfo:
            seen = seen.replace(tzinfo=timezone.utc)
        age = max(0, (datetime.now(timezone.utc) - seen).total_seconds())
    except Exception:
        age = 0
    status = str(device.get("status") or "").lower()
    explicit_offline = status in ("0", "offline", "false", "disconnected", "not streaming")
    stale = age > int(timeout_seconds or 90)
    device["age_seconds"] = int(age)
    device["stale"] = stale
    if explicit_offline:
        device["status"] = "offline"
    else:
        device["status"] = "online"
    return device


def mqtt_human_status(messages, timeout_seconds=90, limit=12):
    summaries = []
    seen_devices = set()
    warnings = []
    for msg in messages or []:
        try:
            payload, root, identity = message_identity(msg)
            topic = str(msg.get("topic") or "")
            method = str(payload.get("method") or root.get("method") or "").strip()
            identity = mark_device_freshness(dict(identity), timeout_seconds)
            sn = identity.get("sn") or "Unknown Device"

            sub_device = root.get("sub_device") if isinstance(root.get("sub_device"), dict) else {}
            if sub_device:
                sub_sn = str(sub_device.get("device_sn") or sub_device.get("deviceSn") or "").strip()
                if sub_sn and sub_sn not in seen_devices:
                    online_value = int(sub_device.get("device_online_status", 1) or 0)
                    sub_model = device_model_label(
                        str(sub_device.get("device_model_key") or sub_device.get("deviceModelKey") or ""),
                        json.dumps(sub_device, ensure_ascii=False),
                    ) or "Drone"
                    summaries.append({
                        "level": "ok" if online_value else "offline",
                        "title": f"{sub_model} Online" if online_value else f"{sub_model} Offline",
                        "message": f"{sub_sn} reported by {sn}",
                        "time": identity.get("last_seen"),
                        "topic": topic,
                    })
                    seen_devices.add(sub_sn)

            if sn and sn != "Unknown Device" and sn not in seen_devices:
                status = identity.get("status") or "unknown"
                kind_label = {"dock": "Dock", "drone": "Drone"}.get(identity.get("kind"), "Device")
                label = identity.get("model") or kind_label
                title = f"{label} Online" if status == "online" else f"{label} Disconnected" if status == "disconnected" else f"{label} Offline"
                summaries.append({
                    "level": "ok" if status == "online" else "warn" if status == "disconnected" else "offline",
                    "title": title,
                    "message": f"{identity.get('name') or sn} ({sn})",
                    "time": identity.get("last_seen"),
                    "topic": topic,
                })
                seen_devices.add(sn)

            battery = first_nested_number(payload, ["capacity_percent", "battery_percent", "battery", "remain_capacity", "remaining_percent"])
            drone_in_dock = first_nested_value(payload, ["drone_in_dock"])
            gps_quality = first_nested_value(payload, ["quality", "gps_number", "rtk_number"])
            temperature = first_nested_number(payload, ["environment_temperature", "temperature"])
            wind = first_nested_number(payload, ["wind_speed"])
            detail_parts = []
            if battery is not None:
                detail_parts.append(f"Battery {battery:.0f}%")
            if drone_in_dock not in ("", None):
                detail_parts.append(f"Drone in dock: {'Yes' if str(drone_in_dock) == '1' else 'No'}")
            if gps_quality not in ("", None):
                detail_parts.append(f"GPS/quality {gps_quality}")
            if temperature is not None:
                detail_parts.append(f"Temp {temperature:.1f} C")
            if wind is not None:
                detail_parts.append(f"Wind {wind:.1f}")
            if detail_parts:
                summaries.append({
                    "level": "info",
                    "title": "Telemetry",
                    "message": " | ".join(detail_parts),
                    "time": identity.get("last_seen"),
                    "topic": topic,
                })

            if method == "hms":
                for item in ((root.get("list") if isinstance(root.get("list"), list) else [])[:5]):
                    code = item.get("code") or "HMS"
                    level = item.get("level")
                    warnings.append({
                        "level": "warn",
                        "title": "HMS Warning",
                        "message": f"{code} level {level} device_type {item.get('device_type', '--')}",
                        "time": identity.get("last_seen"),
                        "topic": topic,
                    })
        except Exception:
            continue
        if len(summaries) + len(warnings) >= limit:
            break
    output = warnings + summaries
    return output[:limit]


def device_kind_from_text(*values):
    text = " ".join(str(v or "") for v in values).lower()
    if any(token in text for token in ("dock", "gateway", "airport", "dock_sn")):
        return "dock"
    if any(token in text for token in ("o4", "ground station", "groundstation")):
        return "o4"
    if any(token in text for token in ("matrice", "m4t", "m4td", "drone", "aircraft", "uav", "0-100-1")):
        return "drone"
    return "device"


def merge_stream_identity(base, stream):
    if not stream:
        return base
    merged = dict(base)
    stream_name = stream.get("name")
    if stream_name and (not merged.get("name") or merged.get("name") in (merged.get("sn"), "Unknown Device")):
        merged["name"] = stream_name
    current_kind = merged.get("kind")
    stream_kind = stream.get("kind")
    if current_kind not in ("dock", "drone", "o4") and stream_kind in ("dock", "drone", "o4"):
        merged["kind"] = stream_kind
    return merged


def map_coordinate_sources(root):
    sources = [root] if isinstance(root, dict) else []
    if not isinstance(root, dict):
        return sources
    for key in ("host", "device", "drone", "aircraft", "position", "location"):
        value = root.get(key)
        if isinstance(value, dict):
            sources.append(value)
            for nested_key in ("position", "location", "self_converge_coordinate"):
                nested = value.get(nested_key)
                if isinstance(nested, dict):
                    sources.append(nested)
    return sources


def map_coordinate_pair(root):
    lat_keys = ["latitude", "lat", "drone_latitude", "aircraft_latitude", "rtk_latitude"]
    lng_keys = ["longitude", "lng", "lon", "long", "drone_longitude", "aircraft_longitude", "rtk_longitude"]
    for source in map_coordinate_sources(root):
        lat = direct_number(source, lat_keys)
        lng = direct_number(source, lng_keys)
        if lat is None or lng is None:
            continue
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            continue
        if lat == 0 and lng == 0:
            continue
        return lat, lng, source
    return None, None, root


def map_device_from_message(msg):
    payload, root, identity = message_identity(msg)
    lat, lng, gps_source = map_coordinate_pair(root)
    if lat is None or lng is None:
        return None
    battery = first_nested_number(payload, ["capacity_percent", "battery_percent", "battery", "remain_capacity", "remaining_percent"])
    altitude = direct_number(gps_source, ["height", "altitude", "elevation", "rtk_height", "relative_height"])
    if altitude is None:
        altitude = direct_number(root, ["height", "altitude", "elevation", "rtk_height", "relative_height"])
    speed = first_nested_number(payload, ["speed", "horizontal_speed", "vertical_speed", "wind_speed"])
    heading = first_nested_number(payload, ["attitude_head", "heading", "yaw", "aircraft_head"])
    gateway = str(first_nested_value(payload, ["gateway", "gateway_sn", "gateway_callsign"]) or "").strip()
    return {
        **identity,
        "lat": lat,
        "lng": lng,
        "altitude": altitude,
        "battery": battery,
        "speed": speed,
        "heading": heading,
        "gateway": gateway,
        "has_gps": True,
        "payload_type": msg.get("payload_type") or "JSON",
    }


def map_data(cfg, limit=2000):
    map_cfg = cfg["modules"].get("map", {})
    online_timeout = int(map_cfg.get("online_timeout_seconds") or 90)
    mqtt = mqtt_data(cfg, limit)
    stream_identity = {}
    for channel in cfg["modules"].get("live_streams", {}).get("channels", []):
        sn = str(channel.get("device_sn") or "").strip()
        if not sn:
            continue
        stream_identity[sn] = {
            "name": channel.get("name") or channel.get("converter_name") or sn,
            "kind": device_kind_from_text(channel.get("name"), channel.get("converter_name"), channel.get("source"), channel.get("stream_key")),
        }
    devices = {}
    online_devices = {}
    points_by_device = {}
    for msg in mqtt.get("messages", []):
        try:
            payload, root, identity = message_identity(msg)
            if identity["sn"] in stream_identity:
                identity = merge_stream_identity(identity, stream_identity.get(identity["sn"]))
            if identity["sn"] and identity["sn"] != "Unknown Device" and identity["sn"] not in online_devices:
                online_devices[identity["sn"]] = {**identity, "has_gps": False}
            sub_device = root.get("sub_device") if isinstance(root.get("sub_device"), dict) else {}
            sub_sn = str(sub_device.get("device_sn") or sub_device.get("deviceSn") or "").strip()
            if sub_sn and sub_sn not in online_devices:
                sub_stream = stream_identity.get(sub_sn, {})
                sub_kind = device_kind_from_text(
                    sub_device.get("device_name"),
                    sub_device.get("device_callsign"),
                    sub_device.get("device_model_key"),
                    sub_device.get("deviceModelKey"),
                    sub_stream.get("name"),
                )
                if sub_kind not in ("dock", "drone") and sub_stream.get("kind") in ("dock", "drone"):
                    sub_kind = sub_stream.get("kind")
                sub_identity = {
                    "id": sub_sn,
                    "sn": sub_sn,
                    "name": sub_device.get("device_name") or sub_device.get("device_callsign") or sub_stream.get("name") or sub_sn,
                    "kind": sub_kind,
                    "status": "online" if int(sub_device.get("device_online_status", 1) or 0) else "offline",
                    "topic": msg.get("topic") or "",
                    "last_seen": msg_seen_iso(payload, msg),
                    "has_gps": False,
                }
                online_devices[sub_sn] = sub_identity
        except Exception:
            pass
        device = map_device_from_message(msg)
        if not device:
            continue
        if device["sn"] in stream_identity:
            device = merge_stream_identity(device, stream_identity.get(device["sn"]))
        key = device["sn"]
        online_devices[key] = {**online_devices.get(key, {}), **device}
        points_by_device.setdefault(key, []).append({
            "lat": device["lat"],
            "lng": device["lng"],
            "time": device["last_seen"],
        })
        if key not in devices:
            devices[key] = device
    for key, device in devices.items():
        trail = list(reversed(points_by_device.get(key, [])[-50:]))
        device["trail"] = trail
    for device in online_devices.values():
        mark_device_freshness(device, online_timeout)
    for device in devices.values():
        mark_device_freshness(device, online_timeout)
    tile_path = safe_path(map_cfg.get("offline_tile_path")) or (DATA_DIR / "maps" / "tiles")
    tile_count = 0
    try:
        if tile_path.exists():
            tile_count = sum(1 for p in tile_path.rglob("*.png") if p.is_file())
    except Exception:
        tile_count = 0
    rid_module = MODULES.get("rid")
    if rid_module:
        try:
            rid_state = rid_module.status()
            for src in rid_state.get("sources", []):
                sn = str(src.get("serial_no") or src.get("id") or "").strip()
                if not sn:
                    continue
                item = {"id": sn, "sn": sn, "name": src.get("name") or sn, "kind": "rid", "status": src.get("status") or "offline", "last_seen": src.get("last_seen"), "has_gps": src.get("lat") is not None and src.get("lng") is not None, "lat": src.get("lat"), "lng": src.get("lng"), "altitude": src.get("altitude"), "battery": src.get("battery_percent")}
                online_devices[sn] = {**online_devices.get(sn, {}), **item}
                if item["has_gps"]:
                    devices[sn] = {**devices.get(sn, {}), **item, "trail": []}
            for tgt in rid_state.get("targets", []):
                if tgt.get("lat") is None or tgt.get("lng") is None:
                    continue
                sn = f"RID:{tgt.get('uav_id') or tgt.get('id')}"
                item = {"id": sn, "sn": sn, "name": tgt.get("model") or tgt.get("uav_id") or "RID Aircraft", "kind": "rid_aircraft", "status": "online" if tgt.get("status") == "live" else "offline", "last_seen": tgt.get("last_seen"), "has_gps": True, "lat": tgt.get("lat"), "lng": tgt.get("lng"), "altitude": tgt.get("altitude"), "speed": tgt.get("speed"), "heading": tgt.get("heading"), "trail": tgt.get("trail") or []}
                online_devices[sn] = item
                devices[sn] = item
        except Exception:
            pass

    online_list = [d for d in online_devices.values() if d.get("status") == "online"]
    offline_list = [d for d in online_devices.values() if d.get("status") == "offline"]
    disconnected_list = [d for d in online_devices.values() if d.get("status") == "disconnected"]
    online_keys = {d.get("sn") for d in online_list}
    visible_devices = [d for d in devices.values() if d.get("status") == "online" and d.get("sn") in online_keys]
    return {
        "settings": map_cfg,
        "devices": visible_devices,
        "device_count": len(visible_devices),
        "online_devices": [d for d in online_list if d.get("sn") in online_keys],
        "online_count": len(online_list),
        "offline_count": len(offline_list),
        "disconnected_count": len(disconnected_list),
        "dock_count": len([d for d in online_list if d.get("kind") == "dock"]),
        "drone_count": len([d for d in online_list if d.get("kind") == "drone"]),
        "o4_count": len([d for d in online_list if d.get("kind") == "o4"]),
        "rid_count": len([d for d in online_list if d.get("kind") == "rid"]),
        "mqtt_available": mqtt.get("available", False),
        "mqtt_count": mqtt.get("count", 0),
        "tile_path": str(tile_path),
        "tile_count": tile_count,
        "updated_at": utc_now(),
    }


def safe_tile_root(cfg):
    path = safe_path(cfg["modules"].get("map", {}).get("offline_tile_path")) or (DATA_DIR / "maps" / "tiles")
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def extract_tile_zip(zip_bytes, root):
    from io import BytesIO
    saved = 0
    skipped = 0
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("", "/").lstrip("/")
            parts = [p for p in name.split("/") if p not in ("", ".", "..")]
            if not parts or parts[-1].lower().split(".")[-1] not in ("png", "jpg", "jpeg", "webp"):
                skipped += 1
                continue
            target = root.joinpath(*parts).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            saved += 1
    return {"saved": saved, "skipped": skipped, "path": str(root)}


def media_data(cfg, limit=200):
    s3_cfg = cfg["modules"]["local_s3"]
    root = safe_path(s3_cfg.get("storage_path"))
    bucket = (s3_cfg.get("bucket") or "aeronex").strip().strip("/")
    preset = (s3_cfg.get("preset_path") or "").strip()
    result = {"configured": bool(root), "available": False, "count": 0, "bytes": 0, "files": []}
    if not root or not root.exists():
        return result
    view_root = root / bucket
    if preset:
        parts = []
        for raw in unquote(preset).lstrip("/").replace("\\", "/").split("/"):
            if raw in ("", ".", ".."):
                continue
            if re.fullmatch(r"[A-Za-z]:", raw):
                continue
            safe = re.sub(r'[<>:"|?*]', "_", raw).strip()
            if safe:
                parts.append(safe)
        if parts:
            view_root = view_root.joinpath(*parts)
    result["available"] = True
    try:
        files = [p for p in view_root.rglob("*") if p.is_file() and ".s3meta" not in p.parts] if view_root.exists() else []
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        result["count"] = len(files)
        result["bytes"] = sum(p.stat().st_size for p in files)
        for p in files[:limit]:
            st = p.stat()
            result["files"].append({
                "name": p.name,
                "path": str(p),
                "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            })
    except Exception as exc:
        result["error"] = str(exc)
    return result


def logs_data(cfg):
    event_log = existing_file_or_default(cfg["modules"]["event_receiver"].get("log_path"), LOG_DIR / "event_receiver.log")
    mqtt_log = existing_file_or_default(cfg["modules"]["mqtt"].get("dashboard_log_path"), LOG_DIR / "mqtt_dashboard.log")
    s3_log = existing_file_or_default(cfg["modules"]["local_s3"].get("log_path"), LOG_DIR / "local_s3.log")
    sources = {
        "Audit": str(AUDIT_FILE),
        "Event Receiver": str(event_log),
        "MQTT Dashboard": str(mqtt_log),
        "Local S3": str(s3_log),
        "DFR": str(existing_file_or_default(cfg.get("modules", {}).get("dfr", {}).get("log_path"), LOG_DIR / "dfr" / "dfr.log")),
    }
    logs = []
    for name, path in sources.items():
        lines = tail_lines(path, 80)
        size = 0
        try:
            size = Path(path).stat().st_size if Path(path).exists() else 0
        except Exception:
            size = 0
        logs.append({"name": name, "path": path, "available": bool(lines), "lines": lines, "size": size})
    return logs


def available_log_count(cfg):
    paths = [
        AUDIT_FILE,
        existing_file_or_default(cfg["modules"]["event_receiver"].get("log_path"), LOG_DIR / "event_receiver.log"),
        existing_file_or_default(cfg["modules"]["mqtt"].get("dashboard_log_path"), LOG_DIR / "mqtt_dashboard.log"),
        existing_file_or_default(cfg["modules"]["local_s3"].get("log_path"), LOG_DIR / "local_s3.log"),
        existing_file_or_default(cfg.get("modules", {}).get("dfr", {}).get("log_path"), LOG_DIR / "dfr" / "dfr.log"),
    ]
    count = 0
    for path in paths:
        try:
            if Path(path).exists() and Path(path).stat().st_size > 0:
                count += 1
        except Exception:
            pass
    return count


def log_retention_status(cfg):
    retention = cfg.get("log_retention", {})
    log_files = []
    groups = {}
    search_dirs = {LOG_DIR}
    data_root = safe_path(cfg.get("storage", {}).get("data_root_path"))
    if data_root:
        search_dirs.add(data_root)
    for active_path in (
        cfg["modules"]["event_receiver"].get("log_path"),
        cfg["modules"]["mqtt"].get("capture_log_path"),
        cfg["modules"]["mqtt"].get("dashboard_log_path"),
        cfg["modules"]["local_s3"].get("log_path"),
    ):
        p = safe_path(active_path)
        if p:
            search_dirs.add(p.parent)
    seen = set()
    for root in list(search_dirs):
        if not root or not root.exists():
            continue
        for path in root.rglob("*"):
            lower_name = path.name.lower()
            is_log_file = path.suffix.lower() == ".log" or ".log." in lower_name or lower_name.endswith(".log.zip")
            if path.is_file() and is_log_file:
                try:
                    resolved = str(path.resolve())
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    size = path.stat().st_size
                    category = "Other"
                    lname = path.name.lower()
                    if "mqtt_capture" in lname:
                        category = "MQTT Capture"
                    elif "mqtt_dashboard" in lname:
                        category = "MQTT Dashboard"
                    elif "audit" in lname:
                        category = "Audit"
                    elif "event_receiver" in lname:
                        category = "Event Receiver"
                    elif "local_s3" in lname:
                        category = "Local S3"
                    elif "stream" in lname:
                        category = "Live Streams"
                    bucket = groups.setdefault(category, {"name": category, "size": 0, "files": 0})
                    bucket["size"] += size
                    bucket["files"] += 1
                    log_files.append({
                        "name": path.name,
                        "path": str(path),
                        "size": size,
                        "category": category,
                        "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                    })
                except Exception:
                    pass
    log_files.sort(key=lambda x: x["modified"], reverse=True)
    group_list = sorted(groups.values(), key=lambda x: x["size"], reverse=True)
    return {"settings": retention, "files": log_files[:50], "groups": group_list}


def parse_report_date(value, end_of_day=False):
    if not value:
        return None
    try:
        if len(value) == 10:
            suffix = "T23:59:59" if end_of_day else "T00:00:00"
            value = value + suffix
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def parse_any_datetime(value):
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S,%f", "%d-%b-%y %I:%M %p"):
        try:
            return datetime.strptime(text[:26], fmt)
        except Exception:
            continue
    return None


def in_report_range(value, start_dt, end_dt):
    dt = parse_any_datetime(value)
    if not dt:
        return True
    if start_dt and dt < start_dt:
        return False
    if end_dt and dt > end_dt:
        return False
    return True


def audit_report_rows(limit=300):
    rows = []
    for line in tail_lines(AUDIT_FILE, limit):
        parts = line.split(" ", 3)
        ts = parts[0] if parts else ""
        level = parts[1].strip("[]") if len(parts) >= 2 else ""
        user_value = parts[2].replace("user=", "", 1) if len(parts) >= 3 and parts[2].startswith("user=") else ""
        message = parts[3] if len(parts) >= 4 else line
        section = "User Activity" if any(token in message for token in ("module_open", "live_stream_", "stream_snapshot")) else "Users / Security"
        rows.append({
            "section": section,
            "time": ts,
            "type": level,
            "device": user_value,
            "status": message,
            "details": line,
        })
    return rows


def report_data(cfg, query):
    start_dt = parse_report_date((query.get("from") or [""])[0])
    end_dt = parse_report_date((query.get("to") or [""])[0], True)
    device_filter = ((query.get("device") or [""])[0] or "").strip().lower()
    section_filter = ((query.get("section") or ["all"])[0] or "all").strip().lower()

    sections = {
        "EventAPI": [],
        "MQTT": [],
        "Media / S3": [],
        "Live Streams": [],
        "Map History": [],
        "User Activity": [],
        "Users / Security": [],
        "System Health": [],
    }

    events = event_data(cfg, 1000)
    for item in events.get("events", []):
        raw = item.get("raw") or {}
        data = raw.get("data") if isinstance(raw, dict) else {}
        device = item.get("device_sn") or (data or {}).get("sn") or ""
        time_value = item.get("received_at") or ""
        if device_filter and device_filter not in str(device).lower():
            continue
        if not in_report_range(time_value, start_dt, end_dt):
            continue
        sections["EventAPI"].append({
            "section": "EventAPI",
            "time": time_value,
            "type": item.get("event_type") or "",
            "device": device,
            "status": "Signature OK" if item.get("signature_valid") else "Unsigned",
            "details": f"Source {item.get('source_ip') or '-'}",
        })

    mqtt = mqtt_data(cfg, 250, include_payload=False)
    for item in mqtt.get("messages", []):
        payload = item.get("payload_json")
        device = ""
        if isinstance(payload, dict):
            device = payload.get("gateway") or payload.get("sn") or payload.get("device_sn") or ""
        topic = item.get("topic") or ""
        if not device and "/product/" in topic:
            parts = topic.split("/")
            if len(parts) >= 3:
                device = parts[2]
        if device_filter and device_filter not in str(device).lower() and device_filter not in topic.lower():
            continue
        sections["MQTT"].append({
            "section": "MQTT",
            "time": item.get("time") or "",
            "type": item.get("payload_type") or "MQTT",
            "device": device,
            "status": topic,
            "details": f"{item.get('bytes', 0)} bytes",
        })

    media = media_data(cfg, 1000)
    for item in media.get("files", []):
        time_value = item.get("modified") or ""
        if not in_report_range(time_value, start_dt, end_dt):
            continue
        path_text = item.get("path") or ""
        if device_filter and device_filter not in path_text.lower() and device_filter not in (item.get("name") or "").lower():
            continue
        sections["Media / S3"].append({
            "section": "Media / S3",
            "time": time_value,
            "type": "File",
            "device": "",
            "status": item.get("name") or "",
            "details": f"{item.get('size', 0)} bytes",
        })

    for channel in cfg["modules"]["live_streams"].get("channels", []):
        device = channel.get("device_sn") or ""
        time_value = channel.get("updated_at") or ""
        if device_filter and device_filter not in str(device).lower() and device_filter not in str(channel.get("name", "")).lower():
            continue
        sections["Live Streams"].append({
            "section": "Live Streams",
            "time": time_value,
            "type": f"Channel {int(channel.get('channel', 0)):02d}",
            "device": device,
            "status": channel.get("status") or ("enabled" if channel.get("enabled") else "offline"),
            "details": channel.get("name") or "",
        })

    map_points = {}
    mqtt_for_map = mqtt_data(cfg, 5000)
    for item in mqtt_for_map.get("messages", []):
        device = map_device_from_message(item)
        if not device:
            continue
        device_id = device.get("sn") or ""
        time_value = device.get("last_seen") or item.get("time") or ""
        if device_filter and device_filter not in str(device_id).lower() and device_filter not in str(device.get("name", "")).lower():
            continue
        if not in_report_range(time_value, start_dt, end_dt):
            continue
        bucket = map_points.setdefault(device_id, [])
        if len(bucket) >= 50:
            continue
        row = {
            "section": "Map History",
            "time": time_value,
            "type": device.get("kind") or "device",
            "device": device_id,
            "status": device.get("name") or device_id,
            "details": f"Lat {device.get('lat')} | Lng {device.get('lng')} | Alt {device.get('altitude', '--')} | Battery {device.get('battery', '--')} | Heading {device.get('heading', '--')}",
        }
        bucket.append(row)
        sections["Map History"].append(row)

    for row in audit_report_rows(300):
        if in_report_range(row.get("time"), start_dt, end_dt):
            sections.setdefault(row["section"], []).append(row)

    module_states = module_status()
    resources = server_resources(cfg, max_age_seconds=60, allow_probe=False)
    for name, state in module_states.items():
        state_text = "ready"
        if isinstance(state, dict) and state.get("error"):
            state_text = f"error: {state['error']}"
        elif isinstance(state, dict) and state.get("running") is False:
            state_text = "stopped"
        sections["System Health"].append({
            "section": "System Health",
            "time": datetime.now().isoformat(timespec="seconds"),
            "type": name,
            "device": "",
            "status": state_text,
            "details": json.dumps(state, ensure_ascii=False)[:300],
        })
    mem = resources.get("memory", {})
    sections["System Health"].append({
        "section": "System Health",
        "time": datetime.now().isoformat(timespec="seconds"),
        "type": "Server Resources",
        "device": "",
        "status": f"CPU {resources.get('cpu', {}).get('percent')}% | RAM {mem.get('percent', 'N/A')}%",
        "details": json.dumps(resources, ensure_ascii=False)[:500],
    })

    if section_filter != "all":
        sections = {k: v for k, v in sections.items() if k.lower() == section_filter}

    rows = []
    for values in sections.values():
        rows.extend(values)
    rows.sort(key=lambda r: str(r.get("time") or ""), reverse=True)
    summary = {
        "events": len(sections.get("EventAPI", [])),
        "mqtt": len(sections.get("MQTT", [])),
        "media": len(sections.get("Media / S3", [])),
        "streams": len(sections.get("Live Streams", [])),
        "map_history": len(sections.get("Map History", [])),
        "activity": len(sections.get("User Activity", [])),
        "security": len(sections.get("Users / Security", [])),
        "system": len(sections.get("System Health", [])),
        "total": len(rows),
    }
    return {"summary": summary, "sections": sections, "rows": rows[:1500]}


def split_emails(value):
    if isinstance(value, list):
        items = value
    else:
        items = str(value or "").replace(";", ",").split(",")
    return [item.strip() for item in items if item and item.strip()]


def template_text(text, values):
    result = str(text or "")
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result


def csv_bytes(rows):
    headers = ["section", "time", "type", "device", "status", "details"]

    def cell(value):
        text = str(value or "")
        return '"' + text.replace('"', '""') + '"'

    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join(cell(row.get(h)) for h in headers))
    return ("\ufeff" + "\n".join(lines)).encode("utf-8")


def json_bytes(report):
    return json.dumps(report, indent=2, ensure_ascii=False).encode("utf-8")


def xlsx_bytes(rows):
    headers = ["section", "time", "type", "device", "status", "details"]

    def xml_escape(value):
        return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    sheet_rows = []
    all_rows = [dict(zip(headers, headers))] + rows[:5000]
    for idx, row in enumerate(all_rows, 1):
        cells = []
        for col_idx, key in enumerate(headers, 1):
            col = ""
            n = col_idx
            while n:
                n, rem = divmod(n - 1, 26)
                col = chr(65 + rem) + col
            cells.append(f'<c r="{col}{idx}" t="inlineStr"><is><t>{xml_escape(row.get(key))}</t></is></c>')
        sheet_rows.append(f'<row r="{idx}">{"".join(cells)}</row>')
    sheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'''
    from io import BytesIO

    bio = BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>''')
        zf.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        zf.writestr("xl/workbook.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        zf.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>''')
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return bio.getvalue()


def pdf_bytes(report):
    rows = report.get("rows", [])[:80]
    lines = ["Operation Center Report", f"Generated: {datetime.now().isoformat(timespec='seconds')}", ""]
    for row in rows:
        lines.append(f"{row.get('section','')} | {row.get('time','')} | {row.get('type','')} | {row.get('device','')} | {row.get('status','')}")
    text = "\n".join(lines)
    safe = text.replace("", "").replace("(", "\\(").replace(")", "\\)")
    content = "BT /F1 10 Tf 40 780 Td 12 TL " + " T* ".join(f"({line[:110]}) Tj" for line in safe.splitlines()) + " ET"
    content_bytes = content.encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content_bytes)} >>\nstream\n".encode("ascii") + content_bytes + b"\nendstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{i} 0 obj\n".encode("ascii"))
        result.extend(obj)
        result.extend(b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        result.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    result.extend(f"trailer << /Root 1 0 R /Size {len(objects)+1} >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(result)


def report_attachments(report, formats):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rows = report.get("rows", [])
    attachments = []
    for fmt in formats:
        fmt = str(fmt).lower().strip()
        if fmt == "csv":
            attachments.append((f"OperationCenter_Report_{stamp}.csv", "text/csv", csv_bytes(rows)))
        elif fmt == "json":
            attachments.append((f"OperationCenter_Report_{stamp}.json", "application/json", json_bytes(report)))
        elif fmt == "xlsx":
            attachments.append((f"OperationCenter_Report_{stamp}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", xlsx_bytes(rows)))
        elif fmt == "pdf":
            attachments.append((f"OperationCenter_Report_{stamp}.pdf", "application/pdf", pdf_bytes(report)))
    return attachments


def send_email(cfg, to_addresses, subject, body, attachments=None, from_address=None, cc=None, bcc=None):
    email_cfg = cfg["modules"].get("email", {})
    if not email_cfg.get("enabled"):
        raise ValueError("Email module is disabled")
    host = str(email_cfg.get("smtp_host") or "").strip()
    if not host:
        raise ValueError("SMTP host is required")
    port = int(email_cfg.get("smtp_port") or 587)
    from_candidates = split_emails(from_address or email_cfg.get("from_addresses"))
    if not from_candidates:
        raise ValueError("Sender email is required")
    sender = from_candidates[0]
    recipients = split_emails(to_addresses)
    cc_list = split_emails(cc)
    bcc_list = split_emails(bcc)
    all_recipients = recipients + cc_list + bcc_list
    if not all_recipients:
        raise ValueError("At least one recipient is required")

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.set_content(body or "")
    for filename, mime, content in attachments or []:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)

    security = str(email_cfg.get("security") or "starttls").lower()
    username = email_cfg.get("username") or ""
    password = email_cfg.get("password") or ""
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    try:
        server.ehlo()
        if security == "starttls":
            server.starttls()
            server.ehlo()
        if username:
            server.login(username, password)
        server.send_message(msg, from_addr=sender, to_addrs=all_recipients)
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return {"from": sender, "to": recipients, "cc": cc_list, "bcc_count": len(bcc_list), "attachments": [a[0] for a in attachments or []]}


def email_templates(cfg):
    email_cfg = cfg["modules"].get("email", {})
    templates = email_cfg.get("templates") or []
    if templates:
        return templates
    return [{
        "id": "default",
        "name": "Default Report",
        "from_address": email_cfg.get("from_addresses", ""),
        "to": email_cfg.get("default_recipients", ""),
        "cc": email_cfg.get("cc_recipients", ""),
        "bcc": email_cfg.get("bcc_recipients", ""),
        "subject": email_cfg.get("template_subject", "Operation Center Report - {date}"),
        "body": email_cfg.get("template_body", ""),
        "section": "all",
        "device": "",
        "formats": email_cfg.get("default_attachment_formats") or ["csv", "json"],
    }]


def find_email_template(cfg, template_id):
    templates = email_templates(cfg)
    if template_id:
        for template in templates:
            if str(template.get("id") or template.get("name") or "") == str(template_id):
                return template
    return templates[0] if templates else {}


def schedule_due(template, now=None):
    if not template.get("schedule_enabled"):
        return False
    now = now or datetime.now()
    target_time = str(template.get("schedule_time") or "08:00")
    try:
        hour, minute = [int(x) for x in target_time.split(":", 1)]
    except Exception:
        hour, minute = 8, 0
    if now.hour != hour or now.minute != minute:
        return False

    last = parse_any_datetime(template.get("last_sent_at"))
    if last and last.date() == now.date():
        return False

    frequency = str(template.get("schedule_frequency") or "daily").lower()
    if frequency == "weekly" and now.weekday() != 0:
        return False
    if frequency == "monthly":
        day = max(1, min(31, int(template.get("schedule_day") or 1)))
        if now.day != min(day, 28 if now.month == 2 else day):
            return False
    return True


def scheduled_query_for_template(template):
    frequency = str(template.get("schedule_frequency") or "daily").lower()
    days = {"daily": 1, "weekly": 7, "monthly": 30}.get(frequency, 1)
    end = datetime.now()
    start = end - timedelta(days=days)
    return {
        "from": [start.date().isoformat()],
        "to": [end.date().isoformat()],
        "section": [template.get("section") or "all"],
        "device": [template.get("device") or ""],
    }


def send_template_report(cfg, template, username="system"):
    report = report_data(cfg, scheduled_query_for_template(template))
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    values = {"date": now_text, "report_type": template.get("name") or "Operation Center Report", "user": username, "rows": report["summary"]["total"]}
    subject = template_text(template.get("subject") or "Operation Center Report - {date}", values)
    body_text = template_text(template.get("body") or "", values)
    attachments = report_attachments(report, template.get("formats") or ["csv", "json"])
    return send_email(
        cfg,
        template.get("to"),
        subject,
        body_text,
        attachments=attachments,
        from_address=template.get("from_address") or cfg["modules"].get("email", {}).get("from_addresses"),
        cc=template.get("cc"),
        bcc=template.get("bcc"),
    )


def maybe_send_scheduled_emails():
    cfg = settings()
    email_cfg = cfg["modules"].get("email", {})
    if not email_cfg.get("enabled"):
        return
    changed = False
    for template in email_cfg.get("templates") or []:
        if not schedule_due(template):
            continue
        try:
            result = send_template_report(cfg, template)
            template["last_sent_at"] = utc_now()
            changed = True
            audit(f"scheduled_report_email_sent template={template.get('name','')} to={','.join(result['to'])} attachments={','.join(result['attachments'])}", "system")
        except Exception as exc:
            template["last_error"] = str(exc)
            changed = True
            audit(f"scheduled_report_email_failed template={template.get('name','')} error={exc}", "system", "ERROR")
    if changed:
        save_json(SETTINGS_FILE, cfg)


def email_scheduler_loop():
    while True:
        try:
            maybe_send_scheduled_emails()
        except Exception as exc:
            audit(f"email scheduler failed: {exc}", "system", "ERROR")
        time.sleep(60)


def start_email_scheduler():
    global EMAIL_THREAD
    if EMAIL_THREAD and EMAIL_THREAD.is_alive():
        return
    EMAIL_THREAD = threading.Thread(target=email_scheduler_loop, daemon=True)
    EMAIL_THREAD.start()
    audit("email scheduler started", "system")


def log_retention_loop():
    while True:
        try:
            cfg = settings()
            last = parse_any_datetime(cfg.get("log_retention", {}).get("last_cleanup_at"))
            if not last or (datetime.now() - last).total_seconds() > 6 * 3600:
                cleanup_log_retention(cfg)
        except Exception as exc:
            audit(f"log retention loop failed: {exc}", "system", "ERROR")
        time.sleep(3600)


def start_log_retention_scheduler():
    global LOG_RETENTION_THREAD
    if LOG_RETENTION_THREAD and LOG_RETENTION_THREAD.is_alive():
        return
    LOG_RETENTION_THREAD = threading.Thread(target=log_retention_loop, daemon=True)
    LOG_RETENTION_THREAD.start()
    audit("log retention scheduler started", "system")



OPENAPI_MODULES = [
    {"key": "overview", "label": "Overview"},
    {"key": "projects", "label": "Organization & Projects"},
    {"key": "devices", "label": "Devices"},
    {"key": "hms", "label": "HMS / Thing Model"},
    {"key": "livestream", "label": "Livestream"},
    {"key": "tasks", "label": "Flight Tasks & Records"},
    {"key": "waylines", "label": "Waylines"},
    {"key": "maps", "label": "Maps & Airspace"},
    {"key": "models", "label": "Models"},
    {"key": "explorer", "label": "API Explorer"},
    {"key": "logs", "label": "Logs"},
]

# Read-only catalogue. Every executable entry is GET-only.  V2 paths below are
# taken from DJI FlightHub 2 OpenAPI V2 documentation / Apifox catalogue.
# Write/control operations are intentionally excluded from AERO SYNC.
PUBLIC_V2_READ_ENDPOINTS = {
    "system_status": {"label":"System Status","path":"/openapi/v2.0/system_status","project":False,"module":"overview"},
    "system_health": {"label":"System Health","path":"/openapi/v2.0/health","project":False,"module":"overview"},
    "projects": {"label":"Project List","path":"/openapi/v2.0/project","project":False,"module":"projects"},
    "organization_devices": {"label":"Organization Device List","path":"/openapi/v2.0/device","project":False,"module":"devices"},
    "project_devices": {"label":"Project Device List","path":"/openapi/v2.0/project/device","project":True,"module":"devices"},
    "project_independent_members": {"label":"Project Independent Members","path":"/openapi/v2.0/members","project":True,"module":"projects"},
    "project_device_details": {"label":"Project Device Details","path":"/openapi/v2.0/project/device/{device_sn}","project":True,"module":"devices","path_params":["device_sn"]},
    "project_device_history_topology": {"label":"Project Device History Topology","path":"/openapi/v2.0/topologies/history","project":True,"module":"devices"},
    "hms": {"label":"HMS Information","path":"/openapi/v2.0/device/hms","project":True,"module":"hms","query_hint":"device_sn_list=1581... (comma-separated device SNs)"},
    "thing_model": {"label":"Thing Model / Device State","path":"/openapi/v2.0/device/{device_sn}/state","project":True,"module":"hms","path_params":["device_sn"]},
    "command_execution": {"label":"Device Command Execution Status","path":"/openapi/v2.0/organizations/{organization_uuid}/manage-devices/cmds","project":False,"module":"devices","path_params":["organization_uuid"],"query_hint":"device_sn=..."},
    "recording_tasks_project": {"label":"Recording Tasks - Project Device","path":"/openapi/v2.0/devices/{device_sn}/streams","project":True,"module":"livestream","path_params":["device_sn"]},
    "livestream_share_details": {"label":"Livestream Share Details","path":"/openapi/v2.0/live-shares/{device_sn}","project":True,"module":"livestream","path_params":["device_sn"]},
    "livestream_share_list": {"label":"Livestream Share List","path":"/openapi/v2.0/live-shares","project":True,"module":"livestream","query_hint":"page=1&page_size=20&status=0"},
    "flight_task_default_name": {"label":"Default Task Name","path":"/openapi/v2.0/workspaces/{workspace_id}/flight-tasks/default-name","project":False,"module":"tasks","path_params":["workspace_id"],"query_hint":"name=Inspection Task"},
    "flight_task_batch": {"label":"Flight Task Batch Details","path":"/openapi/v2.0/workspaces/{workspace_id}/flight-tasks/batch","project":False,"module":"tasks","path_params":["workspace_id"],"query_hint":"task_uuids=..."},
    "flight_task_track": {"label":"Flight Task Track","path":"/openapi/v2.0/flight-task/{task_uuid}/track","project":True,"module":"tasks","path_params":["task_uuid"]},
    "wayline_task_details": {"label":"Wayline Task Details","path":"/openapi/v2.0/flight-task/detail","project":True,"module":"tasks","query_hint":"workspace_id=...&task_uuid=..."},
    "flight_task_info": {"label":"Flight Task Information","path":"/openapi/v2.0/flight-task/{task_uuid}","project":True,"module":"tasks","path_params":["task_uuid"],"query_hint":"workspace_id=..."},
    "flight_task_list": {"label":"Flight Task List","path":"/openapi/v2.0/flight-task/list","project":True,"module":"tasks","query_hint":"sn=&name=&begin_at=&end_at=&status="},
    "flight_task_media": {"label":"Flight Task Media Resources","path":"/openapi/v2.0/flight-task/{task_uuid}/media","project":True,"module":"tasks","path_params":["task_uuid"]},
    "flight_record_export_history": {"label":"Flight Record Export History","path":"/openapi/v2.0/flight-task/export","project":True,"module":"tasks","query_hint":"page=1&page_size=20"},
    "flight_record_download_link": {"label":"Flight Record Download Link","path":"/openapi/v2.0/flight-task/oss-url-info/get","project":True,"module":"tasks","query_hint":"object_key=..."},
    "model_list": {"label":"Project Model List","path":"/openapi/v2.0/model","project":True,"module":"models"},
    "model_details": {"label":"Model Details","path":"/openapi/v2.0/model/{model_uuid}","project":True,"module":"models","path_params":["model_uuid"]},
    "model_download": {"label":"Model File Download Link","path":"/openapi/v2.0/model/download-url/{file_id}","project":True,"module":"models","path_params":["file_id"]},
    "open_model_details": {"label":"Open Modeling - Model Details","path":"/openapi/v2.0/open_model/models/{model_uuid}","project":True,"module":"models","path_params":["model_uuid"]},
    "open_model_running": {"label":"Open Modeling - In-Progress Models","path":"/openapi/v2.0/open_model/models/running","project":True,"module":"models"},
}

# V1 stays intentionally small because AERO SYNC is focused on current V2.
PUBLIC_V1_READ_ENDPOINTS = {
    "system_status": {"label":"System Status","path":"/openapi/v0.1/system_status","project":False,"module":"overview"},
    "projects": {"label":"Project List","path":"/openapi/v0.1/project","project":False,"module":"projects"},
    "organization_devices": {"label":"Organization Devices","path":"/openapi/v0.1/device","project":False,"module":"devices"},
    "project_devices": {"label":"Project Devices","path":"/openapi/v0.1/project/device","project":True,"module":"devices"},
}

# On-Premises V2 is a separate catalogue by design.  Only endpoints verified as
# common/safe in the current AERO SYNC baseline are enabled automatically.
# The read-only custom GET facility can query further documented On-Prem V2 GET
# endpoints without exposing POST/PUT/PATCH/DELETE.
ONPREM_V2_READ_ENDPOINTS = {
    "system_status": {"label":"System Status","path":"/openapi/v2.0/system_status","project":False,"module":"overview"},
    "projects": {"label":"Project List","path":"/openapi/v2.0/project","project":False,"module":"projects"},
    "organization_devices": {"label":"Organization Devices","path":"/openapi/v2.0/device","project":False,"module":"devices"},
    "project_devices": {"label":"Project Devices","path":"/openapi/v2.0/project/device","project":True,"module":"devices"},
}

OPENAPI_CATALOGUES = {
    ("cloud", "v1"): PUBLIC_V1_READ_ENDPOINTS,
    ("cloud", "v2"): PUBLIC_V2_READ_ENDPOINTS,
    ("onprem", "v2"): ONPREM_V2_READ_ENDPOINTS,
}

def openapi_catalogue(profile):
    platform = "onprem" if str(profile.get("platform") or "cloud").lower() == "onprem" else "cloud"
    version = "v1" if str(profile.get("api_version") or "v2").lower() in ("v1", "1", "1.0") else "v2"
    return OPENAPI_CATALOGUES.get((platform, version), {})


def openapi_capabilities(profile):
    catalogue = openapi_catalogue(profile)
    supported_modules = {str(item.get("module") or "") for item in catalogue.values()}
    platform = "onprem" if str(profile.get("platform") or "cloud").lower() == "onprem" else "cloud"
    version = "v1" if str(profile.get("api_version") or "v2").lower() in ("v1", "1", "1.0") else "v2"
    # DJI Public Cloud V2 catalogue documents read operations across these areas.
    # Some less frequently used APIs are intentionally accessed through the protected
    # custom-GET reader until their exact parameter schema is validated in AERO SYNC.
    if platform == "cloud" and version == "v2":
        supported_modules.update({"projects", "devices", "hms", "livestream", "tasks", "waylines", "maps", "models"})
    result = {}
    for module in OPENAPI_MODULES:
        key = module["key"]
        supported = key in ("overview", "explorer", "logs") or key in supported_modules
        if key == "explorer":
            supported = bool(catalogue)
        result[key] = {
            "supported": bool(supported),
            "message": "" if supported else "Not supported by this connection",
        }
    return result


def openapi_mode(cfg):
    return "onprem" if str((cfg.get("fh2") or {}).get("mode") or "cloud").lower() == "onprem" else "cloud"


def openapi_connections(cfg, enabled_only=False):
    module = (cfg.get("modules") or {}).get("openapi") or {}
    rows = []
    for raw in module.get("connections") or []:
        row = dict(raw or {})
        row["id"] = str(row.get("id") or "").strip()
        row["name"] = str(row.get("name") or "OpenAPI Connection").strip()
        row["platform"] = "onprem" if str(row.get("platform") or "cloud").lower() == "onprem" else "cloud"
        row["api_version"] = "v1" if str(row.get("api_version") or "v2").lower() in ("v1", "1", "1.0") else "v2"
        row["enabled"] = bool(row.get("enabled", True))
        row["base_url"] = str(row.get("base_url") or "").strip().rstrip("/")
        row["user_token"] = str(row.get("user_token") or "").strip()
        row["project_uuid"] = str(row.get("project_uuid") or "").strip()
        row["timeout_seconds"] = max(3, min(120, int(row.get("timeout_seconds") or 30)))
        row["verify_ssl"] = bool(row.get("verify_ssl", True))
        if row["id"] and (row["enabled"] or not enabled_only):
            rows.append(row)
    return rows


def openapi_profile(cfg, connection_id=None):
    module = (cfg.get("modules") or {}).get("openapi") or {}
    wanted = str(connection_id or module.get("active_connection_id") or "").strip()
    rows = openapi_connections(cfg)
    if wanted:
        for row in rows:
            if row["id"] == wanted:
                return row
    return rows[0] if rows else {"id":"", "name":"", "platform":"cloud", "api_version":"v2", "enabled":False, "base_url":"", "user_token":"", "project_uuid":"", "timeout_seconds":30, "verify_ssl":True}


def openapi_connection_slug(profile):
    raw = str(profile.get("name") or profile.get("id") or "connection").lower()
    slug = "".join(ch if ch.isalnum() else "-" for ch in raw).strip("-")
    return (slug or "connection")[:60]


def openapi_log_path(cfg, connection_id=None):
    profile = openapi_profile(cfg, connection_id)
    root = Path((cfg.get("storage") or {}).get("data_root_path") or DEFAULT_STORAGE_ROOT).expanduser().resolve()
    path = root / "openapi" / openapi_connection_slug(profile) / "openapi_requests.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def openapi_write_log(cfg, connection_id, endpoint_key, status, elapsed_ms, message="", ok=None, application_code=None):
    record = {
        "time": utc_now(),
        "connection_id": connection_id,
        "endpoint": endpoint_key,
        "status": int(status),
        "elapsed_ms": round(float(elapsed_ms), 1),
        "message": str(message or "")[:500],
        "ok": bool(ok) if ok is not None else (200 <= int(status or 0) < 300),
    }
    if application_code is not None:
        record["application_code"] = application_code
    try:
        with openapi_log_path(cfg, connection_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        diagnostic(f"openapi_log_failed error={exc}", "system", "WARN")


def openapi_read_logs(cfg, connection_id=None, limit=100):
    path = openapi_log_path(cfg, connection_id)
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(500, int(limit))):]:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return list(reversed(rows))


def openapi_resolve_path(endpoint, path_params=None):
    path = str(endpoint.get("path") or "")
    values = path_params or {}
    for name in endpoint.get("path_params") or []:
        value = str(values.get(name) or "").strip()
        if not value:
            raise ValueError(f"Missing required path parameter: {name}")
        if not re.fullmatch(r"[A-Za-z0-9._:-]+", value):
            raise ValueError(f"Invalid path parameter: {name}")
        path = path.replace("{" + name + "}", quote(value, safe=""))
    if "{" in path or "}" in path:
        raise ValueError("OpenAPI endpoint has unresolved path parameters")
    return path


def openapi_safe_custom_get_path(path, api_version):
    value = str(path or "").strip()
    prefix = "/openapi/v0.1/" if api_version == "v1" else "/openapi/v2.0/"
    if not value.startswith(prefix):
        raise ValueError(f"Custom GET path must start with {prefix}")
    if "?" in value or "#" in value or ".." in value or "\\" in value:
        raise ValueError("Custom GET path is invalid")
    if not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+", value):
        raise ValueError("Custom GET path contains unsupported characters")
    return value


def openapi_safe_url(base_url, endpoint_path, query=None):
    parsed = urlparse(str(base_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("OpenAPI Base URL must start with http:// or https://")
    base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
    url = base + endpoint_path
    if query:
        from urllib.parse import urlencode
        url += "?" + urlencode(query, doseq=True)
    return url


def openapi_get(cfg, endpoint_key, query=None, project_uuid=None, connection_id=None, path_params=None, custom_path=None):
    if not advanced_license_enabled():
        raise PermissionError("Advanced license required")
    profile = openapi_profile(cfg, connection_id)
    catalogue = openapi_catalogue(profile)
    endpoint = catalogue.get(str(endpoint_key or ""))
    if custom_path:
        endpoint = {"label": "Custom Read-Only GET", "path": openapi_safe_custom_get_path(custom_path, profile.get("api_version") or "v2"), "project": bool(project_uuid or profile.get("project_uuid")), "module": "explorer"}
        endpoint_key = "custom_get"
    if not endpoint:
        raise ValueError("Not supported by this connection")
    connection_id = profile.get("id") or ""
    mode = profile.get("platform") or "cloud"
    api_version = profile.get("api_version") or "v2"
    if not profile["base_url"]:
        raise ValueError(f"OpenAPI connection {profile.get('name') or connection_id or 'selected'} Base URL is not configured")
    if not profile["user_token"] and endpoint_key != "system_status":
        raise ValueError("OpenAPI Organization Key / X-User-Token is not configured")
    selected_project = str(project_uuid or profile.get("project_uuid") or "").strip()
    if endpoint.get("project") and not selected_project:
        raise ValueError("OpenAPI Project UUID is not configured")
    headers = {
        "Accept": "application/json",
        "X-Language": "en",
        "X-Request-Id": secrets.token_hex(16),
        "User-Agent": "AERO-SYNC-OpenAPI/1.0",
    }
    if profile["user_token"]:
        headers["X-User-Token"] = profile["user_token"]
    if selected_project:
        headers["X-Project-Uuid"] = selected_project
    url = openapi_safe_url(profile["base_url"], openapi_resolve_path(endpoint, path_params), query=query)
    context = None
    if url.lower().startswith("https://"):
        context = ssl.create_default_context() if profile["verify_ssl"] else ssl._create_unverified_context()
    started = time.perf_counter()
    status = 0
    message = ""
    try:
        req = urllib_request.Request(url, headers=headers, method="GET")
        with urllib_request.urlopen(req, timeout=profile["timeout_seconds"], context=context) as response:
            status = int(response.status or 200)
            raw = response.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            payload = {"raw": raw}
        message = str(payload.get("message") or "") if isinstance(payload, dict) else ""
        application_code = payload.get("code") if isinstance(payload, dict) else None
        app_ok = application_code in (None, 0, "0")
        ok = 200 <= status < 300 and app_ok
        if not ok and 200 <= status < 300 and not message:
            message = f"DJI OpenAPI error code {application_code}"
        return {"ok": ok, "http_status": status, "application_code": application_code, "endpoint": endpoint_key, "mode": mode, "api_version": api_version, "connection_id": connection_id, "connection_name": profile.get("name"), "error": "" if ok else message, "data": payload}
    except urllib_error.HTTPError as exc:
        status = int(exc.code or 500)
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            payload = {"raw": raw}
        message = str(payload.get("message") or exc.reason or "HTTP error") if isinstance(payload, dict) else str(exc.reason or "HTTP error")
        return {"ok": False, "http_status": status, "endpoint": endpoint_key, "mode": mode, "api_version": api_version, "connection_id": connection_id, "connection_name": profile.get("name"), "error": message, "data": payload}
    except Exception as exc:
        status = 0
        message = str(exc)
        return {"ok": False, "http_status": 0, "endpoint": endpoint_key, "mode": mode, "api_version": api_version, "connection_id": connection_id, "connection_name": profile.get("name"), "error": message}
    finally:
        try:
            log_app_code = payload.get("code") if isinstance(locals().get("payload"), dict) else None
            log_ok = (200 <= int(status or 0) < 300) and log_app_code in (None, 0, "0")
        except Exception:
            log_app_code = None
            log_ok = False
        openapi_write_log(cfg, connection_id, endpoint_key, status, (time.perf_counter() - started) * 1000, message, ok=log_ok, application_code=log_app_code)


def openapi_overview(cfg, connection_id=None):
    profile = openapi_profile(cfg, connection_id)
    logs = openapi_read_logs(cfg, profile.get("id"), 100) if profile.get("id") else []
    success = sum(1 for row in logs if bool(row.get("ok", 200 <= int(row.get("status") or 0) < 300)))
    failed = len(logs) - success
    avg = round(sum(float(row.get("elapsed_ms") or 0) for row in logs) / len(logs), 1) if logs else 0
    public_connections = []
    for row in openapi_connections(cfg, enabled_only=True):
        public_connections.append({
            "id": row["id"], "name": row["name"], "platform": row["platform"],
            "api_version": row.get("api_version") or "v2",
            "platform_label": fh2_mode_label(row["platform"]),
            "version_label": "V1.0" if (row.get("api_version") or "v2") == "v1" else "V2.0",
            "configured": bool(row["base_url"] and row["user_token"]),
            "project_uuid": row["project_uuid"],
        })
    catalogue = openapi_catalogue(profile)
    version = profile.get("api_version") or "v2"
    return {
        "connection_id": profile.get("id"),
        "connection_name": profile.get("name"),
        "mode": profile.get("platform"),
        "mode_label": fh2_mode_label(profile.get("platform")),
        "api_version": version,
        "version_label": "V1.0" if version == "v1" else "V2.0",
        "configured": bool(profile.get("base_url") and profile.get("user_token")),
        "base_url": profile.get("base_url"),
        "project_uuid": profile.get("project_uuid"),
        "token_configured": bool(profile.get("user_token")),
        "connections": public_connections,
        "capabilities": openapi_capabilities(profile),
        "modules": OPENAPI_MODULES,
        "allowed_endpoints": [{"key": key, **value} for key, value in catalogue.items()],
        "custom_get_enabled": True,
        "statistics": {"requests": len(logs), "success": success, "failed": failed, "average_ms": avg, "last_sync": logs[0].get("time") if logs else ""},
    }

DFR_STATUSES = ("Event Received", "Event Sent to FH2", "Failed to Sent", "Cancelled")
DFR_EVENT_QUEUE_FILE = DATA_DIR / "dfr" / "dfr_events.json"
DFR_LOCK = threading.RLock()


def dfr_storage_root(cfg):
    raw = cfg.get("storage", {}).get("data_root_path") or str(DATA_DIR.resolve())
    path = safe_path(raw)
    return path or DATA_DIR


def dfr_event_file(cfg=None):
    cfg = cfg or settings()
    return dfr_storage_root(cfg) / "dfr" / "dfr_events.json"


def dfr_log_file(cfg):
    raw = cfg.get("modules", {}).get("dfr", {}).get("log_path")
    path = safe_path(raw) if raw else None
    return path or (dfr_storage_root(cfg) / "logs" / "dfr" / "dfr.log")


def dfr_log(cfg, provider, status, message, level="INFO"):
    path = dfr_log_file(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    provider = str(provider or "DFR").upper()
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [{level}] [{provider}] {status}: {message}\n"
    with DFR_LOCK:
        with path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(line)
        provider_path = path.parent / f"dfr_{provider.lower()}.log"
        with provider_path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(line)


def dfr_load_events(cfg=None):
    path = dfr_event_file(cfg) if cfg else DFR_EVENT_QUEUE_FILE
    with DFR_LOCK:
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8-sig") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []


def dfr_save_events(cfg, events):
    path = dfr_event_file(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, list(events)[:2000])


def dfr_json_hash(payload):
    try:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        raw = str(payload)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def dfr_nested_value(payload, key):
    current = payload if isinstance(payload, dict) else {}
    for part in str(key or "").split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(part)
    return current if current not in (None, "") else ""


def dfr_pick(payload, keys):
    for key in keys:
        value = dfr_nested_value(payload, key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def dfr_alarm_source_name(payload):
    return dfr_pick(payload, (
        "alarm_source_name", "alarmSourceName", "alarm.source_name", "alarm.sourceName",
        "camera_name", "cameraName", "camera", "device_name", "deviceName",
        "channel_name", "channelName", "source_name", "sourceName",
    ))


def dfr_match_hikvision_project(provider_cfg, payload):
    alarm_name = dfr_alarm_source_name(payload)
    cameras = provider_cfg.get("cameras") if isinstance(provider_cfg.get("cameras"), list) else []
    docks = provider_cfg.get("docks") if isinstance(provider_cfg.get("docks"), list) else []
    active_docks = [
        dock for dock in docks
        if isinstance(dock, dict) and str(dock.get("project_uuid") or dock.get("uuid") or "").strip()
    ]
    if not alarm_name:
        if len(active_docks) == 1:
            return str(active_docks[0].get("project_uuid") or active_docks[0].get("uuid") or "").strip(), ""
        return "", "Hikvision payload missing alarm_source_name"
    alarm_lc = alarm_name.casefold()
    for cam in cameras:
        if not isinstance(cam, dict):
            continue
        source = str(cam.get("alarm_source_name") or "").strip()
        dock_name = str(cam.get("dock_name") or cam.get("dock") or "").strip()
        if not source or not dock_name:
            continue
        source_lc = source.casefold()
        if source_lc == alarm_lc or source_lc in alarm_lc or alarm_lc in source_lc:
            for dock in docks:
                if not isinstance(dock, dict):
                    continue
                if str(dock.get("name") or "").strip().casefold() == dock_name.casefold():
                    project_uuid = str(dock.get("project_uuid") or dock.get("uuid") or "").strip()
                    if project_uuid:
                        return project_uuid, ""
                    return "", f"Dock mapping has no DJI Project UUID: {dock_name}"
            return "", f"Camera matched but Dock mapping not found: {dock_name}"
    if len(active_docks) == 1:
        return str(active_docks[0].get("project_uuid") or active_docks[0].get("uuid") or "").strip(), ""
    return "", f"No camera mapping matched alarm source: {alarm_name}"


def dfr_project_from_payload(provider_cfg, payload, provider_key=""):
    direct_project_uuid = str(
        payload.get("project_uuid")
        or payload.get("projectUuid")
        or payload.get("project_id")
        or payload.get("projectId")
        or payload.get("project")
        or ""
    ).strip()
    if direct_project_uuid.upper() in {"DJI_PROJECT_UUID", "PROJECT_UUID", "PROJECT_ID", "XXXX", "XXXXX"}:
        direct_project_uuid = ""
    if direct_project_uuid:
        return direct_project_uuid, ""
    if provider_key == "hikvision":
        return dfr_match_hikvision_project(provider_cfg, payload)
    project_uuid = str(
        payload.get("project_uuid")
        or payload.get("projectUuid")
        or payload.get("project_id")
        or payload.get("projectId")
        or payload.get("project")
        or provider_cfg.get("default_project_id")
        or ""
    ).strip()
    return project_uuid, ""


def dfr_event_name(payload):
    for key in ("event", "eventName", "event_name", "type", "alarm_type", "name", "rule", "trigger"):
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for key in ("event", "type", "alarm_type", "name"):
        value = nested.get(key)
        if value not in (None, ""):
            return str(value)
    return "DFR Event"


def dfr_provider_key(provider):
    return "scylla" if str(provider or "").lower() == "scylla" else "hikvision"


DFR_DEDUPE_SECONDS = 10


def dfr_parse_time(value):
    try:
        if not value:
            return None
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def dfr_record_event(cfg, provider, event_name, status="Event Received", project_uuid="", raw=None, source_ip="", message="", attempts=0):
    if status not in DFR_STATUSES:
        status = "Event Received"
    raw = raw if isinstance(raw, dict) else {"payload": raw}
    provider = str(provider or "DFR")
    dedupe_key = dfr_json_hash({"provider": provider.lower(), "event": event_name, "project_uuid": project_uuid, "raw": raw})
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    with DFR_LOCK:
        events = dfr_load_events(cfg)
        for existing in events[:50]:
            if existing.get("dedupe_key") == dedupe_key:
                last_seen = dfr_parse_time(existing.get("last_duplicate_at")) or dfr_parse_time(existing.get("received_at"))
                if last_seen and (now_dt - last_seen).total_seconds() <= DFR_DEDUPE_SECONDS:
                    existing["duplicate_count"] = int(existing.get("duplicate_count") or 1) + 1
                    existing["last_duplicate_at"] = now_iso
                    existing["message"] = existing.get("message") or message
                    # Do not write/log every rapid duplicate; this protects the UI and disk if a VMS retries fast.
                    return existing
                break
        event = {
            "id": int(time.time() * 1000),
            "received_at": now_iso,
            "updated_at": now_iso,
            "provider": provider,
            "event": str(event_name or "event"),
            "project_uuid": str(project_uuid or ""),
            "status": status,
            "source_ip": str(source_ip or ""),
            "message": str(message or ""),
            "attempts": int(attempts or 0),
            "retry_max": int(cfg.get("modules", {}).get("dfr", {}).get("retry_max") or 3),
            "dedupe_key": dedupe_key,
            "duplicate_count": 1,
            "raw": raw,
        }
        events.insert(0, event)
        dfr_save_events(cfg, events)
    dfr_log(cfg, provider, status, message or event["event"])
    return event


def dfr_update_event(cfg, event_id, **updates):
    with DFR_LOCK:
        events = dfr_load_events(cfg)
        for event in events:
            if str(event.get("id")) == str(event_id):
                event.update(updates)
                event["updated_at"] = utc_now()
                dfr_save_events(cfg, events)
                return event
    return None


def dfr_today_count(events):
    today = datetime.now(timezone(timedelta(hours=4))).date()
    total = 0
    for event in events:
        dt = parse_any_datetime(event.get("received_at"))
        if dt and dt.astimezone(timezone(timedelta(hours=4))).date() == today:
            total += 1
    return total


def dfr_provider_summary(cfg):
    dfr_cfg = cfg.get("modules", {}).get("dfr", {})
    return [
        {
            "name": "Scylla",
            "enabled": bool(dfr_cfg.get("scylla", {}).get("enabled")),
            "endpoint": "/dfr/scylla",
            "permission": "dfr_view",
            "status": "Ready" if dfr_cfg.get("scylla", {}).get("enabled") else "Disabled",
        },
        {
            "name": "Hikvision",
            "enabled": bool(dfr_cfg.get("hikvision", {}).get("enabled")),
            "endpoint": "/dfr/hikvision",
            "permission": "dfr_view",
            "status": "Ready" if dfr_cfg.get("hikvision", {}).get("enabled") else "Disabled",
        },
    ]


def dfr_data(cfg, limit=50):
    events = dfr_load_events(cfg)
    return {
        "enabled": bool(cfg.get("modules", {}).get("dfr", {}).get("enabled", True)),
        "retry_max": int(cfg.get("modules", {}).get("dfr", {}).get("retry_max") or 3),
        "today_count": dfr_today_count(events),
        "statuses": list(DFR_STATUSES),
        "providers": dfr_provider_summary(cfg),
        "events": events[:limit],
        "last5": events[:5],
        "log_path": str(dfr_log_file(cfg)),
        "queue_path": str(dfr_event_file(cfg)),
    }


def dfr_status_messages(events, limit=5):
    result = []
    for event in (events or [])[:limit]:
        status = event.get("status") or "Event Received"
        level = "ok" if status == "Event Sent to FH2" else "warn" if status == "Failed to Sent" else "info"
        result.append({
            "level": level,
            "title": status,
            "message": f"{event.get('provider', 'DFR')} | {event.get('event', 'event')}",
            "time": event.get("received_at", ""),
            "source": event.get("project_uuid") or event.get("source_ip", ""),
        })
    return result


def dfr_build_fh2_payload(event, workflow_uuid=""):
    raw = event.get("raw") if isinstance(event.get("raw"), dict) else {}
    alarm_source = dfr_alarm_source_name(raw)
    latitude = dfr_pick(raw, ("latitude", "lat", "location.latitude", "gps.latitude"))
    longitude = dfr_pick(raw, ("longitude", "lng", "lon", "location.longitude", "gps.longitude"))
    description = dfr_pick(raw, ("description", "message", "desc", "alarm_description"))
    try:
        latitude = float(latitude or 0)
    except Exception:
        latitude = 0
    try:
        longitude = float(longitude or 0)
    except Exception:
        longitude = 0
    common_cfg = settings().get("modules", {}).get("dfr", {}).get("common", {})
    try:
        alert_level = int(common_cfg.get("alert_level", 3) or 3)
    except Exception:
        alert_level = 3
    alert_level = max(1, min(5, alert_level))
    payload = {
        "workflow_uuid": workflow_uuid or common_cfg.get("workflow_uuid", ""),
        "trigger_type": 0,
        "name": f"{alarm_source or event.get('provider') or 'DFR'} | {event.get('received_at') or ''}".strip(),
        "params": {
            "creator": alarm_source or event.get("provider") or "DFR",
            "latitude": latitude,
            "longitude": longitude,
            "level": alert_level,
            "desc": description or event.get("event") or "DFR event",
        },
    }
    if str(event.get("provider") or "").lower() == "hikvision":
        payload["params"]["source"] = "hikvision"
    return payload


def dfr_send_to_fh2(cfg, event):
    dfr_cfg = cfg.get("modules", {}).get("dfr", {})
    common = dfr_cfg.get("common", {})
    endpoint = str(common.get("fh2_endpoint") or "").strip()
    workflow_uuid = str(common.get("workflow_uuid") or "").strip()
    if not endpoint:
        return False, "FH2 endpoint not configured"
    if "{workflow_uuid}" in endpoint:
        endpoint = endpoint.replace("{workflow_uuid}", quote(workflow_uuid, safe=""))
    body = json.dumps(dfr_build_fh2_payload(event, workflow_uuid), ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8", "User-Agent": "AERO-SYNC-DFR/1.0"}
    project_uuid = str(event.get("project_uuid") or "").strip()
    if project_uuid.upper() in {"DJI_PROJECT_UUID", "PROJECT_UUID", "PROJECT_ID", "XXXX", "XXXXX"}:
        project_uuid = ""
    if not project_uuid:
        hikvision_cfg = dfr_cfg.get("hikvision", {}) if isinstance(dfr_cfg.get("hikvision"), dict) else {}
        docks = hikvision_cfg.get("docks") if isinstance(hikvision_cfg.get("docks"), list) else []
        for dock in docks:
            if not isinstance(dock, dict):
                continue
            project_uuid = str(dock.get("project_uuid") or dock.get("uuid") or "").strip()
            if project_uuid:
                break
    if project_uuid:
        headers["x-project-uuid"] = project_uuid
    org_key = str(common.get("organization_key") or "").strip()
    if org_key:
        headers["X-User-Token"] = org_key
    req = urllib_request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        context = ssl._create_unverified_context() if endpoint.lower().startswith("https://") else None
        with urllib_request.urlopen(req, timeout=10, context=context) as resp:
            text = resp.read(4096).decode("utf-8", errors="replace")
            if 200 <= int(resp.status) < 300:
                try:
                    result = json.loads(text) if text.strip() else {}
                except Exception:
                    result = {}
                code = result.get("code") if isinstance(result, dict) else None
                if code in (None, 0, "0"):
                    return True, f"FH2 accepted event. HTTP {resp.status} {text[:240]}".strip()
                return False, f"FH2 API error code {code}: {text[:240]}".strip()
            return False, f"FH2 returned HTTP {resp.status} {text[:240]}".strip()
    except urllib_error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
        return False, f"FH2 HTTP {exc.code}: {detail[:240]}".strip()
    except Exception as exc:
        return False, str(exc)


def dfr_pending_events(cfg):
    retry_max = int(cfg.get("modules", {}).get("dfr", {}).get("retry_max") or 3)
    pending = []
    for event in dfr_load_events(cfg):
        status = event.get("status")
        attempts = int(event.get("attempts") or 0)
        if status in ("Event Received", "Failed to Sent") and attempts < retry_max:
            pending.append(event)
    return list(reversed(pending[:100]))


def dfr_process_queue_once(cfg):
    if not cfg.get("modules", {}).get("dfr", {}).get("enabled", True):
        return 0
    processed = 0
    for event in dfr_pending_events(cfg):
        attempts = int(event.get("attempts") or 0) + 1
        dfr_update_event(cfg, event.get("id"), attempts=attempts, status="Event Received", message=f"Sending to FH2 attempt {attempts}")
        ok, message = dfr_send_to_fh2(cfg, event)
        if ok:
            dfr_update_event(cfg, event.get("id"), status="Event Sent to FH2", message=message, last_error="", sent_at=utc_now())
            dfr_log(cfg, event.get("provider"), "Event Sent to FH2", f"id={event.get('id')} {message}")
        else:
            retry_max = int(cfg.get("modules", {}).get("dfr", {}).get("retry_max") or 3)
            status = "Failed to Sent"
            dfr_update_event(cfg, event.get("id"), status=status, message=message, last_error=message)
            dfr_log(cfg, event.get("provider"), status, f"id={event.get('id')} attempt={attempts}/{retry_max} {message}", "ERROR")
        processed += 1
    return processed



def dfr_test_payload_for_provider(cfg, provider):
    dfr_cfg = cfg.get("modules", {}).get("dfr", {})
    provider_key = dfr_provider_key(provider)
    provider_cfg = dfr_cfg.get(provider_key, {})
    if provider_key == "hikvision":
        return {
            "source": "hikvision",
            "event": "AERO_SYNC_DFR_TEST",
            "alarm_source_name": "AERO SYNC Test",
            "alarm_source_type": "manual_test",
            "description": "AERO SYNC FH2 DFR test",
            "latitude": "0",
            "longitude": "0",
            "test": True,
            "test_id": utc_now(),
        }, ""
    project_uuid = str(provider_cfg.get("default_project_id") or "").strip()
    projects = dfr_cfg.get("projects") if isinstance(dfr_cfg.get("projects"), list) else []
    if not project_uuid:
        project_uuid = next((str(item.get("uuid") or "").strip() for item in projects if isinstance(item, dict) and str(item.get("uuid") or "").strip()), "")
    if not project_uuid:
        return None, "Scylla test requires Default DJI Project UUID"
    return {
        "source": "scylla",
        "event": "AERO_SYNC_DFR_TEST",
        "project_uuid": project_uuid,
        "description": "AERO SYNC FH2 DFR test",
        "latitude": "0",
        "longitude": "0",
        "test": True,
    }, ""

def dfr_worker_loop():
    while not DFR_STOP_EVENT.wait(3):
        try:
            dfr_process_queue_once(settings())
        except Exception as exc:
            try:
                dfr_log(settings(), "DFR", "Failed to Sent", f"DFR worker error: {exc}", "ERROR")
            except Exception:
                pass


def start_dfr_worker():
    global DFR_WORKER_THREAD
    if DFR_WORKER_THREAD and DFR_WORKER_THREAD.is_alive():
        return
    DFR_STOP_EVENT.clear()
    DFR_WORKER_THREAD = threading.Thread(target=dfr_worker_loop, daemon=True, name="DFRWorker")
    DFR_WORKER_THREAD.start()


def stop_dfr_worker():
    global DFR_WORKER_THREAD
    DFR_STOP_EVENT.set()
    if DFR_WORKER_THREAD and DFR_WORKER_THREAD.is_alive():
        DFR_WORKER_THREAD.join(timeout=2)
    DFR_WORKER_THREAD = None


def process_dfr_webhook(provider, raw_body, headers, source_ip):
    if not advanced_license_enabled():
        return 403, advanced_license_error()
    cfg = settings()
    if not cfg.get("modules", {}).get("dfr", {}).get("enabled", True):
        event = dfr_record_event(cfg, provider, "disabled", "Cancelled", raw={"reason": "DFR module disabled"}, source_ip=source_ip, message="DFR module disabled")
        return 503, {"ok": False, "status": event["status"], "error": "DFR module disabled"}
    try:
        payload = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
    except Exception as exc:
        event = dfr_record_event(cfg, provider, "invalid_json", "Failed to Sent", raw={"error": str(exc)}, source_ip=source_ip, message=f"Invalid JSON: {exc}")
        return 400, {"ok": False, "status": event["status"], "error": "Invalid JSON"}

    provider_key = dfr_provider_key(provider)
    provider_cfg = cfg.get("modules", {}).get("dfr", {}).get(provider_key, {})
    if not provider_cfg.get("enabled"):
        event = dfr_record_event(cfg, provider, "provider_disabled", "Cancelled", raw=payload, source_ip=source_ip, message=f"{provider} DFR provider disabled")
        return 403, {"ok": False, "status": event["status"], "error": f"{provider} DFR provider disabled"}

    token = str(provider_cfg.get("bearer_token") or provider_cfg.get("token") or "").strip()
    if token:
        auth = str(headers.get("Authorization") or "")
        incoming_token = auth.replace("Bearer", "", 1).strip() if auth.lower().startswith("bearer") else str(headers.get("X-DFR-Token") or "").strip()
        if not secrets.compare_digest(incoming_token, token):
            event = dfr_record_event(cfg, provider, "auth_failed", "Cancelled", raw={"reason": "Invalid token"}, source_ip=source_ip, message="Invalid DFR token")
            return 401, {"ok": False, "status": event["status"], "error": "Invalid DFR token"}

    event_name = dfr_event_name(payload)
    project_uuid, project_error = dfr_project_from_payload(provider_cfg, payload, provider_key)
    if project_error:
        retry_max = int(cfg.get("modules", {}).get("dfr", {}).get("retry_max") or 3)
        event = dfr_record_event(cfg, provider, event_name, "Failed to Sent", project_uuid=project_uuid, raw=payload, source_ip=source_ip, message=project_error, attempts=retry_max)
        return 200, {"ok": False, "status": event["status"], "id": event["id"], "error": project_error}
    event = dfr_record_event(cfg, provider, event_name, "Event Received", project_uuid=project_uuid, raw=payload, source_ip=source_ip, message="Event received and queued")
    try:
        dfr_process_queue_once(cfg)
    except Exception as exc:
        dfr_log(cfg, provider, "Failed to Sent", f"Immediate DFR queue processing failed: {exc}", "ERROR")
    updated = next((item for item in dfr_load_events(cfg) if str(item.get("id")) == str(event.get("id"))), event)
    return 200, {"ok": True, "status": updated.get("status"), "id": event["id"], "message": "DFR event received"}


def sync_network_derived_settings(cfg):
    local_ip = cfg.get("network", {}).get("local_ip", "").strip()
    ports = cfg.get("ports", {})
    if not local_ip:
        return
    modules = cfg.setdefault("modules", {})
    s3 = modules.setdefault("local_s3", {})
    local_s3_port = ports.get("local_s3") or DEFAULT_PORTS["local_s3"]
    s3["endpoint"] = f"http://{local_ip}:{local_s3_port}"

def dashboard_payload(cfg):
    edition = active_license_edition()
    advanced = edition == "Advanced"
    events = event_data(cfg, 5)
    mqtt = mqtt_data(cfg, 5, include_payload=True)
    media = media_data(cfg, 5) if advanced else {"count": 0, "available": False, "files": []}
    live_map = map_data(cfg, 300)
    nvr = nvr_sync_status(cfg) if advanced else {"used_channels": 0, "enabled": False, "servers": []}
    dfr = dfr_data(cfg, 5) if advanced else {"today_count": 0, "enabled": False, "last5": []}
    channels = cfg["modules"]["live_streams"]["channels"]
    enabled = [c for c in channels if c.get("enabled")]
    return {
        "cards": [
            {"name": "Events", "value": events["count"], "status": "ready" if events["available"] else "path not set", "port": cfg["ports"]["event_api"]},
            {"name": "MQTT", "value": mqtt["count"], "status": "ready" if mqtt["available"] else "path not set", "port": cfg["ports"]["mqtt_broker"]},
            {"name": "Media / S3", "value": media["count"], "status": "ready" if media["available"] else "storage empty", "port": cfg["ports"]["local_s3"]},
            {"name": "Live Streams", "value": len(enabled), "status": f"{len(enabled)} enabled", "port": cfg["ports"]["stream_bridge"]},
            {"name": "Live Map", "value": live_map["device_count"], "status": "ready" if live_map["device_count"] else "waiting GPS", "port": "-"},
            {"name": "NVR Sync", "value": nvr["used_channels"], "status": "ready" if nvr["enabled"] else "disabled", "port": "SDK"},
            {"name": "DFR", "value": dfr["today_count"], "status": "ready" if dfr["enabled"] else "disabled", "port": cfg["ports"].get("dfr", 19007)},
            {"name": "Logs", "value": available_log_count(cfg), "status": "ready", "port": "-"},
            {"name": "OpenAPI", "value": "Ready" if openapi_overview(cfg).get("configured") else "Setup", "status": openapi_overview(cfg).get("mode_label"), "port": "GET"},
            {"name": "Settings", "value": "OK", "status": "configured", "port": "-"},
        ],
        "recent_events": events["events"],
        "recent_mqtt": mqtt["messages"],
        "event_status_messages": event_human_status(events["events"], 6),
        "dfr_status_messages": dfr_status_messages(dfr["last5"], 5),
        "recent_dfr": dfr["last5"],
        "mqtt_status_messages": mqtt_human_status(
            mqtt.get("messages", []),
            cfg.get("modules", {}).get("map", {}).get("online_timeout_seconds", 90),
            limit=6,
        ),
        "recent_media": media["files"],
        "map_devices": live_map["devices"][:5],
        "nvr_servers": nvr.get("servers", []),
        "ports": cfg["ports"],
        "urls": visible_urls(cfg),
        "resources": server_resources(cfg, max_age_seconds=60, allow_probe=False),
        "license_edition": edition,
        "advanced_enabled": advanced,
    }


def create_backup(cfg):
    import zipfile

    backup_cfg = cfg.get("backup", {})
    backup_root = Path(backup_cfg.get("backup_path") or (DATA_DIR / "backups"))
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_root / f"OperationCenter_Backup_{stamp}.zip"
    tmp_target = backup_root / f"OperationCenter_Backup_{stamp}.tmp"

    # Automatic backups are intentionally configuration-only. Module payloads
    # such as S3 media, recordings, and MQTT capture logs can be very large.
    include_paths = [
        SETTINGS_FILE,
        USERS_FILE,
        AUDIT_FILE,
        CERT_DIR,
        LICENSE_FILE,
        DATA_DIR / "events.db",
        DATA_DIR / "config",
        DATA_DIR / "users",
        DATA_DIR / "certificates",
    ]

    try:
        with zipfile.ZipFile(tmp_target, "w", zipfile.ZIP_DEFLATED) as zf:
            manifest = {
                "created_at": utc_now(),
                "app": APP_NAME,
                "backup_type": "configuration",
                "excluded": ["S3/media payloads", "recordings", "MQTT capture logs", "backup archives"],
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

            seen = set()
            for p in include_paths:
                p = Path(p)
                if not p.exists() or str(p.resolve()) in seen:
                    continue
                seen.add(str(p.resolve()))
                try:
                    p.resolve().relative_to(backup_root.resolve())
                    continue
                except ValueError:
                    pass
                if p.is_file():
                    zf.write(p, f"config/{p.name}")
                elif p.is_dir():
                    for child in p.rglob("*"):
                        if not child.is_file():
                            continue
                        name = child.name.lower()
                        if name.endswith((".zip", ".tmp")) or "mqtt_capture" in name:
                            continue
                        zf.write(child, f"config/{p.name}/{child.relative_to(p)}")
        tmp_target.replace(target)
    except Exception:
        tmp_target.unlink(missing_ok=True)
        raise

    cfg["backup"]["last_backup_at"] = utc_now()
    save_json(SETTINGS_FILE, cfg)
    cleanup_backups(cfg)
    return {"path": str(target), "created_at": cfg["backup"]["last_backup_at"]}


def cleanup_backups(cfg):
    backup_cfg = cfg.get("backup", {})
    backup_root = Path(backup_cfg.get("backup_path") or (DATA_DIR / "backups"))
    if not backup_root.exists():
        return
    retention_days = int(backup_cfg.get("retention_days") or 2)
    cutoff = time.time() - (retention_days * 86400)
    backups = sorted(backup_root.glob("OperationCenter_Backup_*.zip"), key=lambda p: p.stat().st_mtime)
    for path in backups:
        try:
            if path.stat().st_mtime < cutoff and len(backups) > 1:
                path.unlink()
        except Exception:
            pass


def log_category_retention_days(path, retention):
    name = path.name.lower()
    if "audit" in name:
        return int(retention.get("audit_retention_days") or 90)
    if "mqtt_capture" in name:
        return int(retention.get("mqtt_capture_retention_days") or 30)
    return int(retention.get("module_retention_days") or 30)


def rotate_log_file(path, retention, summary):
    if not path.exists() or not path.is_file() or path.stat().st_size == 0:
        return
    max_bytes = int(retention.get("max_log_size_mb") or 100) * 1024 * 1024
    today = datetime.now().strftime("%Y-%m-%d")
    should_rotate = path.stat().st_size > max_bytes
    if retention.get("daily_rotation", True):
        modified_day = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        should_rotate = should_rotate or modified_day < today
    if not should_rotate:
        return
    target = path.with_name(f"{path.stem}-{datetime.fromtimestamp(path.stat().st_mtime):%Y-%m-%d_%H%M%S}{path.suffix}")
    try:
        path.replace(target)
        path.write_text("", encoding="utf-8")
        summary["rotated"] += 1
        if retention.get("compress_old_logs", False):
            zip_target = target.with_suffix(target.suffix + ".zip")
            with zipfile.ZipFile(zip_target, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(target, target.name)
            target.unlink()
            summary["compressed"] += 1
    except PermissionError:
        summary["locked"] += 1
    except Exception as exc:
        summary["errors"].append(f"{path.name}: {exc}")


def enforce_log_storage_limits(cleanup_roots, current_log_keys, retention, summary):
    drive_limit = float(retention.get("drive_usage_limit_percent") or 80)
    drive_limit = max(50.0, min(98.0, drive_limit))
    candidates = []
    total_size = 0
    checked_drives = {}

    for root in cleanup_roots:
        if not root.exists():
            continue
        try:
            usage = shutil.disk_usage(root)
            used_percent = ((usage.total - usage.free) / usage.total * 100) if usage.total else 0
            checked_drives[str(root.resolve())] = {
                "free": usage.free,
                "total": usage.total,
                "used_percent": used_percent,
            }
        except Exception:
            pass
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            lower_name = path.name.lower()
            is_log_file = path.suffix.lower() == ".log" or ".log." in lower_name or lower_name.endswith(".log.zip")
            if not is_log_file:
                continue
            try:
                key = str(path.resolve())
                st = path.stat()
            except Exception:
                continue
            total_size += st.st_size
            candidates.append({
                "path": path,
                "key": key,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "active": key in current_log_keys,
            })

    drive_over_limit = any(item["used_percent"] >= drive_limit for item in checked_drives.values())
    if not drive_over_limit:
        summary["total_log_size"] = total_size
        summary["drive_over_limit"] = False
        return

    # Delete oldest rotated logs first. Active logs are rotated elsewhere and
    # are not removed while the service is running.
    candidates.sort(key=lambda item: (item["active"], item["mtime"]))
    for item in candidates:
        if item["active"]:
            continue
        try:
            if all(
                (((usage.total - usage.free) / usage.total * 100) if usage.total else 0) < drive_limit
                for usage in (shutil.disk_usage(root) for root in cleanup_roots if root.exists())
            ):
                break
        except Exception:
            pass
        try:
            item["path"].unlink()
            total_size -= item["size"]
            summary["deleted"] += 1
            summary["space_deleted"] += 1
        except PermissionError:
            summary["locked"] += 1
        except Exception as exc:
            summary["errors"].append(f"{item['path'].name}: {exc}")

    summary["total_log_size"] = total_size
    summary["drive_over_limit"] = drive_over_limit


def cleanup_log_retention(cfg):
    retention = cfg.get("log_retention", {})
    summary = {"rotated": 0, "deleted": 0, "compressed": 0, "locked": 0, "space_deleted": 0, "event_rows_deleted": 0, "total_log_size": 0, "drive_over_limit": False, "errors": []}
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    current_logs = [
        AUDIT_FILE,
        LOG_DIR / "event_receiver.log",
        LOG_DIR / "mqtt_dashboard.log",
        LOG_DIR / "mqtt_capture.log",
        LOG_DIR / "local_s3.log",
        LOG_DIR / "stream_module.log",
    ]
    for active_path in (
        cfg["modules"]["event_receiver"].get("log_path"),
        cfg["modules"]["mqtt"].get("capture_log_path"),
        cfg["modules"]["mqtt"].get("dashboard_log_path"),
        cfg["modules"]["local_s3"].get("log_path"),
    ):
        p = safe_path(active_path)
        if p:
            current_logs.append(p)
    current_logs.extend(LOG_DIR.glob("stream_channel_*.ffmpeg.log"))
    seen_current = set()
    for path in current_logs:
        try:
            key = str(Path(path).resolve())
        except Exception:
            key = str(path)
        if key in seen_current:
            continue
        seen_current.add(key)
        rotate_log_file(Path(path), retention, summary)

    now = time.time()
    cleanup_roots = {LOG_DIR}
    data_root = safe_path(cfg.get("storage", {}).get("data_root_path"))
    if data_root:
        cleanup_roots.add(data_root)
    for root in cleanup_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            lower_name = path.name.lower()
            is_log_file = path.suffix.lower() == ".log" or ".log." in lower_name or lower_name.endswith(".log.zip")
            if not is_log_file:
                continue
            try:
                if str(path.resolve()) in seen_current:
                    continue
            except Exception:
                pass
            days = log_category_retention_days(path, retention)
            try:
                if now - path.stat().st_mtime > days * 86400:
                    path.unlink()
                    summary["deleted"] += 1
            except PermissionError:
                summary["locked"] += 1
            except Exception as exc:
                summary["errors"].append(f"{path.name}: {exc}")

    enforce_log_storage_limits(cleanup_roots, seen_current, retention, summary)

    event_days = int(retention.get("event_db_retention_days") or 180)
    event_db = DATA_DIR / "events.db"
    if event_db.exists():
        cutoff = (datetime.now(timezone.utc) - timedelta(days=event_days)).isoformat()
        try:
            with sqlite3.connect(event_db) as conn:
                cur = conn.execute("DELETE FROM events WHERE received_at < ?", (cutoff,))
                summary["event_rows_deleted"] = cur.rowcount if cur.rowcount is not None else 0
        except Exception as exc:
            summary["errors"].append(f"events.db: {exc}")

    cfg["log_retention"]["last_cleanup_at"] = utc_now()
    save_json(SETTINGS_FILE, cfg)
    audit(f"log retention cleanup rotated={summary['rotated']} deleted={summary['deleted']} event_rows_deleted={summary['event_rows_deleted']}", "system")
    return summary


def backup_status(cfg):
    backup_cfg = cfg.get("backup", {})
    backup_root = Path(backup_cfg.get("backup_path") or (DATA_DIR / "backups"))
    backups = []
    if backup_root.exists():
        for p in sorted(backup_root.glob("OperationCenter_Backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            backups.append({
                "name": p.name,
                "path": str(p),
                "size": p.stat().st_size,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds"),
            })
    return {"settings": backup_cfg, "backups": backups[:20]}


def maybe_auto_backup(cfg):
    backup_cfg = cfg.get("backup", {})
    if not backup_cfg.get("auto_backup", True):
        return
    frequency = backup_cfg.get("frequency", "daily")
    interval_days = {"daily": 1, "7_days": 7, "30_days": 30}.get(frequency, 1)
    last = backup_cfg.get("last_backup_at") or ""
    due = True
    if last:
        try:
            last_ts = datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
            due = time.time() - last_ts >= interval_days * 86400
        except Exception:
            due = True
    if due:
        try:
            result = create_backup(cfg)
            audit(f"automatic backup created {result['path']}", "system")
        except Exception as exc:
            audit(f"automatic backup failed {exc}", "system", "ERROR")


def start_auto_backup_check(cfg):
    def run():
        startup_trace("auto backup: start")
        maybe_auto_backup(cfg)
        startup_trace("auto backup: done")

    threading.Thread(target=run, daemon=True).start()


def module_settings():
    return settings()


def rtsp_url_with_credentials(raw_url, username, password):
    if not raw_url or not password:
        return raw_url
    try:
        parts = urlsplit(raw_url)
        if parts.scheme.lower() != "rtsp" or not parts.netloc:
            return raw_url
        if "@" in parts.netloc:
            userinfo, hostport = parts.netloc.rsplit("@", 1)
        else:
            userinfo, hostport = "", parts.netloc
        user = username or userinfo
        if not user:
            return raw_url
        auth = f"{quote(user, safe='')}:{quote(password, safe='')}"
        return urlunsplit((parts.scheme, f"{auth}@{hostport}", parts.path, parts.query, parts.fragment))
    except Exception:
        return raw_url


def live_stream_event_key(data, device_sn):
    camera_index = str(data.get("camera_index") or data.get("cameraIndex") or "")
    converter_name = str(data.get("converter_name") or data.get("converterName") or "").strip().lower()
    stream_source = str(data.get("stream_source") or data.get("streamSource") or data.get("source") or "").strip().lower()
    if device_sn or camera_index or converter_name or stream_source:
        return "|".join([str(device_sn), camera_index, converter_name, stream_source])
    return str(data.get("converter_id") or data.get("converterId") or "")


def normalized_stream_identity_text(value):
    text = str(value or "").strip().lower()
    return " ".join(text.replace("_", " ").replace("-", " ").split())


def live_channel_identity_name(channel):
    return normalized_stream_identity_text(
        channel.get("name")
        or channel.get("device_name")
        or channel.get("converter_name")
        or channel.get("source")
    )


def event_camera_display_name(payload, data, live_channel, camera_index):
    # Do not hard-code DJI camera_index names here. Use the name/position
    # already provided by the Event API or the existing device metadata cache.
    # If DJI does not provide a friendly name, fall back to camera_index.
    sources = [data or {}, payload or {}, live_channel or {}]
    keys = (
        "camera_name", "cameraName",
        "camera_position", "cameraPosition",
        "camera_label", "cameraLabel",
        "payload_name", "payloadName",
    )
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return str(value).strip()
        camera = source.get("camera")
        if isinstance(camera, dict):
            for key in keys + ("name", "position", "label"):
                value = camera.get(key)
                if value not in (None, ""):
                    return str(value).strip()
    return str(camera_index or "").strip()


def event_identity_name(payload, data, converter_name, camera_index, device_sn, camera_display_name=None):
    callsign = (
        data.get("device_callsign")
        or data.get("deviceCallsign")
        or payload.get("device_callsign")
        or payload.get("deviceCallsign")
        or converter_name
        or f"Dock {device_sn}"
    )
    label = str(camera_display_name or camera_index or "").strip()
    display_name = callsign if not label else f"{callsign} {label}"
    return normalized_stream_identity_text(display_name), display_name


def live_channel_same_named_source(channel, identity_name, camera_index):
    if not identity_name:
        return False
    if str(channel.get("camera_index") or "") != str(camera_index or ""):
        return False
    return live_channel_identity_name(channel) == identity_name


def nvr_source_key(device_sn, camera_index, identity_name=None):
    # Stable identity for DJI stream/NVR mapping.
    # One DJI serial number can have multiple cameras, so the correct unique
    # key is always serial number + camera index. Do not key by display name,
    # because the same dock/drone name can appear more than once or change.
    sn = str(device_sn or '').strip()
    cam = str(camera_index or '').strip()
    return f"{sn}|{cam}"


def same_dji_serial_camera(item, device_sn, camera_index):
    return (
        str(item.get("device_sn") or "").strip() == str(device_sn or "").strip()
        and str(item.get("camera_index") or "").strip() == str(camera_index or "").strip()
    )


def nvr_log(nvr_cfg, level, message, details=None):
    log = nvr_cfg.setdefault("sync_log", [])
    log.insert(0, {
        "time": utc_now(),
        "level": level,
        "message": message,
        "details": details or {},
    })
    del log[100:]


def normalize_nvr_config(nvr_cfg):
    nvr_cfg.setdefault("enabled", False)
    nvr_cfg.setdefault("auto_assign", True)
    nvr_cfg.setdefault("sdk_status", "sdk_not_configured")
    nvr_cfg.setdefault("nvrs", [])
    nvr_cfg.setdefault("mappings", [])
    nvr_cfg.setdefault("sync_log", [])
    for index, nvr in enumerate(nvr_cfg["nvrs"], start=1):
        nvr.setdefault("id", f"nvr_{index}")
        nvr.setdefault("name", f"NVR {index}")
        nvr.setdefault("enabled", True)
        nvr.setdefault("host", "")
        nvr.setdefault("sdk_port", 8000)
        nvr.setdefault("web_port", 80)
        nvr.setdefault("username", "")
        nvr.setdefault("password", "")
        nvr.setdefault("max_channels", 32)
        nvr.setdefault("priority", index)
    return nvr_cfg


def parse_rtsp_for_hikvision(rtsp_url):
    parts = urlsplit(rtsp_url or "")
    if parts.scheme.lower() != "rtsp" or not parts.hostname:
        return None
    path = parts.path or ""
    if parts.query:
        path = f"{path}?{parts.query}"
    return {
        "host": parts.hostname,
        "port": int(parts.port or 554),
        "stream_path": path,
    }


def is_real_nvr_stream_mapping(mapping):
    host = str(mapping.get("host") or "").strip().lower()
    stream_path = str(mapping.get("stream_path") or "").strip()
    last_url = str(mapping.get("last_url") or "").strip().lower()
    if host in {"", "example", "example.com"}:
        return False
    if not stream_path:
        return False
    if not last_url:
        return False
    return True


def safe_nvr_channel_number(value):
    try:
        channel = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return channel if channel > 0 else 0


def nvr_channel_usage(nvr_cfg, exclude_mapping=None):
    usage = {}
    for mapping in nvr_cfg.get("mappings", []):
        if exclude_mapping is not None and mapping is exclude_mapping:
            continue
        if not is_real_nvr_stream_mapping(mapping):
            continue
        nvr_id = mapping.get("nvr_id")
        channel = mapping.get("nvr_channel")
        if nvr_id and channel:
            usage.setdefault(nvr_id, set()).add(int(channel))
    return usage


def find_free_nvr_channel(nvr_cfg, exclude_mapping=None, preferred_channel=None, preferred_nvr_name=None):
    usage = nvr_channel_usage(nvr_cfg, exclude_mapping=exclude_mapping)
    for nvr in sorted((n for n in nvr_cfg.get("nvrs", []) if n.get("enabled", True)), key=lambda x: int(x.get("priority") or 999)):
        if preferred_nvr_name and str(nvr.get("name") or "") != str(preferred_nvr_name):
            continue
        max_channels = max(0, int(nvr.get("max_channels") or 0))
        used = usage.get(nvr.get("id"), set())
        if preferred_channel:
            channel = int(preferred_channel)
            if 1 <= channel <= max_channels and channel not in used:
                return nvr, channel
        for channel in range(1, max_channels + 1):
            if channel not in used:
                return nvr, channel
    if preferred_nvr_name:
        return find_free_nvr_channel(nvr_cfg, exclude_mapping=exclude_mapping, preferred_channel=preferred_channel)
    return None, None


def nvr_display_status(nvr):
    if not nvr.get("host") or not nvr.get("username"):
        return "not_configured"
    if not nvr.get("enabled", True):
        return "disabled"
    if nvr.get("online_status") == "online":
        try:
            last = datetime.fromisoformat(str(nvr.get("last_checked_at", "")).replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - last).total_seconds() > 90:
                return "stale"
        except Exception:
            return "stale"
    return nvr.get("online_status") or "configured"


def merge_nvr_sync_settings(current, incoming):
    merged = merge_defaults(incoming or {}, current or {})
    old_by_id = {str(n.get("id")): n for n in (current or {}).get("nvrs", [])}
    cleaned = []
    replaced_nvr_ids = set()
    for index, nvr in enumerate((incoming or {}).get("nvrs", [])):
        item = dict(nvr)
        nvr_id = str(item.get("id") or f"nvr_{index + 1}")
        old = old_by_id.get(nvr_id, {})
        item["id"] = nvr_id
        if old:
            old_host = str(old.get("host") or "").strip()
            new_host = str(item.get("host") or "").strip()
            old_port = str(old.get("sdk_port") or "").strip()
            new_port = str(item.get("sdk_port") or "").strip()
            if (old_host or new_host) and (old_host != new_host or old_port != new_port):
                replaced_nvr_ids.add(nvr_id)
        else:
            replaced_nvr_ids.add(nvr_id)
        password = str(item.get("password") or "").strip()
        if password == SECRET_MASK and old.get("password"):
            item["password"] = old.get("password")
            password = str(item.get("password") or "").strip()
        if not password:
            if old.get("password"):
                item["password"] = old.get("password")
            elif item.get("host") or item.get("username"):
                raise ValueError(f"NVR password is required for {item.get('name') or item.get('host') or nvr_id}")
        for key in ("online_status", "online_message", "last_checked_at", "last_checked_latency_ms", "device_info"):
            if key not in item and key in old:
                item[key] = old.get(key)
        cleaned.append(item)
    merged["nvrs"] = cleaned
    if replaced_nvr_ids:
        kept_mappings = []
        removed = []
        for mapping in merged.get("mappings", []):
            if str(mapping.get("nvr_id") or "") in replaced_nvr_ids:
                removed.append({
                    "source_key": mapping.get("source_key"),
                    "nvr_id": mapping.get("nvr_id"),
                    "nvr_name": mapping.get("nvr_name"),
                    "nvr_channel": mapping.get("nvr_channel"),
                })
                continue
            kept_mappings.append(mapping)
        if removed:
            merged["mappings"] = kept_mappings
            nvr_log(merged, "info", "Cleared old NVR mappings for new/replaced NVR server", {"removed": removed})
    return normalize_nvr_config(merged)


def check_nvr_connection(nvr):
    host = str(nvr.get("host") or "").strip()
    port = int(nvr.get("sdk_port") or 8000)
    started = time.monotonic()
    result = HIKVISION_SDK.login_check(
        host,
        port,
        str(nvr.get("username") or "").strip(),
        str(nvr.get("password") or ""),
    )
    result["latency_ms"] = int((time.monotonic() - started) * 1000)
    result["sdk"] = HIKVISION_SDK.status()
    return result


def check_nvr_by_id(cfg, nvr_id):
    nvr_cfg = normalize_nvr_config(cfg["modules"].setdefault("nvr_sync", {}))
    target = next((n for n in nvr_cfg.get("nvrs", []) if str(n.get("id")) == str(nvr_id)), None)
    if not target:
        raise ValueError("NVR not found")
    result = check_nvr_connection(target)
    if result.get("status") == "online":
        monitor = HIKVISION_SDK.ensure_monitor(
            target.get("id"),
            str(target.get("host") or "").strip(),
            int(target.get("sdk_port") or 8000),
            str(target.get("username") or "").strip(),
            str(target.get("password") or ""),
        )
        result["monitor"] = monitor
        if monitor.get("status") == "offline":
            result["status"] = "offline"
            result["message"] = monitor.get("message") or "SDK monitor reported offline"
    else:
        HIKVISION_SDK.close_monitor(target.get("id"))
    target["online_status"] = result["status"]
    target["online_message"] = result["message"]
    target["last_checked_at"] = utc_now()
    target["last_checked_latency_ms"] = result.get("latency_ms")
    target["last_sdk_error_code"] = result.get("sdk_error_code")
    target["device_info"] = result.get("device_info") or target.get("device_info") or {}
    nvr_log(nvr_cfg, "info" if result["status"] == "online" else "warn", f"NVR check {result['status']}: {target.get('name') or target.get('host')}", result)
    return result


def check_all_nvrs(cfg):
    nvr_cfg = normalize_nvr_config(cfg["modules"].setdefault("nvr_sync", {}))
    results = []
    changed = False
    for nvr in nvr_cfg.get("nvrs", []):
        if not nvr.get("enabled", True):
            HIKVISION_SDK.close_monitor(nvr.get("id"))
            nvr["online_status"] = "disabled"
            nvr["online_message"] = "NVR disabled"
            changed = True
            continue
        if not nvr.get("host") or not nvr.get("username"):
            HIKVISION_SDK.close_monitor(nvr.get("id"))
            nvr["online_status"] = "not_configured"
            nvr["online_message"] = "NVR IP/username not configured"
            changed = True
            continue
        result = check_nvr_connection(nvr)
        if result.get("status") == "online":
            monitor = HIKVISION_SDK.ensure_monitor(
                nvr.get("id"),
                str(nvr.get("host") or "").strip(),
                int(nvr.get("sdk_port") or 8000),
                str(nvr.get("username") or "").strip(),
                str(nvr.get("password") or ""),
            )
            if monitor.get("status") in {"online", "offline"}:
                result["monitor"] = monitor
                if monitor.get("status") == "offline":
                    result["status"] = "offline"
                    result["message"] = monitor.get("message") or "SDK monitor reported offline"
        else:
            HIKVISION_SDK.close_monitor(nvr.get("id"))
        nvr["online_status"] = result["status"]
        nvr["online_message"] = result["message"]
        nvr["last_checked_at"] = utc_now()
        nvr["last_checked_latency_ms"] = result.get("latency_ms")
        nvr["last_sdk_error_code"] = result.get("sdk_error_code")
        nvr["device_info"] = result.get("device_info") or nvr.get("device_info") or {}
        results.append({"id": nvr.get("id"), "name": nvr.get("name"), **result})
        changed = True
    if changed:
        sdk = HIKVISION_SDK.status()
        if sdk.get("loaded"):
            nvr_cfg["sdk_status"] = "sdk_ready"
        elif sdk.get("available"):
            nvr_cfg["sdk_status"] = "sdk_available"
        else:
            nvr_cfg["sdk_status"] = "sdk_missing"
        save_json(SETTINGS_FILE, cfg)
    return results


def clear_aero_sync_nvr_channels(cfg):
    nvr_cfg = normalize_nvr_config(cfg["modules"].setdefault("nvr_sync", {}))
    mappings = [m for m in nvr_cfg.get("mappings", []) if is_real_nvr_stream_mapping(m)]
    if not mappings:
        return {"ok": True, "results": [], "removed_mappings": 0, "message": "No AERO SYNC NVR mappings to clear"}

    nvr_by_id = {str(nvr.get("id")): nvr for nvr in nvr_cfg.get("nvrs", [])}
    results = []
    cleared_keys = set()
    for nvr_id in sorted({str(m.get("nvr_id") or "") for m in mappings if m.get("nvr_id")}):
        nvr = nvr_by_id.get(nvr_id)
        nvr_mappings = [m for m in mappings if str(m.get("nvr_id") or "") == nvr_id]
        channels = [safe_nvr_channel_number(m.get("nvr_channel")) for m in nvr_mappings if safe_nvr_channel_number(m.get("nvr_channel")) > 0]
        if not nvr:
            results.append({"nvr_id": nvr_id, "status": "nvr_missing", "message": "Mapped NVR not found", "cleared": []})
            continue
        result = HIKVISION_SDK.clear_ip_channels(
            str(nvr.get("host") or "").strip(),
            int(nvr.get("sdk_port") or 8000),
            str(nvr.get("username") or "").strip(),
            str(nvr.get("password") or ""),
            channels,
        )
        result["nvr_id"] = nvr_id
        result["nvr_name"] = nvr.get("name") or nvr.get("host") or nvr_id
        results.append(result)
        if result.get("status") in {"cleared", "nothing_to_clear"}:
            cleared_numbers = {int(ch) for ch in result.get("cleared", [])}
            if result.get("status") == "nothing_to_clear":
                cleared_numbers = set(channels)
            for mapping in nvr_mappings:
                if safe_nvr_channel_number(mapping.get("nvr_channel")) in cleared_numbers:
                    cleared_keys.add(str(mapping.get("source_key") or ""))
            nvr_log(nvr_cfg, "warn", f"Cleared AERO SYNC channels on {result['nvr_name']}", {
                "nvr": result["nvr_name"],
                "channels": sorted(cleared_numbers),
                "status": result.get("status"),
            })
        else:
            nvr_log(nvr_cfg, "error", f"Failed to clear AERO SYNC channels on {result['nvr_name']}", {
                "nvr": result["nvr_name"],
                "channels": channels,
                "status": result.get("status"),
                "message": result.get("message"),
                "sdk_error_code": result.get("sdk_error_code"),
            })

    before = len(nvr_cfg.get("mappings", []))
    if cleared_keys:
        nvr_cfg["mappings"] = [m for m in nvr_cfg.get("mappings", []) if str(m.get("source_key") or "") not in cleared_keys]
    removed = before - len(nvr_cfg.get("mappings", []))
    cfg["modules"]["nvr_sync"] = nvr_cfg
    save_json(SETTINGS_FILE, cfg)
    return {
        "ok": not any(r.get("status") not in {"cleared", "nothing_to_clear"} for r in results),
        "results": results,
        "removed_mappings": removed,
        "message": f"Cleared {removed} AERO SYNC mapping(s)",
    }


def live_stream_source_key(channel):
    device_sn = str(channel.get("device_sn") or "").strip()
    camera_index = str(channel.get("camera_index") or "").strip()
    if device_sn and camera_index:
        return f"{device_sn}|{camera_index}"
    stream_key = str(channel.get("stream_key") or "").strip()
    if stream_key:
        return f"stream:{stream_key}"
    return ""


def remember_cleared_live_source(live_cfg, channel):
    source_key = live_stream_source_key(channel)
    if not source_key:
        return
    rows = [row for row in (live_cfg.get("cleared_sources") or []) if str(row.get("source_key") or "") != source_key]
    rows.insert(0, {
        "source_key": source_key,
        "device_sn": str(channel.get("device_sn") or ""),
        "camera_index": str(channel.get("camera_index") or ""),
        "stream_key": str(channel.get("stream_key") or ""),
        "event_timestamp": channel.get("event_timestamp"),
        "cleared_at": utc_now(),
    })
    live_cfg["cleared_sources"] = rows[:200]


def cleared_live_source(live_cfg, device_sn, camera_index, stream_key=""):
    stable = f"{str(device_sn or '').strip()}|{str(camera_index or '').strip()}" if device_sn and camera_index else ""
    fallback = f"stream:{str(stream_key or '').strip()}" if stream_key else ""
    for row in live_cfg.get("cleared_sources") or []:
        key = str(row.get("source_key") or "")
        if (stable and key == stable) or (fallback and key == fallback):
            return row
    return None


def release_live_source_tombstone(live_cfg, device_sn, camera_index, stream_key=""):
    stable = f"{str(device_sn or '').strip()}|{str(camera_index or '').strip()}" if device_sn and camera_index else ""
    fallback = f"stream:{str(stream_key or '').strip()}" if stream_key else ""
    live_cfg["cleared_sources"] = [
        row for row in (live_cfg.get("cleared_sources") or [])
        if str(row.get("source_key") or "") not in {stable, fallback}
    ]


def clear_live_stream_channel(cfg, channel_no):
    live_cfg = cfg["modules"].setdefault("live_streams", {})
    channels = live_cfg.get("channels", [])
    channel = next((item for item in channels if int(item.get("channel") or 0) == int(channel_no)), None)
    if not channel:
        raise ValueError("Live stream channel not found")

    original = dict(channel)
    remember_cleared_live_source(live_cfg, original)

    # Best effort: stop any local preview/recording process for this slot.
    stream_module = MODULES.get("stream")
    if stream_module:
        try:
            stream_module.stop_live(int(channel_no))
        except Exception as exc:
            audit(f"live stream clear: preview stop failed channel={channel_no}: {exc}", "system", "WARN")

    nvr_cfg = normalize_nvr_config(cfg["modules"].setdefault("nvr_sync", {}))
    matched = []
    for mapping in list(nvr_cfg.get("mappings", [])):
        same_source = False
        if original.get("device_sn") and original.get("camera_index"):
            same_source = same_dji_serial_camera(mapping, original.get("device_sn"), original.get("camera_index"))
        if not same_source and original.get("stream_key"):
            same_source = str(mapping.get("live_stream_key") or "") == str(original.get("stream_key") or "")
        if not same_source and int(channel_no) == 16:
            same_source = str(mapping.get("source_key") or "") == "manual|channel16"
        if same_source:
            matched.append(mapping)

    remote_results = []
    nvr_by_id = {str(nvr.get("id") or ""): nvr for nvr in nvr_cfg.get("nvrs", [])}
    for mapping in matched:
        nvr = nvr_by_id.get(str(mapping.get("nvr_id") or ""))
        nvr_channel = safe_nvr_channel_number(mapping.get("nvr_channel"))
        if nvr and nvr_channel > 0:
            try:
                result = HIKVISION_SDK.clear_ip_channels(
                    str(nvr.get("host") or "").strip(),
                    int(nvr.get("sdk_port") or 8000),
                    str(nvr.get("username") or "").strip(),
                    str(nvr.get("password") or ""),
                    [nvr_channel],
                )
            except Exception as exc:
                result = {"status": "error", "message": str(exc), "cleared": []}
            remote_results.append({
                "nvr_id": nvr.get("id"), "nvr_name": nvr.get("name") or nvr.get("host"),
                "channel": nvr_channel, **result,
            })
            nvr_log(
                nvr_cfg,
                "info" if result.get("status") in {"cleared", "nothing_to_clear"} else "warn",
                f"Live Stream Clear requested NVR channel {nvr_channel}",
                {"source": live_stream_source_key(original), "result": result},
            )

    # Always release AeroSync's own NVR mapping. Remote NVR cleanup is best-effort.
    matched_ids = {id(m) for m in matched}
    if matched_ids:
        nvr_cfg["mappings"] = [m for m in nvr_cfg.get("mappings", []) if id(m) not in matched_ids]

    channel.clear()
    channel.update({
        "channel": int(channel_no),
        "name": f"Channel {int(channel_no):02d}",
        "rtsp_url": "",
        "rtsp_source_url": "",
        "rtsp_username": "",
        "rtsp_password": "",
        "device_sn": "",
        "camera_index": "",
        "camera_name": "",
        "converter_id": "",
        "converter_name": "",
        "stream_key": "",
        "identity_name": "",
        "enabled": False,
        "status": "idle",
        "updated_at": "",
        "event_timestamp": None,
        "source": "",
    })
    cfg["modules"]["nvr_sync"] = nvr_cfg
    save_json(SETTINGS_FILE, cfg)
    return {
        "ok": True,
        "channel": int(channel_no),
        "released_nvr_mappings": len(matched),
        "nvr_results": remote_results,
        "message": f"Channel {int(channel_no):02d} cleared",
    }


def nvr_mapping_camera_password(cfg, mapping):
    password = str(mapping.get("password") or "")
    if password and password != SECRET_MASK:
        return password
    source_key = str(mapping.get("source_key") or "")
    device_sn = str(mapping.get("device_sn") or "")
    camera_index = str(mapping.get("camera_index") or "")
    for channel in cfg.get("modules", {}).get("live_streams", {}).get("channels", []):
        if source_key and str(channel.get("stream_key") or "").startswith(source_key):
            if channel.get("rtsp_password"):
                return str(channel.get("rtsp_password") or "")
        if device_sn and camera_index:
            if str(channel.get("device_sn") or "") == device_sn and str(channel.get("camera_index") or "") == camera_index:
                if channel.get("rtsp_password"):
                    return str(channel.get("rtsp_password") or "")
    parsed = urlsplit(str(mapping.get("last_url") or ""))
    return unquote(parsed.password or "")


def sync_nvr_mapping_to_sdk(nvr_cfg, nvr, mapping):
    if not is_real_nvr_stream_mapping(mapping):
        result = {
            "status": "skipped_no_stream",
            "message": "Skipped stale or test mapping without a valid DJI RTSP stream",
            "sdk_error_code": None,
        }
    elif not nvr:
        result = {"status": "nvr_missing", "message": "Mapped NVR not found", "sdk_error_code": None}
    else:
        result = HIKVISION_SDK.sync_dji_rtsp_channel(
            nvr.get("host") or "",
            int(nvr.get("sdk_port") or 8000),
            nvr.get("username") or "",
            nvr.get("password") or "",
            int(mapping.get("nvr_channel") or 1),
            int(mapping.get("rtsp_port") or 554),
            mapping.get("stream_path") or "",
            mapping.get("last_url") or "",
            name=(mapping.get("device_name") or mapping.get("camera_index") or f"Channel {int(mapping.get('nvr_channel') or 1):02d}"),
            camera_username=mapping.get("username") or "",
            camera_password=nvr_mapping_camera_password(settings(), mapping),
        )
    mapping["status"] = result.get("status") or "sync_failed"
    mapping["message"] = result.get("message") or ""
    mapping["last_sdk_error_code"] = result.get("sdk_error_code")
    mapping["last_sdk_sync_at"] = utc_now()
    nvr_log(
        nvr_cfg,
        "info" if result.get("status") in {"channel_synced", "custom_protocol_synced"} else "warn",
        f"NVR channel sync {result.get('status')}: {mapping.get('device_name') or mapping.get('source_key')}",
        result,
    )
    return result


def sync_manual_rtsp_channel_16(cfg):
    """Sync a manually entered RTSP URL from Live Stream Channel 16 to NVR channel 16.

    This is intentionally narrow: it does not change EventAPI/DJI mappings or UI
    behavior. If Channel 16 has an RTSP URL and NVR sync is enabled, the URL is
    pushed to the first enabled NVR as NVR channel 16.
    """
    live_channels = cfg.get("modules", {}).get("live_streams", {}).get("channels", [])
    channel_16 = next((ch for ch in live_channels if int(ch.get("channel") or 0) == 16), None)
    if not channel_16:
        return None
    rtsp_url = str(channel_16.get("rtsp_url") or channel_16.get("rtsp_source_url") or "").strip()
    if not rtsp_url:
        return None

    parsed = parse_rtsp_for_hikvision(rtsp_url)
    nvr_cfg = normalize_nvr_config(cfg["modules"].setdefault("nvr_sync", {}))
    if not nvr_cfg.get("enabled"):
        nvr_log(nvr_cfg, "warn", "Manual RTSP channel 16 not synced because NVR sync is disabled", {"channel": 16})
        return None
    if not parsed:
        nvr_log(nvr_cfg, "error", "Manual RTSP channel 16 has invalid RTSP URL", {"channel": 16, "url": rtsp_url})
        return None

    nvr = next((item for item in sorted(nvr_cfg.get("nvrs", []), key=lambda x: int(x.get("priority") or 999)) if item.get("enabled", True)), None)
    if not nvr:
        nvr_log(nvr_cfg, "error", "Manual RTSP channel 16 sync failed: no enabled NVR configured", {"channel": 16})
        return None

    parts = urlsplit(rtsp_url)
    mappings = nvr_cfg.setdefault("mappings", [])
    source_key = "manual|channel16"
    mapping = next((m for m in mappings if m.get("source_key") == source_key), None)
    if mapping is None:
        mapping = {"source_key": source_key}
        mappings.append(mapping)

    mapping.update({
        "source_key": source_key,
        "device_sn": "manual",
        "camera_index": "manual-16",
        "camera_name": "Manual",
        "device_name": "",
        "identity_name": "manual channel 16",
        "live_stream_key": "manual-channel-16",
        "nvr_id": nvr.get("id"),
        "nvr_name": nvr.get("name"),
        "nvr_channel": 16,
        "host": parsed["host"],
        "rtsp_port": parsed["port"],
        "stream_path": parsed["stream_path"],
        "username": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "last_url": rtsp_url,
        "last_sync_at": utc_now(),
        "status": "prepared",
        "message": "Prepared for manual Hikvision custom protocol sync",
        "protocol": "RTSP",
        "transfer_protocol": "RTP Over RTSP",
        "manual": True,
    })
    result = sync_nvr_mapping_to_sdk(nvr_cfg, nvr, mapping)
    nvr_log(nvr_cfg, "info" if result.get("status") == "channel_synced" else "warn", "Manual RTSP URL synced to NVR channel 16", {
        "nvr": nvr.get("name") or nvr.get("host"),
        "nvr_channel": 16,
        "rtsp_host": parsed["host"],
        "status": result.get("status"),
        "message": result.get("message"),
        "sdk_error_code": result.get("sdk_error_code"),
    })
    cfg["modules"]["nvr_sync"] = nvr_cfg
    return result


def nvr_sync_status(cfg):
    nvr_cfg = normalize_nvr_config(cfg["modules"].setdefault("nvr_sync", {}))
    usage = nvr_channel_usage(nvr_cfg)
    stream_info = {}
    stream_module = MODULES.get("stream")
    if stream_module:
        try:
            stream_info = stream_module.status().get("stream_info", {})
        except Exception:
            stream_info = {}
    live_channels = cfg.get("modules", {}).get("live_streams", {}).get("channels", [])
    servers = []
    total_channels = 0
    used_channels = 0
    for nvr in sorted(nvr_cfg.get("nvrs", []), key=lambda x: int(x.get("priority") or 999)):
        max_channels = max(0, int(nvr.get("max_channels") or 0))
        used = len(usage.get(nvr.get("id"), set()))
        total_channels += max_channels
        used_channels += used
        safe_nvr = dict(nvr)
        safe_nvr["password_set"] = bool(safe_nvr.get("password"))
        safe_nvr.pop("password", None)
        safe_nvr["used_channels"] = used
        safe_nvr["free_channels"] = max(0, max_channels - used)
        safe_nvr["status"] = nvr_display_status(safe_nvr)
        servers.append(safe_nvr)
    safe_mappings = []
    for mapping in nvr_cfg.get("mappings", []):
        if not is_real_nvr_stream_mapping(mapping):
            continue
        safe_mapping = dict(mapping)
        safe_mapping["password_set"] = bool(safe_mapping.get("password"))
        safe_mapping.pop("password", None)
        live_channel = next((
            ch for ch in live_channels
            if str(ch.get("stream_key") or "") == str(mapping.get("live_stream_key") or "")
        ), None)
        if live_channel is None:
            live_channel = next((
                ch for ch in live_channels
                if live_channel_same_named_source(ch, str(mapping.get("identity_name") or ""), str(mapping.get("camera_index") or ""))
            ), None)
        if live_channel is None:
            live_channel = next((
                ch for ch in live_channels
                if str(ch.get("device_sn") or "") == str(mapping.get("device_sn") or "")
                and str(ch.get("camera_index") or "") == str(mapping.get("camera_index") or "")
            ), None)
        if live_channel:
            safe_mapping["live_channel"] = live_channel.get("channel")
            safe_mapping["stream_info"] = stream_info.get(str(live_channel.get("channel"))) or {}
        safe_mappings.append(safe_mapping)
    return {
        "enabled": bool(nvr_cfg.get("enabled")),
        "auto_assign": bool(nvr_cfg.get("auto_assign", True)),
        "sdk_status": nvr_cfg.get("sdk_status") or ("sdk_ready" if HIKVISION_SDK.status().get("loaded") else "sdk_available" if HIKVISION_SDK.status().get("available") else "sdk_missing"),
        "sdk": HIKVISION_SDK.status(),
        "servers": servers,
        "mappings": safe_mappings,
        "sync_log": nvr_cfg.get("sync_log", [])[:50],
        "total_channels": total_channels,
        "used_channels": used_channels,
        "free_channels": max(0, total_channels - used_channels),
    }


def sync_nvr_from_rtsp_event(cfg, payload, live_channel):
    nvr_cfg = normalize_nvr_config(cfg["modules"].setdefault("nvr_sync", {}))
    if not nvr_cfg.get("enabled"):
        return
    data = payload.get("data") or {}
    rtsp_url = data.get("url") or data.get("rtsp_url") or payload.get("url") or ""
    parsed = parse_rtsp_for_hikvision(rtsp_url)
    device_sn = live_channel.get("device_sn") or data.get("sn") or ""
    camera_index = live_channel.get("camera_index") or data.get("camera_index") or ""
    converter_name = str(data.get("converter_name") or data.get("converterName") or payload.get("converter_name") or payload.get("converterName") or live_channel.get("converter_name") or "")
    camera_name = converter_name
    identity_name = live_channel_identity_name(live_channel)
    source_key = nvr_source_key(device_sn, camera_index, identity_name)
    if not parsed or not device_sn or not camera_index:
        nvr_log(nvr_cfg, "error", "NVR sync skipped: invalid DJI RTSP event", {"device_sn": device_sn, "camera_index": camera_index, "url": rtsp_url})
        return

    mappings = nvr_cfg.setdefault("mappings", [])
    mapping = next((m for m in mappings if m.get("source_key") == source_key), None)
    if mapping is None:
        mapping = next((m for m in mappings if same_dji_serial_camera(m, device_sn, camera_index)), None)
        if mapping is not None:
            old_key = mapping.get("source_key")
            mapping["source_key"] = source_key
            nvr_log(nvr_cfg, "info", "Reused existing NVR mapping by DJI serial + camera index", {"old_source_key": old_key, "new_source_key": source_key})
    if mapping is None:
        if not nvr_cfg.get("auto_assign", True):
            nvr_log(nvr_cfg, "warn", "NVR sync waiting for manual channel mapping", {"device_sn": device_sn, "camera_index": camera_index})
            return
        nvr, channel = find_free_nvr_channel(nvr_cfg)
        if not nvr:
            nvr_log(nvr_cfg, "error", "Channel limit exceeded: no free NVR channel available", {"device_sn": device_sn, "camera_index": camera_index})
            return
        mapping = {
            "source_key": source_key,
            "device_sn": device_sn,
            "camera_index": camera_index,
            "camera_name": camera_name,
            "device_name": live_channel.get("name") or data.get("device_callsign") or "",
            "nvr_id": nvr.get("id"),
            "nvr_name": nvr.get("name"),
            "nvr_channel": channel,
        }
        mappings.append(mapping)
        nvr_log(nvr_cfg, "info", "Assigned DJI stream to free NVR channel", {"source_key": source_key, "nvr": nvr.get("name"), "channel": channel})

    mapping.update({
        "device_sn": device_sn,
        "camera_index": camera_index,
        "camera_name": camera_name,
        "device_name": live_channel.get("name") or mapping.get("device_name") or "",
        "identity_name": identity_name,
        "live_stream_key": live_channel.get("stream_key") or "",
        "host": parsed["host"],
        "rtsp_port": parsed["port"],
        "stream_path": parsed["stream_path"],
        "username": data.get("username") or live_channel.get("rtsp_username") or "",
        "password": data.get("password") or live_channel.get("rtsp_password") or "",
        "last_url": rtsp_url,
        "last_sync_at": utc_now(),
        "status": "prepared",
        "message": "Prepared for Hikvision custom protocol sync",
        "protocol": "RTSP",
        "transfer_protocol": "RTP Over RTSP",
    })
    duplicate_mappings = []
    for other in list(mappings):
        if other is mapping:
            continue
        if same_dji_serial_camera(other, device_sn, camera_index):
            duplicate_mappings.append({
                "source_key": other.get("source_key"),
                "nvr_id": other.get("nvr_id"),
                "nvr_channel": other.get("nvr_channel"),
            })
            mappings.remove(other)
    if duplicate_mappings:
        nvr_by_id = {str(item.get("id") or ""): item for item in nvr_cfg.get("nvrs", [])}
        kept_nvr_id = str(mapping.get("nvr_id") or "")
        kept_channel = safe_nvr_channel_number(mapping.get("nvr_channel"))
        channels_by_nvr = {}
        for duplicate in duplicate_mappings:
            duplicate_nvr_id = str(duplicate.get("nvr_id") or "")
            duplicate_channel = safe_nvr_channel_number(duplicate.get("nvr_channel"))
            if not duplicate_nvr_id or duplicate_channel <= 0:
                continue
            if duplicate_nvr_id == kept_nvr_id and duplicate_channel == kept_channel:
                continue
            channels_by_nvr.setdefault(duplicate_nvr_id, set()).add(duplicate_channel)
        for duplicate_nvr_id, duplicate_channels in channels_by_nvr.items():
            duplicate_nvr = nvr_by_id.get(duplicate_nvr_id)
            if not duplicate_nvr:
                continue
            result = HIKVISION_SDK.clear_ip_channels(
                str(duplicate_nvr.get("host") or "").strip(),
                int(duplicate_nvr.get("sdk_port") or 8000),
                str(duplicate_nvr.get("username") or "").strip(),
                str(duplicate_nvr.get("password") or ""),
                sorted(duplicate_channels),
            )
            nvr_log(nvr_cfg, "info" if result.get("status") in {"cleared", "nothing_to_clear"} else "error", "Cleared duplicate NVR channel(s) for same DJI serial + camera index", {
                "nvr": duplicate_nvr.get("name") or duplicate_nvr.get("host") or duplicate_nvr_id,
                "channels": sorted(duplicate_channels),
                "status": result.get("status"),
                "message": result.get("message"),
                "sdk_error_code": result.get("sdk_error_code"),
            })
        nvr_log(nvr_cfg, "info", "Merged duplicate NVR mappings for same DJI serial + camera index", {"kept_source_key": source_key, "removed": duplicate_mappings})

    nvr = next((item for item in nvr_cfg.get("nvrs", []) if item.get("id") == mapping.get("nvr_id")), None)
    if not nvr and nvr_cfg.get("auto_assign", True):
        replacement, replacement_channel = find_free_nvr_channel(
            nvr_cfg,
            exclude_mapping=mapping,
            preferred_channel=mapping.get("nvr_channel"),
            preferred_nvr_name=mapping.get("nvr_name"),
        )
        if replacement:
            mapping["nvr_id"] = replacement.get("id")
            mapping["nvr_name"] = replacement.get("name")
            mapping["nvr_channel"] = replacement_channel
            nvr = replacement
            nvr_log(nvr_cfg, "info", "Reassigned DJI stream mapping to available NVR", {
                "source_key": source_key,
                "nvr": replacement.get("name"),
                "channel": replacement_channel,
            })
    sync_nvr_mapping_to_sdk(nvr_cfg, nvr, mapping)
    nvr_log(nvr_cfg, "info", "Prepared NVR channel update from DJI live_rtsp_start", {
        "source_key": source_key,
        "nvr": mapping.get("nvr_name"),
        "channel": mapping.get("nvr_channel"),
        "stream_path": parsed["stream_path"],
    })


def event_timestamp_value(payload):
    data = payload.get("data") or {}
    candidates = [
        payload.get("created_at"),
        payload.get("createdAt"),
        payload.get("timestamp"),
        payload.get("time"),
        payload.get("event_time"),
        payload.get("eventTime"),
        data.get("created_at"),
        data.get("createdAt"),
        data.get("timestamp"),
        data.get("time"),
        data.get("event_time"),
        data.get("eventTime"),
    ]
    for value in candidates:
        if value in (None, ""):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        try:
            return float(text)
        except Exception:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
    return None


def handle_event_auto_sync(event_type, payload, sync_nvr=True, from_backfill=False):
    sync_nvr = bool(sync_nvr and advanced_license_enabled())
    if event_type != "live_rtsp_start":
        return
    data = payload.get("data") or {}
    rtsp_url = (
        data.get("url")
        or data.get("rtsp_url")
        or data.get("rtspUrl")
        or data.get("server_address")
        or data.get("serverAddress")
        or payload.get("url")
        or payload.get("rtsp_url")
        or payload.get("rtspUrl")
        or ""
    )
    device_sn = (
        data.get("sn")
        or data.get("device_sn")
        or data.get("deviceSn")
        or data.get("deviceSerialNumber")
        or payload.get("device_sn")
        or payload.get("deviceSn")
        or payload.get("sn")
        or payload.get("deviceSerialNumber")
        or ""
    )
    if not rtsp_url or not device_sn:
        return

    cfg = settings()
    live_cfg = cfg["modules"]["live_streams"]
    channels = live_cfg.get("channels", [])
    camera_index = str(data.get("camera_index") or data.get("cameraIndex") or payload.get("camera_index") or payload.get("cameraIndex") or "")
    converter_name = str(data.get("converter_name") or data.get("converterName") or payload.get("converter_name") or payload.get("converterName") or "")
    converter_id = str(data.get("converter_id") or data.get("converterId") or payload.get("converter_id") or payload.get("converterId") or "")
    event_data = dict(payload)
    event_data.update(data)
    stream_key = live_stream_event_key(event_data, device_sn)
    camera_name = converter_name
    identity_name, display_name = event_identity_name(payload, data, converter_name, camera_index, device_sn, camera_name)
    incoming_event_ts = event_timestamp_value(payload)

    cleared = cleared_live_source(live_cfg, device_sn, camera_index, stream_key)
    if cleared:
        if from_backfill:
            audit(f"skipped backfill for manually cleared RTSP source {cleared.get('source_key')}", "system", "INFO")
            return
        cleared_ts = cleared.get("event_timestamp")
        try:
            cleared_ts = float(cleared_ts) if cleared_ts not in (None, "") else None
        except Exception:
            cleared_ts = None
        if cleared_ts is not None and incoming_event_ts is not None and float(incoming_event_ts) <= cleared_ts:
            audit(f"ignored stale RTSP event for manually cleared source {cleared.get('source_key')}", "system", "INFO")
            return
        # A genuinely newer live event is allowed to assign the source again.
        release_live_source_tombstone(live_cfg, device_sn, camera_index, stream_key)

    match = None
    # DJI can change the RTSP path/stream key on every live start. The stable
    # camera identity is serial number + camera index, so match this first.
    if device_sn and camera_index:
        for channel in channels:
            if same_dji_serial_camera(channel, device_sn, camera_index):
                match = channel
                break
    if match is None:
        for channel in channels:
            if channel.get("stream_key") and channel.get("stream_key") == stream_key:
                match = channel
                break
    if match is None and identity_name and camera_index:
        for channel in channels:
            if live_channel_same_named_source(channel, identity_name, camera_index):
                match = channel
                break
    if match is None and converter_id:
        for channel in channels:
            if channel.get("converter_id") == converter_id:
                match = channel
                break
    if match is None:
        for channel in channels:
            same_device = channel.get("device_sn") == device_sn
            same_camera = str(channel.get("camera_index") or "") == camera_index
            same_converter = str(channel.get("converter_name") or "").strip().lower() == converter_name.strip().lower()
            if same_device and same_camera and same_converter:
                match = channel
                break
    if match is None:
        for channel in channels:
            if channel.get("rtsp_source_url") == rtsp_url or channel.get("rtsp_url") == rtsp_url:
                match = channel
                break
    if match is None:
        for channel in channels:
            if not str(channel.get("rtsp_url") or "").strip() and not str(channel.get("stream_key") or "").strip():
                match = channel
                break
    if match is None:
        for channel in channels:
            if not str(channel.get("rtsp_url") or "").strip():
                match = channel
                break
    if match is None:
        audit(f"RTSP event received but no free live stream channel is available sn={device_sn} key={stream_key}", "system", "WARN")
        return

    previous_event_ts = match.get("event_timestamp")
    try:
        previous_event_ts = float(previous_event_ts) if previous_event_ts not in (None, "") else None
    except Exception:
        previous_event_ts = None
    if previous_event_ts is not None and incoming_event_ts is not None and incoming_event_ts < previous_event_ts:
        audit(
            f"ignored older RTSP event for channel {match.get('channel')} sn={device_sn} key={stream_key}",
            "system",
            "INFO",
        )
        return

    callsign = data.get("device_callsign") or data.get("deviceCallsign") or payload.get("device_callsign") or payload.get("deviceCallsign") or converter_name or f"Dock {device_sn}"
    username = data.get("username") or data.get("account") or payload.get("username") or payload.get("account") or ""
    password = data.get("password") or payload.get("password") or ""
    playable_url = rtsp_url_with_credentials(rtsp_url, username, password)
    previous_playable_url = match.get("rtsp_url") or ""
    channel_number = match.get("channel")
    match.update({
        "name": display_name,
        "identity_name": identity_name,
        "rtsp_url": playable_url,
        "rtsp_source_url": rtsp_url,
        "rtsp_username": username,
        "rtsp_password": password,
        "device_sn": device_sn,
        "camera_index": camera_index,
        "camera_name": camera_name,
        "converter_id": converter_id,
        "converter_name": converter_name,
        "stream_key": stream_key,
        "enabled": True,
        "status": "online",
        "updated_at": utc_now(),
        "event_timestamp": incoming_event_ts,
        "source": "DJI EventAPI live_rtsp_start",
    })
    # Remove duplicate entries for the same DJI serial + camera index.
    # A drone can have multiple camera indexes, so only the same SN+camera_index
    # is treated as the same source.
    duplicate_channels = []
    for other in channels:
        if other is match:
            continue
        if same_dji_serial_camera(other, device_sn, camera_index):
            duplicate_channels.append(other.get("channel"))
            other.update({
                "name": f"Channel {int(other.get('channel') or 0):02d}" if other.get("channel") else "Channel",
                "rtsp_url": "",
                "rtsp_source_url": "",
                "rtsp_username": "",
                "rtsp_password": "",
                "device_sn": "",
                "camera_index": "",
                "camera_name": "",
                "converter_id": "",
                "converter_name": "",
                "stream_key": "",
                "identity_name": "",
                "enabled": False,
                "status": "idle",
                "source": "",
            })
    if duplicate_channels:
        audit(f"merged duplicate RTSP source by DJI serial+camera into channel {match.get('channel')} and cleared channels {duplicate_channels}", "system", "INFO")

    if sync_nvr:
        sync_nvr_from_rtsp_event(cfg, payload, match)
    save_json(SETTINGS_FILE, cfg)
    audit(f"auto-updated live stream channel {match.get('channel')} from RTSP event sn={device_sn} key={stream_key}", "system")
    stream_module = MODULES.get("stream")
    if stream_module and channel_number and previous_playable_url != playable_url:
        try:
            stream_module.stop_live(int(channel_number))
            stream_module.start_live(int(channel_number))
            audit(f"restarted live preview channel {channel_number} after RTSP event update", "system")
        except Exception as exc:
            audit(f"live preview restart failed channel {channel_number}: {exc}", "system", "WARN")


def backfill_live_streams_from_events(limit=50):
    cfg = settings()
    configured_path = safe_path(cfg["modules"]["event_receiver"].get("event_db_path"))
    event_db = configured_path or (DATA_DIR / "events.db")
    if not event_db.exists():
        return
    applied = 0
    with sqlite3.connect(event_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT event_type, raw_json FROM events WHERE event_type = ? ORDER BY id DESC LIMIT ?",
            ("live_rtsp_start", int(limit)),
        ).fetchall()
    latest_by_key = {}
    ordered = []
    for row in rows:
        try:
            payload = json.loads(row["raw_json"])
            data = payload.get("data") or {}
            device_sn = data.get("sn") or data.get("device_sn") or payload.get("device_sn") or payload.get("sn") or ""
            camera_index = str(data.get("camera_index") or data.get("cameraIndex") or payload.get("camera_index") or payload.get("cameraIndex") or "")
            key = f"{device_sn}|{camera_index}" if device_sn and camera_index else live_stream_event_key(data, device_sn)
            if key and key not in latest_by_key:
                latest_by_key[key] = row
                ordered.append(row)
        except Exception:
            continue
    for row in reversed(ordered):
        try:
            payload = json.loads(row["raw_json"])
            before = settings()["modules"]["live_streams"].get("channels", [])
            before_key = json.dumps(before, sort_keys=True)
            handle_event_auto_sync(row["event_type"], payload, sync_nvr=False, from_backfill=True)
            after = settings()["modules"]["live_streams"].get("channels", [])
            if json.dumps(after, sort_keys=True) != before_key:
                applied += 1
        except Exception as exc:
            audit(f"live stream backfill failed: {exc}", "system", "ERROR")
    if applied:
        audit(f"backfilled live stream channels from {applied} stored RTSP event(s)", "system")


def merge_live_stream_channels(current_channels, incoming_channels):
    current_by_channel = {int(c.get("channel", 0)): dict(c) for c in current_channels or []}
    merged = []
    incoming_by_channel = {}
    for item in incoming_channels or []:
        try:
            incoming_by_channel[int(item.get("channel", 0))] = dict(item)
        except Exception:
            continue

    all_channels = sorted(set(current_by_channel) | set(incoming_by_channel))
    for channel_no in all_channels:
        current = current_by_channel.get(channel_no, {"channel": channel_no})
        incoming = incoming_by_channel.get(channel_no)
        if not incoming:
            merged.append(current)
            continue

        if incoming.get("_reset") or incoming.get("_delete"):
            merged.append({
                "channel": channel_no,
                "name": f"Channel {channel_no:02d}",
                "rtsp_url": "",
                "enabled": False,
                "status": "idle",
                "rtsp_source_url": "",
                "rtsp_username": "",
                "rtsp_password": "",
                "device_sn": "",
                "camera_index": "",
                "converter_id": "",
                "converter_name": "",
                "stream_key": "",
                "updated_at": "",
                "event_timestamp": None,
                "source": "",
            })
            continue

        result = dict(current)
        for key, value in incoming.items():
            if key == "channel":
                result[key] = channel_no
                continue
            if key in ("_reset", "_delete"):
                continue
            if key == "rtsp_password" and value == SECRET_MASK and current.get("rtsp_password"):
                result[key] = current.get("rtsp_password")
                continue
            if key == "rtsp_url" and not value and current.get("rtsp_url"):
                continue
            if key == "name" and (not value or value == f"Channel {channel_no:02d}") and current.get("device_sn"):
                continue
            result[key] = value

        for key in (
            "device_sn",
            "camera_index",
            "converter_id",
            "converter_name",
            "stream_key",
            "rtsp_source_url",
            "rtsp_username",
            "rtsp_password",
            "updated_at",
            "event_timestamp",
            "source",
            "status",
        ):
            if key not in incoming and current.get(key) is not None:
                result[key] = current.get(key)
        merged.append(result)
    return merged


def merge_live_stream_settings(current, incoming):
    result = merge_defaults(incoming, current)
    if isinstance(incoming, dict) and "channels" in incoming:
        result["channels"] = merge_live_stream_channels(current.get("channels", []), incoming.get("channels", []))
    return result


def start_modules(cfg):
    global INTERNAL_SERVER, INTERNAL_THREAD, DFR_SERVER, DFR_THREAD
    with SERVICE_LOCK:
        startup_trace("modules: create module objects")
        MODULES["event_receiver"] = EventReceiverModule(DATA_DIR, module_settings, handle_event_auto_sync)
        MODULES["mqtt"] = MqttModule(DATA_DIR, module_settings)
        MODULES["rid"] = RidModule(DATA_DIR, settings)
        MODULES["mqtt"].add_listener(MODULES["rid"].handle_mqtt)
        MODULES["local_s3"] = LocalS3Module(DATA_DIR, module_settings)
        MODULES["stream"] = StreamModule(DATA_DIR, module_settings)

        host = "0.0.0.0"
        try:
            startup_trace("modules: start internal api")
            INTERNAL_SERVER = ThreadingHTTPServer((host, int(cfg["ports"]["internal_api"])), InternalApiHandler)
            INTERNAL_THREAD = threading.Thread(target=INTERNAL_SERVER.serve_forever, daemon=True)
            INTERNAL_THREAD.start()
            audit(f"internal api started on port {cfg['ports']['internal_api']}", "system")
        except Exception as exc:
            audit(f"internal api failed to start: {exc}", "system", "ERROR")
        try:
            startup_trace("modules: start event receiver")
            MODULES["event_receiver"].start(host, cfg["ports"]["event_api"], None)
        except Exception as exc:
            audit(f"event receiver module failed to start: {exc}", "system", "ERROR")
        try:
            if not advanced_license_enabled():
                raise RuntimeError("Advanced license required; DFR receiver disabled")
            startup_trace("modules: start dfr receiver")
            DFR_SERVER = ThreadingHTTPServer((host, int(cfg["ports"].get("dfr", 19007))), DfrReceiverHandler)
            DFR_THREAD = threading.Thread(target=DFR_SERVER.serve_forever, daemon=True)
            DFR_THREAD.start()
            audit(f"dfr receiver started on port {cfg['ports'].get('dfr', 19007)}", "system")
        except Exception as exc:
            audit(f"dfr receiver failed to start: {exc}", "system", "ERROR")
        try:
            if not advanced_license_enabled():
                raise RuntimeError("Advanced license required; DFR worker disabled")
            startup_trace("modules: start dfr worker")
            start_dfr_worker()
            audit("dfr worker started", "system")
        except Exception as exc:
            audit(f"dfr worker failed to start: {exc}", "system", "ERROR")
        try:
            if not advanced_license_enabled():
                raise RuntimeError("Advanced license required; Local S3 disabled")
            startup_trace("modules: start local s3")
            MODULES["local_s3"].start(host, cfg["ports"]["local_s3"])
        except Exception as exc:
            audit(f"local s3 module failed to start: {exc}", "system", "ERROR")
        try:
            startup_trace("modules: start mqtt")
            MODULES["mqtt"].start()
        except Exception as exc:
            audit(f"mqtt module failed to start: {exc}", "system", "ERROR")
        try:
            startup_trace("modules: backfill live streams")
            backfill_live_streams_from_events()
        except Exception as exc:
            audit(f"live stream backfill failed: {exc}", "system", "ERROR")
        startup_trace("modules: done")


def stop_background_modules():
    global INTERNAL_SERVER, INTERNAL_THREAD, DFR_SERVER, DFR_THREAD
    with SERVICE_LOCK:
        stop_dfr_worker()
        for module in list(MODULES.values()):
            stop = getattr(module, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception as exc:
                    audit(f"{getattr(module, 'name', 'module')} failed to stop: {exc}", "system", "ERROR")
        MODULES.clear()
        if INTERNAL_SERVER:
            try:
                INTERNAL_SERVER.shutdown()
                INTERNAL_SERVER.server_close()
            except Exception as exc:
                audit(f"internal api failed to stop: {exc}", "system", "ERROR")
        if DFR_SERVER:
            try:
                DFR_SERVER.shutdown()
                DFR_SERVER.server_close()
            except Exception as exc:
                audit(f"dfr receiver failed to stop: {exc}", "system", "ERROR")
        DFR_SERVER = None
        DFR_THREAD = None
        INTERNAL_SERVER = None
        INTERNAL_THREAD = None


def restart_background_modules(cfg):
    stop_background_modules()
    start_modules(cfg)


def module_status():
    status = {}
    for name, module in MODULES.items():
        try:
            status[name] = module.status()
        except Exception as exc:
            status[name] = {"error": str(exc)}
    return status


def process_dji_event(raw_body, headers, source_ip):
    module = MODULES.get("event_receiver")
    if not module:
        return 503, {"status": "rejected", "reason": "event receiver module unavailable"}

    signature_valid = module.verify_signature(raw_body, headers.get("x-dji-signature", ""))
    cfg = settings()["modules"]["event_receiver"]
    if not signature_valid and not cfg.get("allow_unsigned_events", True):
        module.add_log("ERROR", "Rejected EventAPI message on dashboard port: invalid signature")
        return 401, {"status": "rejected", "reason": "invalid signature"}
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        module.add_log("ERROR", f"Rejected EventAPI message on dashboard port: invalid JSON - {exc}")
        return 400, {"status": "rejected", "reason": "invalid json"}

    event_type = payload.get("event_type") or payload.get("eventType") or payload.get("type") or payload.get("name") or "Unknown Event"
    project_name = payload.get("project_name") or payload.get("projectName") or payload.get("project") or ""
    device_sn = payload.get("device_sn") or payload.get("deviceSn") or payload.get("sn") or payload.get("deviceSerialNumber") or payload.get("data", {}).get("sn", "")
    with module.db() as conn:
        conn.execute("""
            INSERT INTO events(received_at, event_type, project_name, device_sn, signature_valid, source_ip, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (module.now(), event_type, project_name, device_sn, 1 if signature_valid else 0, source_ip, json.dumps(payload, ensure_ascii=False)))
    active_mode = str((settings().get("fh2") or {}).get("mode") or "cloud")
    module.add_log("INFO", f"[{fh2_mode_label(active_mode)}] Received EventAPI message on dashboard port: {event_type}")
    def run_auto_sync():
        try:
            handle_event_auto_sync(event_type, payload)
        except Exception as exc:
            module.add_log("ERROR", f"Event auto-sync failed: {exc}")

    threading.Thread(target=run_auto_sync, daemon=True).start()
    return 200, {"status": "received", "event_type": event_type}


def process_mqtt_device_status(raw_body, source_ip):
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except Exception:
        payload = {"raw": raw_body.decode("utf-8", errors="replace")}
    active_mode = str((settings().get("fh2") or {}).get("mode") or "cloud")
    item = {
        "type": "mqtt_device_status",
        "fh2_mode": active_mode,
        "fh2_mode_label": fh2_mode_label(active_mode),
        "id": int(time.time() * 1000),
        "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "topic": "fh2/device/status",
        "payload": json.dumps(payload, indent=2, ensure_ascii=False),
        "payload_type": "JSON",
        "bytes": len(raw_body),
        "source": source_ip,
    }
    mqtt = MODULES.get("mqtt")
    if mqtt:
        try:
            mqtt.message_count += 1
            item["id"] = mqtt.message_count
            mqtt.latest = item
            with mqtt.capture_log_path().open("a", encoding="utf-8", errors="replace") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
            mqtt.log("INFO", f"[{fh2_mode_label(active_mode)}] Received FH2 device status callback from {source_ip}: {payload}")
        except Exception as exc:
            audit(f"mqtt status callback log failed: {exc}", "system", "ERROR")
    else:
        audit(f"mqtt status callback from {source_ip}: {payload}", "system")
    return {"ok": True, "status": "received"}


class DfrReceiverHandler(BaseHTTPRequestHandler):
    server_version = "AeroSyncDFR/1.0"

    def log_message(self, fmt, *args):
        return

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/dfr/scylla", "/scylla", "/dfr/hikvision", "/hikvision"):
            self.send_json(404, {"ok": False, "error": "DFR endpoint not found"})
            return
        provider = "Scylla" if "scylla" in path.lower() else "Hikvision"
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        status, payload = process_dfr_webhook(provider, raw_body, self.headers, self.client_address[0])
        self.send_json(status, payload)

class InternalApiHandler(BaseHTTPRequestHandler):
    server_version = "OperationCenterInternal/0.1"

    def log_message(self, fmt, *args):
        audit(f"internal api {fmt % args}", "http")

    def send_json(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass

    def do_GET(self):
        self._request_started = time.perf_counter()
        path = urlparse(self.path).path
        if path in ("/", "/health", "/mqtt/device-status"):
            self.send_json(200, {"status": "ready", "service": "AERO SYNC Internal API"})
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path in ("/", "/mqtt/device-status"):
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b"{}"
            self.send_json(200, process_mqtt_device_status(raw_body, self.client_address[0]))
            return
        self.send_json(404, {"error": "not found"})


class Handler(BaseHTTPRequestHandler):
    server_version = "OperationCenterPortable/0.1"

    def log_message(self, fmt, *args):
        audit(fmt % args, "http")

    def send_bytes(self, status, data, content_type, headers=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            if headers:
                for key, value in headers.items():
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, ssl.SSLError, OSError):
            audit(f"client disconnected while sending {self.path}", "http")

    def send_json(self, status, payload, headers=None):
        started = getattr(self, "_request_started", None)
        if started and str(getattr(self, "path", "")).startswith("/api/"):
            elapsed_ms = (time.perf_counter() - started) * 1000
            if elapsed_ms >= 750 or status >= 400:
                user = "-"
                try:
                    sid = cookie_value(self.headers)
                    user = sessions.get(sid, {}).get("user", "-") if sid else "-"
                except Exception:
                    user = "-"
                diagnostic(f"api_response method={getattr(self, 'command', '-')} path={self.path} status={status} ms={elapsed_ms:.1f} ip={client_ip(self)}", user, "WARN" if status >= 400 else "INFO")
        self.send_bytes(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8", headers)

    def send_text(self, status, text, content_type="text/plain; charset=utf-8", headers=None):
        self.send_bytes(status, text.encode("utf-8"), content_type, headers)

    def stream_snapshot(self, channel):
        if not self.require_login():
            return
        stream = MODULES.get("stream")
        if not stream:
            self.send_json(503, {"ok": False, "error": "stream module unavailable"})
            return
        path = stream.preview_file(int(channel))
        if not path.exists() or path.stat().st_size < 100:
            self.send_json(404, {"ok": False, "error": "preview frame not ready"})
            return
        self.send_bytes(200, path.read_bytes(), "image/jpeg", {"Cache-Control": "no-store"})

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        return json.loads(body.decode("utf-8") or "{}")

    def require_login(self):
        user = current_user(self)
        if not user:
            self.send_json(401, {"ok": False, "error": "login required"})
            return None
        return user

    def require_permission(self, permission):
        user = self.require_login()
        if not user:
            return None
        if permission and permission not in user_permissions(user):
            self.send_json(403, {"ok": False, "error": "permission denied"})
            return None
        return user

    def do_GET(self):
        self._request_started = time.perf_counter()
        path = urlparse(self.path).path

        if path == "/":
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            self.send_text(200, html, "text/html; charset=utf-8", {"Cache-Control": "no-store"})
            return

        if path == "/static/app.js":
            self.send_bytes(200, (STATIC_DIR / "app.js").read_bytes(), "application/javascript; charset=utf-8", {"Cache-Control": "no-store"})
            return

        if path == "/static/styles.css":
            self.send_bytes(200, (STATIC_DIR / "styles.css").read_bytes(), "text/css; charset=utf-8", {"Cache-Control": "no-store"})
            return

        if path == "/static/leaflet.js":
            self.send_bytes(200, (STATIC_DIR / "vendor" / "leaflet" / "leaflet.js").read_bytes(), "application/javascript; charset=utf-8")
            return

        if path == "/static/leaflet.css":
            self.send_bytes(200, (STATIC_DIR / "vendor" / "leaflet" / "leaflet.css").read_bytes(), "text/css; charset=utf-8")
            return

        if path.startswith("/static/assets/"):
            try:
                rel = path.removeprefix("/static/").replace("/", os.sep)
                asset_path = (STATIC_DIR / rel).resolve()
                asset_root = (STATIC_DIR / "assets").resolve()
                if asset_root not in asset_path.parents or not asset_path.is_file():
                    raise FileNotFoundError()
                content_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
                self.send_bytes(200, asset_path.read_bytes(), content_type, {"Cache-Control": "no-store"})
                return
            except Exception:
                self.send_json(404, {"error": "not found"})
                return

        if path.startswith("/stream/snapshot/"):
            try:
                channel = int(path.rsplit("/", 1)[-1])
            except Exception:
                self.send_json(400, {"ok": False, "error": "invalid channel"})
                return
            self.stream_snapshot(channel)
            return

        if path.startswith("/map/tiles/"):
            if not self.require_login():
                return
            cfg = settings()
            root = safe_tile_root(cfg)
            raw = path[len("/map/tiles/"):].strip("/")
            parts = [p for p in raw.split("/") if p not in ("", ".", "..")]
            target = root.joinpath(*parts).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                self.send_json(403, {"ok": False, "error": "invalid tile path"})
                return
            if not target.exists() or not target.is_file():
                self.send_json(404, {"ok": False, "error": "offline tile not found"})
                return
            content_type = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }.get(target.suffix.lower(), "application/octet-stream")
            self.send_bytes(200, target.read_bytes(), content_type, {"Cache-Control": "public, max-age=86400"})
            return

        if path == "/dji/event":
            event_module = MODULES.get("event_receiver")
            event_status = event_module.status() if event_module else {}
            self.send_json(200, {
                "status": "ready",
                "service": "Operation Center FH2 EventAPI Receiver",
                "method_required": "POST",
                "message": "Endpoint is online. DJI FH2 must send POST requests to this URL.",
                **event_status,
            })
            return

        if path == "/api/me":
            lic = license_status()
            user = current_user(self)
            if not user:
                self.send_json(200, {"authenticated": False, "app": APP_NAME, "footer": FOOTER, "about": ABOUT_TEXT, "license": lic})
                return
            us = users()[user]
            cfg = settings()
            self.send_json(200, {
                "authenticated": True,
                "app": APP_NAME,
                "footer": FOOTER,
                "about": ABOUT_TEXT,
                "user": {
                    "username": user,
                    "display_name": us["display_name"],
                    "role": us["role"],
                    "permissions": cfg["roles"].get(us["role"], []),
                    "must_change_password": us.get("must_change_password", False),
                },
                "license": lic,
            })
            return

        if path == "/api/license/status":
            self.send_json(200, {"ok": True, "license": license_status()})
            return

        if path == "/api/openapi/overview":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            if not self.require_permission("openapi"):
                return
            query = parse_qs(urlparse(self.path).query)
            connection_id = str((query.get("connection_id") or [""])[0])
            self.send_json(200, {"ok": True, "overview": openapi_overview(settings(), connection_id)})
            return

        if path == "/api/openapi/projects":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            if not self.require_permission("openapi"):
                return
            query = parse_qs(urlparse(self.path).query)
            page = max(1, int((query.get("page") or [1])[0] or 1))
            page_size = max(1, min(100, int((query.get("page_size") or [50])[0] or 50)))
            connection_id = str((query.get("connection_id") or [""])[0])
            result = openapi_get(settings(), "projects", {"page": page, "page_size": page_size, "usage": "simple"}, connection_id=connection_id)
            self.send_json(200 if result.get("ok") else 502, result)
            return

        if path == "/api/openapi/devices":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            if not self.require_permission("openapi"):
                return
            query = parse_qs(urlparse(self.path).query)
            scope = str((query.get("scope") or ["project"])[0]).lower()
            endpoint_key = "organization_devices" if scope == "organization" else "project_devices"
            connection_id = str((query.get("connection_id") or [""])[0])
            project_uuid = str((query.get("project_uuid") or [""])[0]).strip()
            result = openapi_get(settings(), endpoint_key, project_uuid=project_uuid or None, connection_id=connection_id)
            self.send_json(200 if result.get("ok") else 502, result)
            return

        if path == "/api/openapi/explorer":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            if not self.require_permission("openapi"):
                return
            query = parse_qs(urlparse(self.path).query)
            endpoint_key = str((query.get("endpoint") or ["system_status"])[0])
            connection_id = str((query.get("connection_id") or [""])[0])
            project_uuid = str((query.get("project_uuid") or [""])[0])
            custom_path = str((query.get("custom_path") or [""])[0]).strip() or None
            path_params = {}
            safe_query = {}
            raw_path_params = str((query.get("path_params") or [""])[0]).strip()
            raw_query_params = str((query.get("query_params") or [""])[0]).strip()
            try:
                if raw_path_params:
                    obj = json.loads(raw_path_params)
                    if isinstance(obj, dict): path_params = {str(k): str(v) for k, v in obj.items() if v not in (None, "")}
                if raw_query_params:
                    obj = json.loads(raw_query_params)
                    if isinstance(obj, dict): safe_query = {str(k): v for k, v in obj.items() if v not in (None, "")}
            except Exception:
                self.send_json(400, {"ok": False, "error": "Path/Query parameters must be valid JSON objects"})
                return
            for key in ("page", "page_size", "q", "sort_column", "sort_type", "usage"):
                if key in query and key not in safe_query:
                    safe_query[key] = query[key][-1]
            try:
                result = openapi_get(settings(), endpoint_key, safe_query or None, project_uuid=project_uuid or None, connection_id=connection_id, path_params=path_params, custom_path=custom_path)
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
                return
            # Explorer is diagnostic/read-only: preserve DJI HTTP/application error payloads
            # in a normal AeroSync response so the UI can display/copy the exact result.
            self.send_json(200, result)
            return

        if path == "/api/openapi/logs":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            if not self.require_permission("openapi"):
                return
            query = parse_qs(urlparse(self.path).query)
            connection_id = str((query.get("connection_id") or [""])[0])
            self.send_json(200, {"ok": True, "logs": openapi_read_logs(settings(), connection_id, limit=200)})
            return

        if path == "/api/settings":
            if not self.require_login():
                return
            cfg = settings()
            self.send_json(200, {"settings": mask_client_secrets(cfg), "urls": visible_urls(cfg), "modules": module_status()})
            return

        if path == "/api/status":
            if not self.require_login():
                return
            cfg = settings()
            self.send_json(200, dashboard_payload(cfg))
            return

        if path == "/api/health":
            if not self.require_login():
                return
            self.send_json(200, health_snapshot(settings()))
            return

        if path == "/api/events":
            if not self.require_login():
                return
            cfg = settings()
            data = event_data(cfg, 10)
            data["fh2_mode"] = (cfg.get("fh2") or {}).get("mode", "cloud")
            data["fh2_mode_label"] = fh2_mode_label(data["fh2_mode"])
            self.send_json(200, data)
            return

        if path == "/api/rid":
            if not self.require_login():
                return
            if not self.require_permission("rid"):
                return
            rid_module = MODULES.get("rid")
            self.send_json(200, rid_module.status() if rid_module else {"ok": False, "source_count": 0, "target_count": 0, "sources": [], "targets": []})
            return

        if path == "/api/rid/raw":
            if not self.require_login():
                return
            if not self.require_permission("rid"):
                return
            query = parse_qs(urlparse(self.path).query)
            rid_module = MODULES.get("rid")
            if not rid_module:
                self.send_json(503, {"ok": False, "error": "RID module unavailable"})
                return
            self.send_json(200, rid_module.raw_search((query.get("q") or [""])[0], (query.get("source") or [""])[0], (query.get("limit") or [100])[0]))
            return

        if path == "/api/rid/history":
            if not self.require_login():
                return
            if not self.require_permission("rid"):
                return
            query = parse_qs(urlparse(self.path).query)
            rid_module = MODULES.get("rid")
            if not rid_module:
                self.send_json(503, {"ok": False, "error": "RID module unavailable"})
                return
            self.send_json(200, rid_module.track_history((query.get("q") or [""])[0], (query.get("source") or [""])[0], (query.get("limit") or [250])[0]))
            return

        if path == "/api/rid/track":
            if not self.require_login():
                return
            if not self.require_permission("rid"):
                return
            query = parse_qs(urlparse(self.path).query)
            rid_module = MODULES.get("rid")
            self.send_json(200, rid_module.track_details((query.get("track_id") or [""])[0]) if rid_module else {"ok": False, "error": "RID module unavailable"})
            return

        if path == "/api/mqtt/raw":
            if not self.require_login():
                return
            query = parse_qs(urlparse(self.path).query)
            self.send_json(200, mqtt_raw_search(settings(), query))
            return

        if path == "/api/mqtt":
            if not self.require_login():
                return
            cfg = settings()
            query = parse_qs(urlparse(self.path).query)
            try:
                mqtt_limit = max(1, min(1000, int((query.get("limit") or [12])[0] or 12)))
            except Exception:
                mqtt_limit = 12
            data = mqtt_data(cfg, mqtt_limit)
            data["fh2_mode"] = (cfg.get("fh2") or {}).get("mode", "cloud")
            data["fh2_mode_label"] = fh2_mode_label(data["fh2_mode"])
            data["status_messages"] = mqtt_human_status(
                data.get("messages", []),
                cfg.get("modules", {}).get("map", {}).get("online_timeout_seconds", 90),
            )
            mqtt_module = MODULES.get("mqtt")
            if mqtt_module:
                try:
                    data["module"] = mqtt_module.status()
                    data["available"] = data["available"] or bool(data["module"].get("broker_ready") or data["module"].get("subscriber_ready"))
                except Exception as exc:
                    data["module_error"] = str(exc)
            self.send_json(200, data)
            return

        if path == "/api/media":
            if not self.require_login():
                return
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            self.send_json(200, media_data(settings(), 300))
            return

        if path == "/api/map/history":
            if not self.require_login():
                return
            query = parse_qs(urlparse(self.path).query)
            self.send_json(200, map_history_data(settings(), query))
            return

        if path == "/api/map/history/snapshot":
            if not self.require_login():
                return
            query = parse_qs(urlparse(self.path).query)
            device = str((query.get("device") or [""])[0] or "").strip()
            history = map_history_data(settings(), query)
            svg = map_history_snapshot_svg(history, device)
            if not svg:
                self.send_json(404, {"ok": False, "error": "No map history available for selected device"})
                return
            try:
                snap_root = DATA_DIR / "maps" / "history_snapshots"
                snap_root.mkdir(parents=True, exist_ok=True)
                safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", device or "device")[:80] or "device"
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                (snap_root / f"{safe_name}_{stamp}.svg").write_text(svg, encoding="utf-8")
            except Exception as exc:
                diagnostic(f"map_history_snapshot_save_failed error={exc}", "system", "WARN")
            self.send_bytes(200, svg.encode("utf-8"), "image/svg+xml; charset=utf-8", {"Cache-Control": "no-store"})
            return

        if path == "/api/map":
            if not self.require_login():
                return
            self.send_json(200, map_data(settings()))
            return

        if path == "/api/nvr-sync":
            if not self.require_login():
                return
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            self.send_json(200, nvr_sync_status(settings()))
            return

        if path == "/api/dfr":
            if not self.require_permission("dfr_view"):
                return
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            self.send_json(200, dfr_data(settings()))
            return

        if path == "/api/stream/status":
            if not self.require_login():
                return
            stream = MODULES.get("stream")
            self.send_json(200, stream.status() if stream else {"active": {}, "errors": {}, "stream_info": {}})
            return

        if path == "/api/reports":
            if not self.require_login():
                return
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            query = parse_qs(urlparse(self.path).query)
            self.send_json(200, report_data(settings(), query))
            return

        if path == "/api/logs":
            if not self.require_login():
                return
            cfg = settings()
            self.send_json(200, {"logs": logs_data(cfg), "retention": log_retention_status(cfg)})
            return

        if path == "/api/modules/status":
            if not self.require_login():
                return
            self.send_json(200, module_status())
            return

        if path == "/api/backup":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            if not self.require_login():
                return
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            self.send_json(200, backup_status(settings()))
            return

        if path == "/api/users":
            if not self.require_permission("users"):
                return
            cfg = settings()
            self.send_json(200, {"users": public_users(), "roles": cfg.get("roles", {})})
            return

        if path == "/api/help":
            if not self.require_login():
                return
            cfg = settings()
            self.send_json(200, {
                "ports": cfg["ports"],
                "notes": [
                    "Connection URLs are shown in Settings.",
                    "EventAPI, MQTT, Local S3, Live Stream, and internal service ports can be changed in Settings.",
                    "For WAN use, configure firewall and router port forwarding.",
                    "Self-signed HTTPS is enabled by default for testing.",
                    "Change default admin password before production use.",
                ],
            })
            return

        self.send_text(404, "Not found")

    def do_POST(self):
        self._request_started = time.perf_counter()
        path = urlparse(self.path).path

        if path == "/api/diagnostics/frontend":
            try:
                body = self.read_json_body()
                kind = str(body.get("kind") or "frontend")[:40]
                message = str(body.get("message") or body.get("error") or "")[:500]
                page = str(body.get("page") or body.get("path") or "")[:220]
                source = str(body.get("source") or "")[:160]
                line = str(body.get("line") or body.get("lineno") or "")[:20]
                col = str(body.get("col") or body.get("colno") or "")[:20]
                ms = str(body.get("ms") or "")[:20]
                diagnostic(f"frontend kind={kind} page={page} source={source} line={line} col={col} ms={ms} message={message} ip={client_ip(self)} ua={user_agent(self)}", current_user(self) or "frontend", "WARN")
                self.send_json(200, {"ok": True})
            except Exception as exc:
                diagnostic(f"frontend_diagnostic_failed error={exc} ip={client_ip(self)}", "frontend", "ERROR")
                self.send_json(200, {"ok": False})
            return

        if path == "/api/login":
            try:
                body = self.read_json_body()
                username = str(body.get("username", "")).strip()
                password = str(body.get("password", ""))
                lic = license_status()
                if lic["status"] in ("missing", "invalid", "invalid_machine", "hardware_id_unavailable"):
                    audit_request(self, f"login_blocked license_status={lic['status']}", username or "-", "WARN")
                    self.send_json(403, {"ok": False, "error": lic["message"], "license": lic, "license_required": True})
                    return
                if lic["status"] == "expired" and username.lower() != "admin":
                    audit_request(self, "login_blocked license_expired_admin_only", username or "-", "WARN")
                    self.send_json(403, {"ok": False, "error": "License expired. Admin login required.", "license": lic, "license_expired": True})
                    return
                data = users()
                user = data.get(username)
                limit = settings()["security"]["failed_login_limit"]
                if not user:
                    audit_request(self, "failed_login unknown_user", username, "WARN")
                    self.send_json(403, {"ok": False, "error": "Invalid login"})
                    return
                if user.get("locked"):
                    audit_request(self, "login_blocked account_locked", username, "WARN")
                    self.send_json(423, {"ok": False, "error": "Account locked. Admin reset required."})
                    return
                if not verify_password(password, user["password_hash"]):
                    user["failed_attempts"] = int(user.get("failed_attempts", 0)) + 1
                    if user["failed_attempts"] >= limit:
                        user["locked"] = True
                    save_json(USERS_FILE, data)
                    audit_request(self, f"failed_login attempt={user['failed_attempts']}", username, "WARN")
                    self.send_json(403, {"ok": False, "error": "Invalid login"})
                    return

                user["failed_attempts"] = 0
                save_json(USERS_FILE, data)
                sid = secrets.token_urlsafe(32)
                sessions[sid] = {
                    "user": username,
                    "last_seen": time.time(),
                    "ip": client_ip(self),
                    "user_agent": user_agent(self),
                    "login_at": utc_now(),
                }
                audit_request(self, "login_success", username)
                diagnostic(f"session_login role={user.get('role', '')} ip={client_ip(self)} timeout_min={settings()['security']['session_timeout_minutes']}", username)
                self.send_json(200, {"ok": True, "license": lic}, {
                    "Set-Cookie": f"oc_session={sid}; HttpOnly; Secure; SameSite=Strict; Path=/"
                })
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/license/import":
            try:
                body = self.read_json_body()
                content = str(body.get("content") or body.get("license") or "")
                if "," in content and content.lower().startswith("data:"):
                    content = content.split(",", 1)[1]
                if body.get("base64"):
                    content = base64.b64decode(content, validate=True).decode("utf-8")
                status = save_license_text(content)
                audit_request(self, f"license_imported status={status['status']} id={status.get('license_id','')}", current_user(self) or "license")
                self.send_json(200, {"ok": True, "license": status, "message": "License imported"})
            except Exception as exc:
                audit_request(self, f"license_import_failed error={exc}", current_user(self) or "license", "ERROR")
                self.send_json(400, {"ok": False, "error": str(exc), "license": license_status()})
            return

        if path == "/api/logout":
            sid = cookie_value(self.headers)
            user = current_user(self) or "-"
            sessions.pop(sid, None)
            audit_request(self, "logout", user)
            diagnostic(f"session_logout reason=manual ip={client_ip(self)}", user)
            self.send_json(200, {"ok": True}, {"Set-Cookie": "oc_session=; Max-Age=0; HttpOnly; Secure; SameSite=Strict; Path=/"})
            return

        if path == "/dji/event":
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b""
            status, payload = process_dji_event(raw_body, self.headers, self.client_address[0])
            self.send_json(status, payload)
            return

        if path in ("/dfr/scylla", "/api/dfr/scylla", "/dfr/hikvision", "/api/dfr/hikvision"):
            provider = "Scylla" if "scylla" in path.lower() else "Hikvision"
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b""
            status, payload = process_dfr_webhook(provider, raw_body, self.headers, self.client_address[0])
            self.send_json(status, payload)
            return

        user = self.require_login()
        if not user:
            return

        if path == "/api/openapi/test":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            if not self.require_permission("settings"):
                return
            cfg = settings()
            body = self.read_json_body()
            connection_id = str(body.get("connection_id") or "")
            profile = openapi_profile(cfg, connection_id)
            result = openapi_get(cfg, "system_status", connection_id=connection_id)
            self.send_json(200 if result.get("ok") else 502, result)
            return

        if path == "/api/settings":
            if not self.require_permission("settings"):
                return
            body = self.read_json_body()
            cfg = settings()
            previous_ports = dict(cfg.get("ports", {}))
            previous_mode = str((cfg.get("fh2") or {}).get("mode") or "cloud").lower()
            previous_data_root = cfg.get("storage", {}).get("data_root_path") or str(DATA_DIR.resolve())
            incoming = body.get("settings", {})
            restore_masked_secret(incoming, cfg, ("modules", "mqtt", "password"))
            restore_masked_secret(incoming, cfg, ("modules", "local_s3", "secret_key"))
            restore_masked_secret(incoming, cfg, ("modules", "email", "password"))
            restore_masked_secret(incoming, cfg, ("modules", "dfr", "common", "organization_key"))
            # Preserve masked OpenAPI tokens per connection.
            incoming_openapi = (((incoming.get("modules") or {}).get("openapi") or {}))
            existing_openapi = (((cfg.get("modules") or {}).get("openapi") or {}))
            existing_by_id = {str(x.get("id") or ""): x for x in (existing_openapi.get("connections") or [])}
            for item in incoming_openapi.get("connections") or []:
                if str(item.get("user_token") or "").strip() == SECRET_MASK:
                    old_item = existing_by_id.get(str(item.get("id") or "")) or {}
                    item["user_token"] = old_item.get("user_token") or ""
            restore_masked_secret(incoming, cfg, ("modules", "dfr", "scylla", "bearer_token"))
            restore_masked_secret(incoming, cfg, ("modules", "dfr", "hikvision", "token"))
            if "storage" in incoming:
                cfg["storage"]["data_root_path"] = str(incoming["storage"].get("data_root_path", "")).strip() or str(DATA_DIR.resolve())
                cfg["storage"]["use_module_subfolders"] = bool(incoming["storage"].get("use_module_subfolders", True))
            if "network" in incoming:
                cfg["network"]["local_ip"] = str(incoming["network"].get("local_ip", "")).strip()
                cfg["network"]["wan_ip"] = str(incoming["network"].get("wan_ip", "")).strip()
            requested_mode = previous_mode
            if "fh2" in incoming:
                requested_mode = str((incoming.get("fh2") or {}).get("mode") or previous_mode).strip().lower()
                if requested_mode not in ("cloud", "onprem"):
                    raise ValueError("Invalid FH2 mode")
            sync_network_derived_settings(cfg)
            if "security" in incoming:
                security = incoming.get("security") or {}
                mode = str(security.get("ssl_mode") or cfg["security"].get("ssl_mode") or "self-signed").strip()
                if mode not in ("self-signed", "custom"):
                    raise ValueError("Invalid HTTPS certificate mode")
                cfg["security"]["ssl_mode"] = mode
                cfg["security"]["custom_cert_path"] = str(security.get("custom_cert_path", cfg["security"].get("custom_cert_path", ""))).strip()
                cfg["security"]["custom_key_path"] = str(security.get("custom_key_path", cfg["security"].get("custom_key_path", ""))).strip()
                if mode == "custom":
                    cert_path = Path(cfg["security"]["custom_cert_path"])
                    key_path = Path(cfg["security"]["custom_key_path"])
                    if not cert_path.exists() or not key_path.exists():
                        raise ValueError("Custom HTTPS certificate and private key files must exist")
                    validate_certificate_pair(cert_path, key_path)
            if "ports" in incoming:
                for key in DEFAULT_PORTS:
                    if key in incoming["ports"]:
                        value = int(incoming["ports"][key])
                        if value < 1 or value > 65535:
                            raise ValueError(f"Invalid port: {key}")
                        cfg["ports"][key] = value
            if "modules" in incoming:
                incoming_modules = dict(incoming["modules"])
                if "live_streams" in incoming_modules:
                    cfg["modules"]["live_streams"] = merge_live_stream_settings(
                        cfg["modules"].get("live_streams", {}),
                        incoming_modules.pop("live_streams") or {},
                    )
                    sync_manual_rtsp_channel_16(cfg)
                if "nvr_sync" in incoming_modules:
                    cfg["modules"]["nvr_sync"] = merge_nvr_sync_settings(
                        cfg["modules"].get("nvr_sync", {}),
                        incoming_modules.pop("nvr_sync") or {},
                    )
                if incoming_modules:
                    cfg["modules"] = merge_defaults(incoming_modules, cfg["modules"])
            if "backup" in incoming:
                cfg["backup"] = merge_defaults(incoming["backup"], cfg["backup"])
            if "log_retention" in incoming:
                cfg["log_retention"] = merge_defaults(incoming["log_retention"], cfg.get("log_retention", {}))
            if "roles" in incoming:
                new_roles = incoming.get("roles") or {}
                clean_roles = {}
                allowed_permissions = {item for permissions in DEFAULT_PERMISSIONS.values() for item in permissions}
                for role, permissions in new_roles.items():
                    role_name = str(role).strip()
                    if not role_name:
                        continue
                    if role_name == "Admin":
                        clean_roles[role_name] = list(DEFAULT_PERMISSIONS["Admin"])
                    else:
                        clean_roles[role_name] = [str(p) for p in (permissions or []) if str(p) in allowed_permissions]
                clean_roles.setdefault("Admin", list(DEFAULT_PERMISSIONS["Admin"]))
                cfg["roles"] = clean_roles
            if cfg.get("storage", {}).get("use_module_subfolders"):
                migrate_data_root(previous_data_root, cfg["storage"].get("data_root_path") or DATA_DIR)
                apply_data_root_paths(cfg, cfg["storage"].get("data_root_path") or DATA_DIR)
            store_active_fh2_profile(cfg)
            mode_changed = requested_mode != previous_mode
            if mode_changed:
                activate_fh2_profile(cfg, requested_mode, store_current=False)
            save_json(SETTINGS_FILE, cfg)
            restart_ports = {"event_api", "mqtt_broker", "local_s3", "internal_api", "dfr"}
            ports_changed = any(previous_ports.get(key) != cfg.get("ports", {}).get(key) for key in restart_ports)
            if ports_changed or mode_changed:
                restart_background_modules(cfg)
            if mode_changed:
                audit(f"FlightHub mode changed from {fh2_mode_label(previous_mode)} to {fh2_mode_label(requested_mode)}; EventAPI and MQTT profiles restarted", user)
            else:
                audit("settings saved", user)
            self.send_json(200, {"ok": True, "settings": mask_client_secrets(cfg), "urls": visible_urls(cfg)})
            return

        if path == "/api/rid/device":
            if not self.require_permission("settings"):
                return
            rid_module = MODULES.get("rid")
            if not rid_module:
                self.send_json(503, {"ok": False, "error": "RID module unavailable"})
                return
            body = self.read_json_body()
            action = str(body.get("action") or "add").strip().lower()
            try:
                if action == "add":
                    device = rid_module.add_device(body.get("serial_no"), body.get("device_name"), body.get("brand"))
                    audit_request(self, f"rid_device_added sn={device.get('serial_no')} brand={device.get('brand')}", user)
                elif action == "update":
                    device = rid_module.update_device(body.get("serial_no"), body.get("new_serial_no"), body.get("device_name"), body.get("brand"))
                    audit_request(self, f"rid_device_updated sn={device.get('serial_no')} brand={device.get('brand')}", user)
                elif action == "remove":
                    serial_no = str(body.get("serial_no") or "").strip()
                    rid_module.remove_device(serial_no)
                    device = None
                    audit_request(self, f"rid_device_removed sn={serial_no}", user, "WARN")
                else:
                    raise ValueError("Invalid RID device action")
                payload = rid_module.status()
                payload["device"] = device
                self.send_json(200, payload)
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/certificate/import":
            if not self.require_permission("settings"):
                return
            body = self.read_json_body()
            cert_text = str(body.get("cert") or "").strip()
            key_text = str(body.get("key") or "").strip()
            if "-----BEGIN CERTIFICATE-----" not in cert_text:
                self.send_json(400, {"ok": False, "error": "Certificate must be PEM format with BEGIN CERTIFICATE block"})
                return
            if "-----BEGIN" not in key_text or "PRIVATE KEY-----" not in key_text:
                self.send_json(400, {"ok": False, "error": "Private key must be PEM format with BEGIN PRIVATE KEY or BEGIN RSA PRIVATE KEY block"})
                return
            cert_path = CERT_DIR / "custom_https_cert.pem"
            key_path = CERT_DIR / "custom_https_key.pem"
            tmp_cert = CERT_DIR / "custom_https_cert.tmp"
            tmp_key = CERT_DIR / "custom_https_key.tmp"
            tmp_cert.write_text(cert_text + "\n", encoding="ascii", errors="strict")
            tmp_key.write_text(key_text + "\n", encoding="ascii", errors="strict")
            try:
                validate_certificate_pair(tmp_cert, tmp_key)
                tmp_cert.replace(cert_path)
                tmp_key.replace(key_path)
                cfg = settings()
                cfg["security"]["ssl_mode"] = "custom"
                cfg["security"]["custom_cert_path"] = str(cert_path.resolve())
                cfg["security"]["custom_key_path"] = str(key_path.resolve())
                save_json(SETTINGS_FILE, cfg)
                audit_request(self, "custom_https_certificate_imported", user)
                self.send_json(200, {"ok": True, "settings": cfg, "urls": visible_urls(cfg), "message": "Certificate imported. Restart AERO SYNC to use it."})
            except Exception as exc:
                tmp_cert.unlink(missing_ok=True)
                tmp_key.unlink(missing_ok=True)
                self.send_json(400, {"ok": False, "error": f"Certificate/key validation failed: {exc}"})
            return

        if path == "/api/dfr/test":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            body = self.read_json_body()
            provider = str(body.get("provider") or "Scylla").strip()
            permissions = user_permissions(user)
            if "dfr_settings" not in permissions:
                self.send_json(403, {"ok": False, "error": "permission denied"})
                return
            cfg = settings()
            provider_key = dfr_provider_key(provider)
            provider_cfg = cfg.get("modules", {}).get("dfr", {}).get(provider_key, {})
            if not provider_cfg.get("enabled"):
                self.send_json(400, {"ok": False, "error": f"{provider} DFR provider disabled"})
                return
            common = cfg.get("modules", {}).get("dfr", {}).get("common", {})
            if not str(common.get("fh2_endpoint") or "").strip():
                self.send_json(400, {"ok": False, "error": "FH2 endpoint not configured"})
                return
            if not str(common.get("workflow_uuid") or "").strip():
                self.send_json(400, {"ok": False, "error": "FH2 Workflow UUID not configured"})
                return
            sample = body.get("payload") if isinstance(body.get("payload"), dict) else None
            manual_payload = sample is not None
            if not sample:
                sample, sample_error = dfr_test_payload_for_provider(cfg, provider)
                if sample_error:
                    self.send_json(400, {"ok": False, "error": sample_error})
                    return
            if manual_payload:
                sample = dict(sample)
                sample.setdefault("event", "AERO_SYNC_DFR_TEST")
                sample["test"] = True
                sample["test_id"] = utc_now()
            event_name = dfr_event_name(sample)
            project_uuid, project_error = dfr_project_from_payload(provider_cfg, sample, provider_key)
            if provider_key == "hikvision" and not str(project_uuid or "").strip():
                docks = provider_cfg.get("docks") if isinstance(provider_cfg.get("docks"), list) else []
                for dock in docks:
                    if not isinstance(dock, dict):
                        continue
                    project_uuid = str(dock.get("project_uuid") or dock.get("uuid") or "").strip()
                    if project_uuid:
                        project_error = ""
                        break
            if project_error and not manual_payload:
                retry_max = int(cfg.get("modules", {}).get("dfr", {}).get("retry_max") or 3)
                event = dfr_record_event(cfg, provider, event_name, "Failed to Sent", project_uuid=project_uuid, raw=sample, source_ip=client_ip(self), message=project_error, attempts=retry_max)
                audit_request(self, f"dfr_fh2_test_failed provider={provider} id={event.get('id')} error={project_error}", user, "WARN")
                self.send_json(200, {"ok": False, "event": event, "error": project_error})
                return
            message = "Manual FH2 trigger test queued"
            if project_error and manual_payload:
                message = f"Manual FH2 trigger test queued without mapping: {project_error}"
            event = dfr_record_event(cfg, provider, event_name, "Event Received", project_uuid=project_uuid, raw=sample, source_ip=client_ip(self), message=message)
            try:
                dfr_process_queue_once(cfg)
            except Exception as exc:
                dfr_log(cfg, provider, "Failed to Sent", f"Manual FH2 trigger test failed: {exc}", "ERROR")
            updated = next((item for item in dfr_load_events(cfg) if str(item.get("id")) == str(event.get("id"))), event)
            ok = updated.get("status") == "Event Sent to FH2"
            message = updated.get("message") or updated.get("last_error") or ("FH2 accepted test" if ok else "FH2 test failed")
            audit_request(self, f"dfr_fh2_test provider={provider} id={event.get('id')} status={updated.get('status')}", user, "INFO" if ok else "WARN")
            self.send_json(200, {"ok": ok, "event": updated, "message": message, "error": "" if ok else message})
            return

        if path == "/api/live-stream/channel/clear":
            if not self.require_permission("live_streams"):
                return
            body = self.read_json_body()
            try:
                channel = int(body.get("channel") or 0)
                if channel < 1:
                    raise ValueError("Valid channel is required")
                cfg = settings()
                result = clear_live_stream_channel(cfg, channel)
                audit_request(self, f"live_stream_clear channel={channel} nvr_mappings={result.get('released_nvr_mappings',0)}", user, "WARN")
                self.send_json(200, result)
            except Exception as exc:
                audit_request(self, f"live_stream_clear_failed error={exc}", user, "ERROR")
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/nvr-sync/check":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            if not self.require_permission("nvr_sync"):
                return
            body = self.read_json_body()
            cfg = settings()
            try:
                result = check_nvr_by_id(cfg, str(body.get("id") or ""))
                save_json(SETTINGS_FILE, cfg)
                audit_request(self, f"nvr_check id={body.get('id')} status={result.get('status')}", user)
                self.send_json(200, {"ok": True, "result": result, "nvr_sync": nvr_sync_status(settings())})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/nvr-sync/check-all":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            if not self.require_permission("nvr_sync"):
                return
            cfg = settings()
            try:
                results = check_all_nvrs(cfg)
                audit_request(self, f"nvr_check_all count={len(results)}", user)
                self.send_json(200, {"ok": True, "results": results, "nvr_sync": nvr_sync_status(settings())})
            except Exception as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/nvr-sync/clear-aero-sync-channels":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            if not self.require_permission("nvr_sync"):
                return
            body = self.read_json_body()
            if not body.get("confirm"):
                self.send_json(400, {"ok": False, "error": "Confirmation is required"})
                return
            cfg = settings()
            try:
                result = clear_aero_sync_nvr_channels(cfg)
                audit_request(self, f"nvr_clear_aero_sync_channels removed={result.get('removed_mappings')} ok={result.get('ok')}", user, "WARN")
                self.send_json(200, {"ok": bool(result.get("ok")), **result, "nvr_sync": nvr_sync_status(settings())})
            except Exception as exc:
                audit_request(self, f"nvr_clear_aero_sync_channels_failed error={exc}", user, "ERROR")
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/map/upload-tiles":
            if not self.require_permission("settings"):
                return
            body = self.read_json_body()
            filename = str(body.get("filename") or "tiles.zip")
            content = str(body.get("content") or "")
            if "," in content and content.lower().startswith("data:"):
                content = content.split(",", 1)[1]
            try:
                zip_bytes = base64.b64decode(content, validate=True)
                cfg = settings()
                root = safe_tile_root(cfg)
                result = extract_tile_zip(zip_bytes, root)
                audit_request(self, f"map_tiles_uploaded file={filename} saved={result['saved']} skipped={result['skipped']}", user)
                self.send_json(200, {"ok": True, "result": result, "map": map_data(settings(), 100)})
            except Exception as exc:
                audit_request(self, f"map_tiles_upload_failed file={filename} error={exc}", user, "ERROR")
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/logs/cleanup":
            cfg = settings()
            result = cleanup_log_retention(cfg)
            audit_request(self, f"log_cleanup_manual rotated={result['rotated']} deleted={result['deleted']}", user)
            self.send_json(200, {"ok": True, "result": result, "retention": log_retention_status(settings())})
            return

        if path == "/api/activity":
            body = self.read_json_body()
            action = str(body.get("action") or "activity").strip()
            module = str(body.get("module") or "").strip()
            standalone = bool(body.get("standalone"))
            audit_request(self, f"module_open action={action} module={module} standalone={standalone}", user)
            self.send_json(200, {"ok": True})
            return

        if path == "/api/email/test":
            body = self.read_json_body()
            cfg = settings()
            email_cfg = cfg["modules"].get("email", {})
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            subject = template_text(
                body.get("subject") or email_cfg.get("template_subject") or "Operation Center Test Email",
                {"date": now, "report_type": "Test Email", "user": user},
            )
            body_text = template_text(
                body.get("body") or "Operation Center test email sent successfully.",
                {"date": now, "report_type": "Test Email", "user": user},
            )
            try:
                result = send_email(
                    cfg,
                    body.get("to") or email_cfg.get("default_recipients"),
                    subject,
                    body_text,
                    from_address=body.get("from") or email_cfg.get("from_addresses"),
                    cc=body.get("cc") or email_cfg.get("cc_recipients"),
                    bcc=body.get("bcc") or email_cfg.get("bcc_recipients"),
                )
                audit_request(self, f"email_test_sent to={','.join(result['to'])}", user)
                self.send_json(200, {"ok": True, "result": result})
            except Exception as exc:
                audit_request(self, f"email_test_failed error={exc}", user, "ERROR")
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/reports/email":
            if not advanced_license_enabled():
                self.send_json(403, advanced_license_error())
                return
            body = self.read_json_body()
            cfg = settings()
            email_cfg = cfg["modules"].get("email", {})
            template = find_email_template(cfg, body.get("template_id") or body.get("template"))
            query = {
                "from": [body.get("from", "")],
                "to": [body.get("to_date", "")],
                "section": [body.get("section") or template.get("section") or "all"],
                "device": [body.get("device") or template.get("device") or ""],
            }
            report = report_data(cfg, query)
            formats = body.get("formats") or template.get("formats") or email_cfg.get("default_attachment_formats") or ["csv", "json"]
            if isinstance(formats, str):
                formats = [x.strip() for x in formats.replace(";", ",").split(",") if x.strip()]
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            values = {"date": now, "report_type": template.get("name") or "Operation Center Report", "user": user, "rows": report["summary"]["total"]}
            subject = template_text(body.get("subject") or template.get("subject") or email_cfg.get("template_subject"), values)
            body_text = template_text(body.get("body") or template.get("body") or email_cfg.get("template_body"), values)
            try:
                attachments = report_attachments(report, formats)
                result = send_email(
                    cfg,
                    body.get("to") or template.get("to") or email_cfg.get("default_recipients"),
                    subject,
                    body_text,
                    attachments=attachments,
                    from_address=body.get("from_address") or template.get("from_address") or email_cfg.get("from_addresses"),
                    cc=body.get("cc") or template.get("cc") or email_cfg.get("cc_recipients"),
                    bcc=body.get("bcc") or template.get("bcc") or email_cfg.get("bcc_recipients"),
                )
                audit_request(self, f"report_email_sent template={template.get('name','')} to={','.join(result['to'])} attachments={','.join(result['attachments'])}", user)
                self.send_json(200, {"ok": True, "result": result})
            except Exception as exc:
                audit_request(self, f"report_email_failed error={exc}", user, "ERROR")
                self.send_json(400, {"ok": False, "error": str(exc)})
            return

        if path == "/api/backup":
            cfg = settings()
            result = create_backup(cfg)
            audit(f"manual backup created {result['path']}", user)
            self.send_json(200, {"ok": True, "backup": result, "status": backup_status(settings())})
            return

        if path == "/api/stream/action":
            body = self.read_json_body()
            channel = int(body.get("channel", 1))
            action = body.get("action", "")
            stream = MODULES.get("stream")
            if not stream:
                self.send_json(503, {"ok": False, "error": "stream module unavailable"})
                return
            stream_cfg = settings()["modules"]["live_streams"].get("channels", [])
            channel_cfg = next((c for c in stream_cfg if int(c.get("channel", 0)) == int(channel)), {})
            if action == "start":
                result = stream.start_live(channel)
                audit_request(self, f"live_stream_start channel={channel} name={channel_cfg.get('name','')} device_sn={channel_cfg.get('device_sn','')} result={result.get('status')} ok={result.get('ok')}", user, "INFO" if result.get("ok") else "WARN")
                self.send_json(200, result)
                return
            if action == "stop":
                result = stream.stop_live(channel)
                audit_request(self, f"live_stream_stop channel={channel} name={channel_cfg.get('name','')} device_sn={channel_cfg.get('device_sn','')} result={result.get('status')} ok={result.get('ok')}", user)
                self.send_json(200, result)
                return
            if action == "capture":
                result = stream.capture(channel)
                audit_request(self, f"live_stream_capture channel={channel} name={channel_cfg.get('name','')} device_sn={channel_cfg.get('device_sn','')} path={result.get('path','')} ok={result.get('ok')}", user)
                self.send_json(200, result)
                return
            if action == "record":
                result = stream.toggle_record(channel)
                state = "start" if result.get("recording") else "stop"
                audit_request(self, f"live_stream_record_{state} channel={channel} name={channel_cfg.get('name','')} device_sn={channel_cfg.get('device_sn','')} path={result.get('path','')} ok={result.get('ok')}", user)
                self.send_json(200, result)
                return
            self.send_json(400, {"ok": False, "error": "invalid stream action"})
            return

        if path == "/api/users":
            if not self.require_permission("users"):
                return
            body = self.read_json_body()
            username = str(body.get("username", "")).strip()
            if not username:
                self.send_json(400, {"ok": False, "error": "Username is required"})
                return
            role = str(body.get("role", "User")).strip() or "User"
            cfg = settings()
            if role not in cfg.get("roles", {}):
                self.send_json(400, {"ok": False, "error": "Invalid role"})
                return

            data = users()
            existing = data.get(username, {})
            password = str(body.get("password", ""))
            if not existing and len(password) < 8:
                self.send_json(400, {"ok": False, "error": "Password must be at least 8 characters"})
                return

            record = {
                "username": username,
                "name": str(body.get("name", existing.get("name", ""))).strip(),
                "email": str(body.get("email", existing.get("email", ""))).strip(),
                "display_name": str(body.get("name", existing.get("display_name", username))).strip() or username,
                "role": role,
                "password_hash": existing.get("password_hash", ""),
                "failed_attempts": int(body.get("failed_attempts", existing.get("failed_attempts", 0)) or 0),
                "locked": bool(body.get("locked", existing.get("locked", False))),
                "must_change_password": bool(body.get("must_change_password", existing.get("must_change_password", True))),
                "created_at": existing.get("created_at", utc_now()),
            }
            if password:
                if len(password) < 8:
                    self.send_json(400, {"ok": False, "error": "Password must be at least 8 characters"})
                    return
                record["password_hash"] = hash_password(password)
                record["failed_attempts"] = 0
                record["locked"] = False
                record["must_change_password"] = True
            data[username] = record
            save_json(USERS_FILE, data)
            audit(f"user saved {username}", user)
            self.send_json(200, {"ok": True, "users": public_users()})
            return

        if path == "/api/users/delete":
            if not self.require_permission("users"):
                return
            body = self.read_json_body()
            username = str(body.get("username", "")).strip()
            if not username:
                self.send_json(400, {"ok": False, "error": "Username is required"})
                return
            if username == user:
                self.send_json(400, {"ok": False, "error": "You cannot delete your own active account"})
                return
            data = users()
            record = data.get(username)
            if not record:
                self.send_json(404, {"ok": False, "error": "User not found"})
                return
            if record.get("role") == "Admin":
                self.send_json(400, {"ok": False, "error": "Admin users cannot be deleted"})
                return
            data.pop(username, None)
            save_json(USERS_FILE, data)
            audit(f"user deleted {username}", user, "WARN")
            self.send_json(200, {"ok": True, "users": public_users()})
            return

        if path == "/api/change-password":
            body = self.read_json_body()
            old_password = str(body.get("old_password", ""))
            new_password = str(body.get("new_password", ""))
            data = users()
            record = data[user]
            if not verify_password(old_password, record["password_hash"]):
                self.send_json(403, {"ok": False, "error": "Old password is incorrect"})
                return
            if len(new_password) < 8:
                self.send_json(400, {"ok": False, "error": "Password must be at least 8 characters"})
                return
            record["password_hash"] = hash_password(new_password)
            record["must_change_password"] = False
            save_json(USERS_FILE, data)
            audit("password changed", user)
            self.send_json(200, {"ok": True})
            return

        self.send_text(404, "Not found")


class SafeSSLThreadingHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, ssl_context):
        super().__init__(server_address, handler_class)
        self.ssl_context = ssl_context

    def get_request(self):
        while True:
            sock, addr = self.socket.accept()
            sock.settimeout(8)
            try:
                return self.ssl_context.wrap_socket(sock, server_side=True), addr
            except Exception as exc:
                try:
                    sock.close()
                finally:
                    message = str(exc)
                    if "TLSV1_ALERT_UNKNOWN_CA" not in message and "SSLV3_ALERT_CERTIFICATE_UNKNOWN" not in message:
                        audit(f"rejected incomplete TLS connection from {addr[0]}: {exc}", "http", "WARN")


def main():
    startup_trace("main: start")
    acquire_single_instance_lock()
    cfg = settings()
    startup_trace("main: settings loaded")
    users()
    startup_trace("main: users loaded")
    try:
        cleanup_log_retention(cfg)
    except Exception as exc:
        audit(f"startup log retention cleanup failed: {exc}", "system", "ERROR")
    startup_trace("main: log retention checked")
    cert, key = ensure_certificate(cfg)
    startup_trace("main: certificate ready")
    port = int(cfg["ports"]["dashboard_https"])

    startup_trace("main: start modules")
    start_modules(cfg)
    startup_trace("main: modules started")
    start_email_scheduler()
    startup_trace("main: email scheduler started")
    start_log_retention_scheduler()
    startup_trace("main: log scheduler started")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    startup_trace("main: ssl context ready")
    httpd = SafeSSLThreadingHTTPServer(("0.0.0.0", port), Handler, context)
    startup_trace(f"main: dashboard bound {port}")
    start_auto_backup_check(cfg)

    print(f"{APP_NAME} running at https://127.0.0.1:{port}")
    print(FOOTER)
    print("Default login: admin / admin123")
    print("Press CTRL+C to stop.")
    audit(f"server started on port {port}", "system")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        audit("server stopped", "system")


if __name__ == "__main__":
    main()






















