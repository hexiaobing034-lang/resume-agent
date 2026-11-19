# -*- coding: utf-8 -*-
"""本地 HTTP 服务：上传页 → 识别 → 编辑页 → 导出。

只监听 127.0.0.1，所有文件都落在项目下的 workspace/，不往系统别处写。
"""
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .build_docx import build
from .docx_reader import read_docx
from .images import sniff
from .pdf_reader import read_pdf
from .recognize import recognize
from .text_reader import read_text, read_text_file
from . import theme as theme_mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
EXAMPLES = os.path.join(ROOT, "examples")
BUILTIN_ASSETS = os.path.join(ROOT, "assets")
WORKSPACE = os.environ.get("RESUME_AGENT_WORKSPACE") or os.path.join(ROOT, "workspace")
UPLOADS = os.path.join(WORKSPACE, "uploads")
ASSETS = os.path.join(WORKSPACE, "assets")
VERSIONS = os.path.join(WORKSPACE, "versions")
CONTENT_FILE = os.path.join(WORKSPACE, "content.json")
THEME_FILE = os.path.join(WORKSPACE, "theme.json")
REPORT_FILE = os.path.join(WORKSPACE, "report.json")

MAX_UPLOAD = 20 * 1024 * 1024
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
LOCK = threading.Lock()


def safe_name(name: str, fallback: str = "简历") -> str:
    name = ILLEGAL.sub("", (name or "").strip()).strip(" .")
    return name[:60] or fallback


def save_state(content: dict, theme: dict, report: dict) -> None:
    os.makedirs(WORKSPACE, exist_ok=True)
    for path, payload in ((CONTENT_FILE, content), (THEME_FILE, theme), (REPORT_FILE, report)):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)


def load_state():
    if not (os.path.exists(CONTENT_FILE) and os.path.exists(THEME_FILE)):
        return None
    with open(CONTENT_FILE, encoding="utf-8") as fh:
        content = json.load(fh)
    with open(THEME_FILE, encoding="utf-8") as fh:
        theme = json.load(fh)
    report = {}
    if os.path.exists(REPORT_FILE):
        with open(REPORT_FILE, encoding="utf-8") as fh:
            report = json.load(fh)
    return {"content": content, "theme": theme, "report": report}


def next_version(base: str) -> int:
    numbers = []
    if os.path.isdir(VERSIONS):
        for name in os.listdir(VERSIONS):
            match = re.match(re.escape(base) + r"_v(\d+)\.docx$", name)
            if match:
                numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def reset_assets() -> None:
    if os.path.isdir(ASSETS):
        shutil.rmtree(ASSETS, ignore_errors=True)
    os.makedirs(ASSETS, exist_ok=True)


def parse_upload(filename: str, data: bytes):
    ext = os.path.splitext(filename)[1].lower()
    os.makedirs(UPLOADS, exist_ok=True)
    reset_assets()
    saved = os.path.join(UPLOADS, "%s_%s" % (time.strftime("%Y%m%d-%H%M%S"),
                                             safe_name(os.path.basename(filename), "upload")))
    with open(saved, "wb") as fh:
        fh.write(data)

    started = time.perf_counter()
    if ext == ".docx":
        document = read_docx(saved, ASSETS, filename)
    elif ext == ".pdf":
        document = read_pdf(saved, ASSETS, filename)
    elif ext in (".txt", ".md"):
        document = read_text_file(saved, filename)
    else:
        raise ValueError("只支持 .docx / .pdf / .txt / .md，当前是 %s" % (ext or "未知格式"))
    read_ms = round((time.perf_counter() - started) * 1000, 1)

    recognition = recognize(document, filename)
    recognition.report["timings"]["read"] = read_ms
    recognition.report["timings"]["total"] = round(
        (recognition.report["timings"].get("total", 0) + read_ms), 1)
    return recognition, saved


class Handler(BaseHTTPRequestHandler):
    server_version = "ResumeAgent/1.0"

    # ------------------------------------------------------------ 基础
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, payload, ctype: str = "application/json; charset=utf-8"):
        body = payload.encode("utf-8") if isinstance(payload, str) else payload
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload, ensure_ascii=False))

    def _file(self, path: str, ctype: str):
        if not os.path.isfile(path):
            self._json(404, {"ok": False, "error": "文件不存在"})
            return
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD:
            raise ValueError("文件超过 20MB，先压缩一下或换个版本。")
        return self.rfile.read(length) if length else b""

    # ------------------------------------------------------------ GET
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html", "/upload"):
            self._file(os.path.join(STATIC, "upload.html"), "text/html; charset=utf-8")
        elif path in ("/editor", "/editor.html"):
            self._file(os.path.join(STATIC, "editor.html"), "text/html; charset=utf-8")
        elif path.startswith("/static/"):
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1].lower()
            ctype = {".css": "text/css", ".js": "application/javascript",
                     ".png": "image/png", ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
            self._file(os.path.join(STATIC, name), ctype + "; charset=utf-8"
                       if ctype.startswith("text") or "javascript" in ctype else ctype)
        elif path == "/api/state":
            state = load_state()
            self._json(200, {"ok": True, "ready": bool(state), "state": state})
        elif path == "/api/download":
            name = safe_name(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                             .get("f", [""])[0], "")
            target = os.path.join(VERSIONS, os.path.basename(name))
            ctype = "application/pdf" if name.lower().endswith(".pdf") else \
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            self._file(target, ctype)
        elif path.startswith("/assets/"):
            name = os.path.basename(path)
            for base in (ASSETS, BUILTIN_ASSETS):
                target = os.path.join(base, name)
                if os.path.isfile(target):
                    ext = os.path.splitext(name)[1].lower()
                    ctype = {".png": "image/png", ".jpg": "image/jpeg",
                             ".jpeg": "image/jpeg", ".gif": "image/gif",
                             ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
                    self._file(target, ctype)
                    return
            self._json(404, {"ok": False, "error": "图片不存在"})
        else:
            self._json(404, {"ok": False, "error": "没有这个页面"})

    # ------------------------------------------------------------ POST
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/upload":
                self._handle_upload()
            elif path == "/api/sample":
                self._handle_sample()
            elif path == "/api/parse-text":
                self._handle_text()
            elif path == "/api/save":
                self._handle_save()
            elif path == "/api/asset":
                self._handle_asset()
            else:
                self._json(404, {"ok": False, "error": "没有这个接口"})
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"ok": False, "error": "服务端出错：%s" % exc})

    def _handle_upload(self):
        data = self._body()
        if not data:
            raise ValueError("没有收到文件内容。")
        raw_name = urllib.parse.unquote(self.headers.get("X-Filename") or "resume.docx")
        with LOCK:
            recognition, saved = parse_upload(raw_name, data)
            save_state(recognition.content, recognition.theme, recognition.report)
        self._json(200, {
            "ok": True,
            "file": saved,
            "content": recognition.content,
            "theme": recognition.theme,
            "report": recognition.report,
        })

    def _handle_sample(self):
        sample = os.path.join(EXAMPLES, "sample_resume.docx")
        if not os.path.exists(sample):
            raise ValueError("示例文件不存在，先运行 python tools/make_examples.py。")
        with open(sample, "rb") as fh:
            data = fh.read()
        with LOCK:
            recognition, saved = parse_upload(os.path.basename(sample), data)
            save_state(recognition.content, recognition.theme, recognition.report)
        self._json(200, {
            "ok": True,
            "file": saved,
            "content": recognition.content,
            "theme": recognition.theme,
            "report": recognition.report,
        })

    def _handle_text(self):
        try:
            payload = json.loads(self._body().decode("utf-8") or "{}")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("请求不是合法 JSON：%s" % exc)
        text = payload.get("text") or ""
        if len(text) < 20:
            raise ValueError("内容太短，至少贴 20 个字。")
        document = read_text(text, "粘贴文本")
        recognition = recognize(document, "粘贴文本")
        with LOCK:
            save_state(recognition.content, recognition.theme, recognition.report)
        self._json(200, {
            "ok": True,
            "file": "",
            "content": recognition.content,
            "theme": recognition.theme,
            "report": recognition.report,
        })

    def _handle_save(self):
        payload = json.loads(self._body().decode("utf-8") or "{}")
        content = payload.get("content")
        theme = theme_mod.normalize(payload.get("theme") or {})
        if not isinstance(content, dict) or not content.get("sections"):
            raise ValueError("内容为空，没什么可保存的。")
        editor = content.setdefault("_editor", {})
        base = safe_name(editor.get("basename") or "我的简历")

        os.makedirs(VERSIONS, exist_ok=True)
        with LOCK:
            previous = load_state() or {}
            version = next_version(base)
            docx_name = "%s_v%d.docx" % (base, version)
            docx_path = os.path.join(VERSIONS, docx_name)
            build(content, theme, docx_path, ASSETS)
            with open(os.path.join(VERSIONS, "%s_v%d.json" % (base, version)),
                      "w", encoding="utf-8") as fh:
                json.dump({"content": content, "theme": theme}, fh,
                          ensure_ascii=False, indent=2)
            save_state(content, theme, previous.get("report", {}))

        self._json(200, {
            "ok": True,
            "version": version,
            "base": base,
            "docx": docx_name,
            "download": "/api/download?f=" + urllib.parse.quote(docx_name),
        })

    def _handle_asset(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        slot = (query.get("slot") or [""])[0]
        if slot not in ("logo", "photo"):
            raise ValueError("slot 只能是 logo（校徽）或 photo（证件照）。")
        data = self._body()
        if len(data) > 8 * 1024 * 1024:
            raise ValueError("图片不要超过 8MB，先压缩一下。")
        ext, width, height = sniff(data)
        if width <= 0 or height <= 0:
            raise ValueError("读不出图片尺寸，换一张试试。")
        if max(width, height) > 8000:
            raise ValueError("图片尺寸太大（超过 8000px），先缩一下。")

        os.makedirs(ASSETS, exist_ok=True)
        name = "%s_%s%s" % (slot, uuid.uuid4().hex[:10], ext)
        with open(os.path.join(ASSETS, name), "wb") as fh:
            fh.write(data)
        self._json(200, {"ok": True, "file": name, "width": width, "height": height,
                         "url": "/assets/" + name})


def serve(port: int = 8765, open_browser: bool = True):
    os.makedirs(WORKSPACE, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d" % port
    print("resume-agent 已启动：%s" % url, flush=True)
    print("按 Ctrl+C 结束。", flush=True)
    if open_browser:
        try:
            import webbrowser
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()
        except Exception:  # noqa: BLE001
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
    finally:
        server.server_close()
