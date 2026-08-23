import json
import shutil
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


class StreamModule:
    name = "stream"

    def __init__(self, data_dir: Path, get_settings):
        self.data_dir = Path(data_dir)
        self.base_dir = self.data_dir.parent
        self.get_settings = get_settings
        self.log_path = self.data_dir / "logs" / "stream_module.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.active = {}
        self.recording = {}
        self.errors = {}
        self.processes = {}
        self.preview_files = {}
        self.stream_info = {}
        self.preview_dir = self.data_dir / "stream_preview"
        self.preview_dir.mkdir(parents=True, exist_ok=True)

    def log(self, message):
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")

    def ffmpeg_path(self):
        candidates = [
            self.base_dir / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe",
            self.base_dir / "tools" / "ffmpeg" / "ffmpeg.exe",
            self.base_dir / "tools" / "ffmpeg.exe",
        ]
        for path in candidates:
            if path.exists():
                return str(path)
        return shutil.which("ffmpeg")

    def ffprobe_path(self):
        ffmpeg = self.ffmpeg_path()
        if ffmpeg:
            path = Path(ffmpeg)
            ffprobe = path.with_name("ffprobe.exe" if path.suffix.lower() == ".exe" else "ffprobe")
            if ffprobe.exists():
                return str(ffprobe)
        candidates = [
            self.base_dir / "tools" / "ffmpeg" / "bin" / "ffprobe.exe",
            self.base_dir / "tools" / "ffmpeg" / "ffprobe.exe",
            self.base_dir / "tools" / "ffprobe.exe",
        ]
        for path in candidates:
            if path.exists():
                return str(path)
        return shutil.which("ffprobe")

    def inspect_stream(self, channel, url):
        ffprobe = self.ffprobe_path()
        if not ffprobe or not url:
            info = {"available": False, "message": "FFprobe not available"}
            self.stream_info[int(channel)] = info
            return info
        cmd = [
            ffprobe,
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            "8000000",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,r_frame_rate,avg_frame_rate,bit_rate",
            "-show_entries",
            "format=bit_rate",
            "-of",
            "json",
            url,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=12,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode != 0:
                info = {"available": False, "message": (proc.stderr or "FFprobe failed").strip()[-240:]}
                self.stream_info[int(channel)] = info
                return info
            data = json.loads(proc.stdout or "{}")
            stream = (data.get("streams") or [{}])[0]
            fmt = data.get("format") or {}
            fps = self.parse_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
            bitrate = int(stream.get("bit_rate") or fmt.get("bit_rate") or 0)
            info = {
                "available": True,
                "codec": str(stream.get("codec_name") or "").upper() or "--",
                "width": int(stream.get("width") or 0),
                "height": int(stream.get("height") or 0),
                "fps": fps,
                "bitrate": bitrate,
                "bitrate_kbps": round(bitrate / 1000, 1) if bitrate else 0,
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
            self.stream_info[int(channel)] = info
            return info
        except subprocess.TimeoutExpired:
            info = {"available": False, "message": "FFprobe timed out"}
        except Exception as exc:
            info = {"available": False, "message": f"FFprobe error: {exc}"}
        self.stream_info[int(channel)] = info
        return info

    def parse_fps(self, value):
        try:
            text = str(value or "")
            if "/" in text:
                num, den = text.split("/", 1)
                den_value = float(den)
                return round(float(num) / den_value, 2) if den_value else 0
            return round(float(text), 2)
        except Exception:
            return 0

    def start_live(self, channel):
        channel = int(channel)
        stream = self.channel_config(channel)
        url = stream.get("rtsp_url", "")
        probe = self.probe_rtsp(url)
        if not probe["ok"]:
            self.active[channel] = False
            self.errors[channel] = probe["error"]
            self.log(f"start live failed channel {channel}: {probe['error']}")
            return {"ok": False, "channel": channel, "status": "error", "error": probe["error"]}

        ffmpeg = self.ffmpeg_path()
        if not ffmpeg:
            error = "FFmpeg not found. Add ffmpeg.exe to tools\\ffmpeg\\bin or install FFmpeg in PATH."
            self.active[channel] = False
            self.errors[channel] = error
            self.log(f"start live failed channel {channel}: {error}")
            return {"ok": False, "channel": channel, "status": "error", "error": error}

        stream_info = self.inspect_stream(channel, url)
        self.stop_process(channel)
        preview_file = self.preview_dir / f"channel_{channel:02d}.jpg"
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            "8000000",
            "-i",
            url,
            "-an",
            "-vf",
            "fps=8,scale=960:-1",
            "-q:v",
            "6",
            "-update",
            "1",
            "-y",
            str(preview_file),
        ]
        log_path = self.data_dir / "logs" / f"stream_channel_{channel:02d}.ffmpeg.log"
        log_path.write_text("", encoding="utf-8")
        log_file = log_path.open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            error = f"FFmpeg start failed: {exc}"
            self.active[channel] = False
            self.errors[channel] = error
            self.log(f"start live failed channel {channel}: {error}")
            return {"ok": False, "channel": channel, "status": "error", "error": error}

        self.processes[channel] = proc
        self.preview_files[channel] = str(preview_file)
        time.sleep(1.2)
        if proc.poll() is not None:
            error = self.ffmpeg_error(log_path) or f"FFmpeg exited with code {proc.returncode}"
            self.processes.pop(channel, None)
            self.active[channel] = False
            self.errors[channel] = error
            self.log(f"start live failed channel {channel}: {error}")
            return {"ok": False, "channel": channel, "status": "error", "error": error}
        self.active[channel] = True
        self.errors.pop(channel, None)
        preview_url = f"/stream/snapshot/{channel}?t={int(datetime.now().timestamp())}"
        self.log(f"start live channel {channel}: preview file {preview_file}")
        return {
            "ok": True,
            "channel": channel,
            "status": "preview starting",
            "message": "Browser preview bridge starting...",
            "preview_url": preview_url,
            "stream_info": stream_info,
            **probe,
        }

    def stop_live(self, channel):
        channel = int(channel)
        self.stop_process(channel)
        self.active[channel] = False
        self.log(f"stop live channel {channel}")
        return {"ok": True, "channel": channel, "status": "stopped"}

    def stop_process(self, channel):
        proc = self.processes.pop(int(channel), None)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    def ffmpeg_error(self, log_path):
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return ""
        for line in reversed(lines[-20:]):
            clean = line.strip()
            if clean:
                if "401 Unauthorized" in clean:
                    return "RTSP authentication failed. Check the current FH2 stream password/URL."
                if "404" in clean:
                    return "RTSP stream path not found or expired."
                return clean
        return ""

    def preview_file(self, channel):
        return self.preview_dir / f"channel_{int(channel):02d}.jpg"

    def channel_config(self, channel):
        channels = self.get_settings()["modules"]["live_streams"].get("channels", [])
        for item in channels:
            if int(item.get("channel", 0)) == int(channel):
                return item
        return {}

    def probe_rtsp(self, url):
        if not url:
            return {"ok": False, "error": "No RTSP URL saved for this channel."}
        try:
            parts = urlsplit(url)
            if parts.scheme.lower() != "rtsp" or not parts.hostname:
                return {"ok": False, "error": "Invalid RTSP URL."}
            port = parts.port or 554
            path = parts.path or "/"
            request_url = f"rtsp://{parts.hostname}:{port}{path}"
            req = (
                f"OPTIONS {request_url} RTSP/1.0\r\n"
                "CSeq: 1\r\n"
                "User-Agent: OperationCenterProbe\r\n"
                "\r\n"
            ).encode("utf-8")
            with socket.create_connection((parts.hostname, port), timeout=6) as sock:
                sock.settimeout(6)
                sock.sendall(req)
                data = sock.recv(4096)
            text = data.decode("utf-8", "replace")
            first = text.splitlines()[0] if text else ""
            if "200" in first:
                server = ""
                for line in text.splitlines():
                    if line.lower().startswith("server:"):
                        server = line.split(":", 1)[1].strip()
                return {"ok": True, "rtsp_status": first, "server": server}
            if "401" in first:
                if parts.username:
                    return {
                        "ok": True,
                        "rtsp_status": first,
                        "server": "",
                        "message": "RTSP authentication required; preview bridge will use saved stream credentials.",
                    }
                return {"ok": False, "error": "RTSP stream requires credentials. Add the full FH2 RTSP URL with username and password.", "rtsp_status": first}
            if "404" in first:
                return {"ok": False, "error": "RTSP stream path not found or expired.", "rtsp_status": first}
            return {"ok": False, "error": f"RTSP probe failed: {first or 'No response'}", "rtsp_status": first}
        except socket.timeout:
            return {"ok": False, "error": "RTSP connection timed out."}
        except OSError as exc:
            return {"ok": False, "error": f"RTSP connection failed: {exc}"}
        except Exception as exc:
            return {"ok": False, "error": f"RTSP probe error: {exc}"}

    def capture(self, channel):
        save_dir = Path(self.get_settings()["modules"]["live_streams"].get("save_path") or (self.data_dir / "recordings"))
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"Channel{int(channel):02d}_{datetime.now():%Y%m%d_%H%M%S}.jpg"
        path.write_bytes(b"")
        self.log(f"capture placeholder channel {channel} -> {path}")
        return {"ok": True, "channel": int(channel), "path": str(path)}

    def toggle_record(self, channel):
        channel = int(channel)
        if self.recording.get(channel):
            path = self.recording.pop(channel)
            self.log(f"stop record channel {channel} -> {path}")
            return {"ok": True, "channel": channel, "recording": False, "path": str(path)}
        save_dir = Path(self.get_settings()["modules"]["live_streams"].get("save_path") or (self.data_dir / "recordings"))
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"Channel{channel:02d}_{datetime.now():%Y%m%d_%H%M%S}.mp4"
        self.recording[channel] = path
        self.log(f"start record placeholder channel {channel} -> {path}")
        return {"ok": True, "channel": channel, "recording": True, "path": str(path)}

    def status(self):
        return {
            "active": self.active,
            "errors": self.errors,
            "recording": {str(k): str(v) for k, v in self.recording.items()},
            "preview_files": self.preview_files,
            "stream_info": {str(k): v for k, v in self.stream_info.items()},
            "ffmpeg": self.ffmpeg_path() or "",
            "ffprobe": self.ffprobe_path() or "",
            "log_path": str(self.log_path),
        }
