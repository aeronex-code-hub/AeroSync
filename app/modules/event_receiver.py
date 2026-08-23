import base64
import hashlib
import hmac
import json
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


class EventReceiverModule:
    name = "event_receiver"

    def __init__(self, data_dir: Path, get_settings, on_event=None):
        self.data_dir = Path(data_dir)
        self.get_settings = get_settings
        self.on_event = on_event
        self.server = None
        self.thread = None
        self.running = False
        self.init_db()

    def cfg(self):
        return self.get_settings()["modules"]["event_receiver"]

    def _normal_file_path(self, value, default_file):
        path = Path(value or default_file)
        if path.exists() and path.is_dir():
            path = path / default_file.name
        elif not path.suffix:
            path = path / default_file.name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        except Exception:
            default_file.parent.mkdir(parents=True, exist_ok=True)
            return default_file

    def db_path(self):
        return self._normal_file_path(self.cfg().get("event_db_path"), self.data_dir / "events.db")

    def log_path(self):
        return self._normal_file_path(self.cfg().get("log_path"), self.data_dir / "logs" / "event_receiver.log")

    def now(self):
        return datetime.now(timezone.utc).isoformat()

    def db(self):
        conn = sqlite3.connect(self.db_path(), timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    received_at TEXT NOT NULL,
                    event_type TEXT,
                    project_name TEXT,
                    device_sn TEXT,
                    signature_valid INTEGER NOT NULL,
                    source_ip TEXT,
                    raw_json TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                )
            """)

    def add_log(self, level, message):
        level = (level or "INFO").upper()
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S,%f}"[:-3] + f" {level} {message}\n"
        try:
            with self.log_path().open("a", encoding="utf-8", errors="replace") as f:
                f.write(line)
        except Exception:
            pass
        try:
            with self.db() as conn:
                conn.execute(
                    "INSERT INTO app_logs(created_at, level, message) VALUES (?, ?, ?)",
                    (self.now(), level, message),
                )
        except Exception:
            pass

    def verify_signature(self, raw_body, signature):
        token = self.cfg().get("fh2_org_token", "")
        if not token:
            return False
        digest = hmac.new(token.encode("utf-8"), raw_body, hashlib.sha256).digest()
        provided = (signature or "").strip()
        return (
            hmac.compare_digest(provided, digest.hex())
            or hmac.compare_digest(provided, base64.b64encode(digest).decode("utf-8"))
        )

    def status(self):
        try:
            with self.db() as conn:
                count = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
                last = conn.execute("SELECT received_at FROM events ORDER BY id DESC LIMIT 1").fetchone()
        except Exception:
            count = 0
            last = None
        return {
            "running": self.running,
            "event_count": count,
            "last_event_at": last["received_at"] if last else None,
        }

    def save_event(self, payload, raw_body, signature_valid, source_ip):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        event_type = (
            payload.get("event_type")
            or payload.get("eventType")
            or payload.get("type")
            or payload.get("name")
            or "Unknown Event"
        )
        project_name = payload.get("project_name") or payload.get("projectName") or payload.get("project") or ""
        device_sn = (
            payload.get("device_sn")
            or payload.get("deviceSn")
            or payload.get("sn")
            or payload.get("deviceSerialNumber")
            or data.get("sn", "")
        )
        raw_json = json.dumps(payload, ensure_ascii=False)
        with self.db() as conn:
            conn.execute(
                """
                INSERT INTO events(received_at, event_type, project_name, device_sn, signature_valid, source_ip, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (self.now(), event_type, project_name, device_sn, 1 if signature_valid else 0, source_ip, raw_json),
            )
        return event_type

    def run_callback(self, event_type, payload):
        if not self.on_event:
            return
        try:
            self.on_event(event_type, payload)
        except Exception as exc:
            self.add_log("ERROR", f"Event auto-sync failed: {exc}")

    def start(self, host, port, ssl_context=None):
        if self.running:
            return

        module = self
        port = int(port)

        class EventHttpServer(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        class Handler(BaseHTTPRequestHandler):
            server_version = "AeroSyncEventAPI/1.0"

            def log_message(self, fmt, *args):
                message = fmt % args
                module.add_log("INFO", f"{self.client_address[0]} - {message}")

            def send_json(self, status_code, payload):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def valid_path(self):
                return urlparse(self.path).path.rstrip("/") == "/dji/event"

            def do_GET(self):
                path = urlparse(self.path).path.rstrip("/")
                if path == "/dji/event":
                    self.send_json(200, {
                        "status": "ready",
                        "service": "AERO SYNC EventAPI Receiver",
                        "method_required": "POST",
                        "message": "Endpoint is online.",
                    })
                    return
                if path == "/health":
                    self.send_json(200, {"status": "online", **module.status()})
                    return
                if path == "/api/events":
                    try:
                        limit = min(int(parse_qs(urlparse(self.path).query).get("limit", ["100"])[0]), 500)
                    except Exception:
                        limit = 100
                    with module.db() as conn:
                        rows = [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
                    self.send_json(200, rows)
                    return
                self.send_json(404, {"error": "not found"})

            def do_POST(self):
                if not self.valid_path():
                    self.send_json(404, {"error": "not found"})
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    length = 0
                raw_body = self.rfile.read(length) if length else b""

                cfg = module.cfg()
                signature_valid = module.verify_signature(raw_body, self.headers.get("x-dji-signature", ""))
                if not signature_valid and not cfg.get("allow_unsigned_events", True):
                    module.add_log("ERROR", "Rejected EventAPI message: invalid signature")
                    self.send_json(401, {"status": "rejected", "reason": "invalid signature"})
                    return

                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except Exception as exc:
                    module.add_log("ERROR", f"Rejected EventAPI message: invalid JSON - {exc}")
                    self.send_json(400, {"status": "rejected", "reason": "invalid json"})
                    return

                try:
                    event_type = module.save_event(payload, raw_body, signature_valid, self.client_address[0])
                except Exception as exc:
                    module.add_log("ERROR", f"Rejected EventAPI message: database save failed - {exc}")
                    self.send_json(500, {"status": "rejected", "reason": "database save failed"})
                    return

                module.add_log("INFO", f"Received EventAPI message: {event_type}")
                self.send_json(200, {"status": "received"})
                threading.Thread(target=module.run_callback, args=(event_type, payload), daemon=True).start()

        self.server = EventHttpServer((host, port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.running = True
        self.add_log("INFO", f"EventAPI module started on {host}:{port}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.running = False
