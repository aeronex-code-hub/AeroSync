import json
import socket
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path


class MqttModule:
    name = "mqtt"

    def __init__(self, data_dir: Path, get_settings):
        self.data_dir = Path(data_dir)
        self.get_settings = get_settings
        self.log_dir = self.data_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.process = None
        self.broker_process = None
        self.broker_thread = None
        self.thread = None
        self.running = False
        self.message_count = 0
        self.latest = None
        self.missing_tool_logged = False
        self.capture_log_path().touch(exist_ok=True)
        self.dashboard_log_path().touch(exist_ok=True)
        self.broker_ready = False
        self.subscriber_ready = False
        self.listeners = []

    def normal_file_path(self, value, fallback):
        path = Path(value or fallback)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        except Exception:
            fallback = Path(fallback)
            fallback.parent.mkdir(parents=True, exist_ok=True)
            return fallback

    def capture_log_path(self):
        return self.normal_file_path(self.cfg().get("capture_log_path"), self.log_dir / "mqtt_capture.log")

    def dashboard_log_path(self):
        return self.normal_file_path(self.cfg().get("dashboard_log_path"), self.log_dir / "mqtt_dashboard.log")

    def log(self, level, message):
        with self.dashboard_log_path().open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{level}] {message}\n")

    def cfg(self):
        return self.get_settings()["modules"]["mqtt"]

    def tool_path(self, name):
        candidates = [
            self.data_dir.parent / "tools" / "mosquitto" / name,
            self.data_dir.parent / "mosquitto" / name,
            Path(name),
        ]
        for p in candidates:
            if p.exists():
                return str(p)
        return ""

    def port_open(self, host, port):
        try:
            with socket.create_connection((host, int(port)), timeout=1):
                return True
        except Exception:
            return False

    def mosquitto_conf(self, port):
        conf_path = self.data_dir / "mqtt_mosquitto.conf"
        conf_path.write_text(
            "\n".join([
                f"listener {int(port)} 0.0.0.0",
                "protocol mqtt",
                "allow_anonymous true",
                "persistence false",
                "connection_messages true",
                "log_timestamp true",
                "log_type error",
                "log_type warning",
                "log_type notice",
                "log_dest stdout",
                "",
            ]),
            encoding="utf-8",
        )
        return conf_path

    def start_broker_if_needed(self, port):
        if self.port_open("127.0.0.1", port):
            self.broker_ready = True
            return True
        broker = self.tool_path("mosquitto.exe")
        if not broker:
            self.log("WARN", "MQTT broker not started: mosquitto.exe not found under tools\\mosquitto.")
            return False
        conf = self.mosquitto_conf(port)
        cmd = [broker, "-c", str(conf)]
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.broker_process = subprocess.Popen(
                cmd,
                cwd=str(Path(broker).parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
            self.log("INFO", f"Starting MQTT broker on 0.0.0.0:{port}")
            self.broker_thread = threading.Thread(target=self.read_broker_output, daemon=True)
            self.broker_thread.start()
            for _ in range(20):
                if self.port_open("127.0.0.1", port):
                    self.log("INFO", f"MQTT broker ready on port {port}")
                    self.broker_ready = True
                    return True
                time.sleep(0.25)
            self.log("WARN", f"MQTT broker did not open port {port} yet")
            self.broker_ready = False
            return False
        except Exception as exc:
            self.log("ERROR", f"MQTT broker unavailable: {exc}")
            self.broker_ready = False
            return False

    def read_broker_output(self):
        try:
            for line in self.broker_process.stdout:
                text = line.strip()
                if text:
                    self.log("BROKER", text)
        except Exception:
            pass

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while self.running:
            cfg = self.cfg()
            port = int(self.get_settings()["ports"].get("mqtt_broker", 19003))
            self.start_broker_if_needed(port)
            subscriber = self.tool_path("mosquitto_sub.exe")
            if not subscriber:
                if not self.missing_tool_logged:
                    self.log("WARN", "MQTT subscriber not started: mosquitto_sub.exe not found. Bundle Mosquitto under tools\\mosquitto or install it in PATH.")
                    self.missing_tool_logged = True
                time.sleep(30)
                continue
            self.missing_tool_logged = False
            cmd = [
                subscriber,
                "-h", str(cfg.get("host") or "127.0.0.1"),
                "-p", str(port),
                "-u", str(cfg.get("username") or ""),
                "-P", str(cfg.get("password") or ""),
                "-t", str(cfg.get("topic") or "#"),
                "-v",
                "-F", "__MQTTMSG__%t__MQTTSPLIT__%p",
            ]
            try:
                self.log("INFO", f"Starting MQTT subscriber {cfg.get('host') or '127.0.0.1'}:{port} topic={cfg.get('topic') or '#'}")
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                self.process = subprocess.Popen(
                    cmd,
                    cwd=str(Path(subscriber).parent),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
                self.subscriber_ready = True
                for line in self.process.stdout:
                    if not self.running:
                        break
                    self.handle_line(line.rstrip("\n"))
                self.subscriber_ready = False
                self.log("WARN", "MQTT subscriber stopped")
            except Exception as exc:
                self.subscriber_ready = False
                self.log("ERROR", f"MQTT subscriber unavailable: {exc}")
            time.sleep(10)

    def add_listener(self, callback):
        if callable(callback) and callback not in self.listeners:
            self.listeners.append(callback)

    def handle_line(self, line):
        marker = "__MQTTMSG__"
        sep = "__MQTTSPLIT__"
        if not line.startswith(marker) or sep not in line:
            if line.strip():
                self.log("INFO", line)
            return
        topic, payload = line[len(marker):].split(sep, 1)
        payload_type = "RAW"
        formatted = payload
        parsed = None
        try:
            parsed = json.loads(payload)
            payload_type = "JSON"
            formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        except Exception:
            pass
        item = {
            "type": "mqtt",
            "id": self.message_count + 1,
            "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "topic": topic.strip(),
            "payload": formatted,
            "payload_type": payload_type,
            "bytes": len(payload.encode("utf-8", errors="replace")),
        }
        consumed = False
        for callback in list(self.listeners):
            try:
                if callback(item, parsed if payload_type == "JSON" else payload) is True:
                    consumed = True
            except Exception as exc:
                self.log("WARN", f"MQTT listener error: {exc}")
        if consumed:
            return
        self.message_count += 1
        item["id"] = self.message_count
        self.latest = item
        with self.capture_log_path().open("a", encoding="utf-8", errors="replace") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def status(self):
        return {
            "running": self.running,
            "broker_ready": self.broker_ready,
            "subscriber_ready": self.subscriber_ready,
            "capture_log_path": str(self.capture_log_path()),
            "dashboard_log_path": str(self.dashboard_log_path()),
            "message_count": self.message_count,
            "latest": self.latest,
        }

    def stop(self):
        self.running = False
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
        if self.broker_process:
            try:
                self.broker_process.terminate()
            except Exception:
                pass
