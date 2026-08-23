import hashlib
import json
import re
import shutil
import threading
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from xml.sax.saxutils import escape


class LocalS3Module:
    name = "local_s3"

    def __init__(self, data_dir: Path, get_settings):
        self.data_dir = Path(data_dir)
        self.get_settings = get_settings
        self.server = None
        self.thread = None
        self.running = False

    def normal_file_path(self, value, fallback):
        path = Path(value or fallback)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        except Exception:
            fallback = Path(fallback)
            fallback.parent.mkdir(parents=True, exist_ok=True)
            return fallback

    def normal_dir_path(self, value, fallback):
        path = Path(value or fallback)
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except Exception:
            fallback = Path(fallback)
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def log_path(self):
        return self.normal_file_path(self.cfg().get("log_path"), self.data_dir / "logs" / "local_s3.log")

    def log(self, message):
        with self.log_path().open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")

    def diagnostic_log_path(self):
        path = self.log_path().with_name("local_s3_diagnostic.log")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def diag(self, message):
        with self.diagnostic_log_path().open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")

    def cfg(self):
        return self.get_settings()["modules"]["local_s3"]

    def storage_root(self):
        return self.normal_dir_path(self.cfg().get("storage_path"), self.data_dir / "storage")

    def bucket(self):
        return self.cfg().get("bucket") or "aeronex"

    def safe_key_parts(self, key):
        key = unquote(key).lstrip("/").replace("\\", "/")
        parts = []
        for raw in key.split("/"):
            if raw in ("", ".", ".."):
                continue
            if re.fullmatch(r"[A-Za-z]:", raw):
                continue
            safe = re.sub(r'[<>:"|?*]', "_", raw).strip()
            if safe:
                parts.append(safe)
        return parts

    def looks_like_local_path(self, value):
        value = (value or "").strip()
        if not value:
            return False
        if re.match(r"^[A-Za-z]:[\\/]", value):
            return True
        return "\\" in value

    def preset_parts(self):
        preset = self.cfg().get("preset_path") or ""
        # FH2 "Preset Path" is an S3 object prefix, not a Windows storage path.
        # If a local path was entered by mistake, ignore it so uploads stay under bucket/key.
        if self.looks_like_local_path(preset):
            return []
        return self.safe_key_parts(preset)

    def bucket_root(self, bucket):
        # FH2 sends the Preset Path as part of the S3 object key. Keep the
        # bucket root clean so list-prefix checks match the uploaded keys.
        root = self.storage_root() / bucket
        root.mkdir(parents=True, exist_ok=True)
        return root

    def key_without_duplicate_preset(self, parts):
        return parts or ["root"]

    def object_path(self, bucket, key):
        parts = self.safe_key_parts(key) or ["root"]
        path = self.bucket_root(bucket) / Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def etag(self, path):
        if not path.exists() or not path.is_file():
            return '"local-etag"'
        h = hashlib.md5()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return '"' + h.hexdigest() + '"'

    def headers(self, extra=None):
        h = {
            "x-amz-request-id": uuid.uuid4().hex[:16],
            "x-amz-id-2": uuid.uuid4().hex,
            "Server": "AERO-SYNC-S3-Compatible",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,PUT,POST,HEAD,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": "Authorization,Content-Type,Content-MD5,x-amz-content-sha256,x-amz-date,x-amz-security-token,x-amz-tagging,*",
            "Access-Control-Expose-Headers": "ETag,x-amz-request-id,x-amz-id-2",
        }
        if extra:
            h.update(extra)
        return h

    def list_xml(self, bucket, prefix="", max_keys=1000):
        base = self.bucket_root(bucket)
        contents = []
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if rel.startswith(".s3meta/") or not rel.startswith(prefix):
                continue
            st = path.stat()
            mod = datetime.utcfromtimestamp(st.st_mtime).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            contents.append(f"<Contents><Key>{escape(rel)}</Key><LastModified>{mod}</LastModified><ETag>{self.etag(path)}</ETag><Size>{st.st_size}</Size><StorageClass>STANDARD</StorageClass></Contents>")
            if len(contents) >= max_keys:
                break
        self.log(f"[LIST] {bucket} prefix={prefix or '-'} base={base} count={len(contents)}")
        self.diag(f"[LIST] bucket={bucket} prefix={prefix or '-'} base={base} max_keys={max_keys} count={len(contents)}")
        return f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>{escape(bucket)}</Name><Prefix>{escape(prefix)}</Prefix><KeyCount>{len(contents)}</KeyCount><MaxKeys>{max_keys}</MaxKeys><IsTruncated>false</IsTruncated>{"".join(contents)}</ListBucketResult>'

    def upload_dir(self, bucket, upload_id):
        path = self.storage_root() / bucket / ".s3meta" / "multipart" / upload_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def sidecar_path(self, bucket, key, suffix):
        parts = self.safe_key_parts(key) or ["root"]
        path = self.storage_root() / bucket / ".s3meta" / "sidecars" / Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.with_name(path.name + suffix)

    def status(self):
        files = [p for p in self.storage_root().rglob("*") if ".s3meta" not in p.parts]
        return {"running": self.running, "storage_path": str(self.storage_root()), "log_path": str(self.log_path()), "file_count": len([p for p in files if p.is_file()])}

    def start(self, host, port):
        if self.running:
            return
        module = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                module.log("%s - %s" % (self.client_address[0], fmt % args))

            def send_data(self, status, data=b"", content_type="application/octet-stream", headers=None):
                self.send_response(status)
                for k, v in module.headers(headers).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
                module.diag(f"[RESPONSE] {self.command} {self.path} status={status} bytes={len(data)} type={content_type}")

            def log_request_detail(self):
                try:
                    length = self.headers.get("Content-Length", "0")
                    auth = "yes" if self.headers.get("Authorization") else "no"
                    user_agent = self.headers.get("User-Agent", "-")
                    module.log(f"[REQUEST] {self.command} {self.path} from {self.client_address[0]} bytes={length} auth={auth}")
                    module.diag(f"[REQUEST] {self.command} {self.path} from={self.client_address[0]} bytes={length} auth={auth} ua={user_agent}")
                except Exception as exc:
                    module.log(f"[ERROR] request log failed: {exc}")
                    module.diag(f"[ERROR] request log failed: {exc}")

            def has_auth(self):
                return bool(self.headers.get("Authorization"))

            def send_access_denied(self):
                xml = '<?xml version="1.0" encoding="UTF-8"?><Error><Code>AccessDenied</Code><Message>Access denied</Message></Error>'
                self.send_data(403, xml.encode("utf-8"), "application/xml")

            def read_body(self):
                if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
                    chunks = []
                    while True:
                        line = self.rfile.readline().strip()
                        if not line:
                            continue
                        size = int(line.split(b";", 1)[0], 16)
                        if size == 0:
                            self.rfile.readline()
                            break
                        chunks.append(self.rfile.read(size))
                        self.rfile.readline()
                    return b"".join(chunks)
                return self.rfile.read(int(self.headers.get("Content-Length", "0")))

            def do_GET(self):
                self.log_request_detail()
                parsed = urlparse(self.path)
                if parsed.path == "/health":
                    self.send_data(200, json.dumps(module.status()).encode("utf-8"), "application/json")
                    return
                if parsed.path in ("/favicon.ico", "/robots.txt"):
                    self.send_data(404, b"Not found", "text/plain")
                    return
                if not self.has_auth():
                    if parsed.path in ("", "/"):
                        self.send_data(200, b"AERO SYNC Local S3 Receiver is running\n", "text/plain")
                    else:
                        self.send_access_denied()
                    return
                parts = parsed.path.strip("/").split("/", 1)
                bucket = parts[0] if parts and parts[0] else module.bucket()
                key = parts[1] if len(parts) > 1 else ""
                qs = parse_qs(parsed.query, keep_blank_values=True)
                if key and "tagging" in qs:
                    tag_path = module.sidecar_path(bucket, key, ".tags.xml")
                    xml = tag_path.read_text(encoding="utf-8") if tag_path.exists() else '<?xml version="1.0" encoding="UTF-8"?><Tagging xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><TagSet></TagSet></Tagging>'
                    self.send_data(200, xml.encode("utf-8"), "application/xml")
                    return
                if key and "uploadId" in qs:
                    upload_id = qs["uploadId"][0]
                    parts_dir = module.upload_dir(bucket, upload_id)
                    parts = []
                    for part in sorted(parts_dir.glob("*.part")):
                        num = int(part.stem)
                        parts.append(f'<Part><PartNumber>{num}</PartNumber><ETag>"local-part-{num}"</ETag><Size>{part.stat().st_size}</Size></Part>')
                    xml = f'<?xml version="1.0" encoding="UTF-8"?><ListPartsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Bucket>{escape(bucket)}</Bucket><Key>{escape(key)}</Key><UploadId>{escape(upload_id)}</UploadId>{"".join(parts)}<IsTruncated>false</IsTruncated></ListPartsResult>'
                    self.send_data(200, xml.encode("utf-8"), "application/xml")
                    return
                if qs.get("list-type", [""])[0] == "2" or not key:
                    xml = module.list_xml(bucket, qs.get("prefix", [""])[0], int(qs.get("max-keys", ["1000"])[0] or 1000))
                    self.send_data(200, xml.encode("utf-8"), "application/xml")
                    return
                path = module.object_path(bucket, key)
                if path.exists() and path.is_file():
                    self.send_data(200, path.read_bytes(), "application/octet-stream", {"ETag": module.etag(path)})
                else:
                    self.send_data(404, b"Not found", "text/plain")

            def do_HEAD(self):
                self.log_request_detail()
                parsed = urlparse(self.path)
                parts = parsed.path.strip("/").split("/", 1)
                bucket = parts[0] if parts and parts[0] else module.bucket()
                key = parts[1] if len(parts) > 1 else ""
                if not key:
                    (module.storage_root() / bucket).mkdir(parents=True, exist_ok=True)
                    self.send_data(200)
                    return
                path = module.object_path(bucket, key)
                self.send_data(200 if path.exists() else 404, headers={"ETag": module.etag(path)} if path.exists() else None)

            def do_PUT(self):
                self.log_request_detail()
                parsed = urlparse(self.path)
                parts = parsed.path.strip("/").split("/", 1)
                bucket = parts[0] if parts and parts[0] else module.bucket()
                key = parts[1] if len(parts) > 1 else ""
                if not key:
                    self.send_data(400, b"Missing key", "text/plain")
                    return
                qs = parse_qs(parsed.query, keep_blank_values=True)
                data = self.read_body()
                if key and "tagging" in qs:
                    tag_path = module.sidecar_path(bucket, key, ".tags.xml")
                    tag_path.write_bytes(data or b'<Tagging xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><TagSet/></Tagging>')
                    module.log(f"[TAGGING] saved tags for {bucket}/{key} ({len(data)} bytes)")
                    self.send_data(200)
                    return
                if "partNumber" in qs and "uploadId" in qs:
                    upload_id = qs["uploadId"][0]
                    part_number = int(qs["partNumber"][0])
                    part_path = module.upload_dir(bucket, upload_id) / f"{part_number:08d}.part"
                    part_path.write_bytes(data)
                    module.log(f"[MULTIPART] {bucket}/{key} part {part_number} uploadId={upload_id} ({len(data)} bytes)")
                    self.send_data(200, headers={"ETag": '"' + hashlib.md5(data).hexdigest() + '"'})
                    return
                path = module.object_path(bucket, key)
                path.write_bytes(data)
                module.log(f"[UPLOAD] {bucket}/{key} -> {path} ({len(data)} bytes)")
                module.diag(f"[UPLOAD] bucket={bucket} key={key} saved={path} bytes={len(data)} etag={module.etag(path)}")
                self.send_data(200, headers={"ETag": module.etag(path)})

            def do_POST(self):
                self.log_request_detail()
                parsed = urlparse(self.path)
                parts = parsed.path.strip("/").split("/", 1)
                bucket = parts[0] if parts and parts[0] else module.bucket()
                key = parts[1] if len(parts) > 1 else ""
                qs = parse_qs(parsed.query, keep_blank_values=True)
                if "delete" in parsed.query:
                    module.log(f"[DELETE] multi-delete request received for bucket {bucket}")
                    xml = '<?xml version="1.0" encoding="UTF-8"?><DeleteResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"></DeleteResult>'
                    self.send_data(200, xml.encode("utf-8"), "application/xml")
                    return
                if "uploads" in qs:
                    upload_id = uuid.uuid4().hex
                    module.upload_dir(bucket, upload_id)
                    module.log(f"[MULTIPART] initiated {bucket}/{key} uploadId={upload_id}")
                    xml = f'<?xml version="1.0" encoding="UTF-8"?><InitiateMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Bucket>{escape(bucket)}</Bucket><Key>{escape(key)}</Key><UploadId>{upload_id}</UploadId></InitiateMultipartUploadResult>'
                    self.send_data(200, xml.encode("utf-8"), "application/xml")
                    return
                if "uploadId" in qs:
                    upload_id = qs["uploadId"][0]
                    final_path = module.object_path(bucket, key)
                    parts_dir = module.upload_dir(bucket, upload_id)
                    with final_path.open("wb") as out:
                        for part in sorted(parts_dir.glob("*.part")):
                            with part.open("rb") as f:
                                shutil.copyfileobj(f, out)
                    shutil.rmtree(parts_dir, ignore_errors=True)
                    module.log(f"[MULTIPART] completed {bucket}/{key} uploadId={upload_id} -> {final_path} ({final_path.stat().st_size} bytes)")
                    xml = f'<?xml version="1.0" encoding="UTF-8"?><CompleteMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Location>/{escape(bucket)}/{escape(key)}</Location><Bucket>{escape(bucket)}</Bucket><Key>{escape(key)}</Key><ETag>{module.etag(final_path)}</ETag></CompleteMultipartUploadResult>'
                    self.send_data(200, xml.encode("utf-8"), "application/xml", {"ETag": module.etag(final_path)})
                    return
                self.send_data(200, b"", "application/xml")

            def do_DELETE(self):
                self.log_request_detail()
                parsed = urlparse(self.path)
                parts = parsed.path.strip("/").split("/", 1)
                bucket = parts[0] if parts and parts[0] else module.bucket()
                key = parts[1] if len(parts) > 1 else ""
                if key:
                    qs = parse_qs(parsed.query, keep_blank_values=True)
                    if "tagging" in qs:
                        tag_path = module.sidecar_path(bucket, key, ".tags.xml")
                        if tag_path.exists():
                            tag_path.unlink()
                        self.send_data(204)
                        return
                    if "uploadId" in qs:
                        upload_id = qs["uploadId"][0]
                        parts_dir = module.upload_dir(bucket, upload_id)
                        shutil.rmtree(parts_dir, ignore_errors=True)
                        module.log(f"[MULTIPART] aborted {bucket}/{key} uploadId={upload_id}")
                        self.send_data(204)
                        return
                    path = module.object_path(bucket, key)
                    if path.exists() and path.is_file():
                        path.unlink()
                self.send_data(204)

            def do_OPTIONS(self):
                self.log_request_detail()
                self.send_data(200)

        self.server = ThreadingHTTPServer((host, int(port)), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.running = True
        self.log(f"[SERVER] Local S3 module started on {host}:{port}")

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.running = False

