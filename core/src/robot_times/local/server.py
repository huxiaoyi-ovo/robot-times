"""本地看板的 HTTP 服务：只监听本机（127.0.0.1），页面就是云端看板用的那一个。

只用标准库，不额外装 Web 框架。防护：
- 只接受 Host 是 localhost / 127.0.0.1 的请求（防 DNS rebinding）；
- 改数据的请求必须带 Content-Type: application/json，且不能来自别的网站（防 CSRF）。
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .api import LocalApi, Reply

log = logging.getLogger("robot_times.server")

WEB_DIR = Path(
    os.environ.get("ROBOT_TIMES_WEB_DIR")
    or Path(__file__).resolve().parents[4] / "web" / "app" / "public"
)
mimetypes.add_type("application/manifest+json", ".webmanifest")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def make_handler(api: LocalApi, web_dir: Path = WEB_DIR) -> type[BaseHTTPRequestHandler]:
    root = web_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "robot-times-local"

        def log_message(self, fmt: str, *args) -> None:  # 默认会把每个请求打到终端，太吵
            log.debug(fmt, *args)

        # ---------- 防护 ----------

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
            return host in LOCAL_HOSTS

        def _write_ok(self) -> bool:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            site = (self.headers.get("Sec-Fetch-Site") or "same-origin").lower()
            return ctype == "application/json" and site in ("same-origin", "none")

        # ---------- 方法 ----------

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

        def do_PUT(self) -> None:
            self._dispatch("PUT")

        def do_DELETE(self) -> None:
            self._dispatch("DELETE")

        def _dispatch(self, method: str) -> None:
            if not self._host_ok():
                return self._json(Reply(403, {"error": "只能从本机访问。"}))
            url = urlsplit(self.path)
            if not url.path.startswith("/api/"):
                return self._static(url.path) if method == "GET" else self._json(Reply(405, {}))
            if method != "GET" and not self._write_ok():
                return self._json(Reply(403, {"error": "请求来源不对。"}))
            if (
                method == "POST" and url.path == "/api/shutdown"
            ):  # robot-times local stop / restart 用
                self._json(Reply(200, {"ok": True}))
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            reply = api.handle(method, url.path, dict(parse_qsl(url.query)), body)
            if reply.stream is not None:
                return self._stream(reply)
            self._json(reply)

        # ---------- 输出 ----------

        def _json(self, reply: Reply) -> None:
            data = json.dumps(reply.body, ensure_ascii=False).encode()
            self.send_response(reply.status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _stream(self, reply: Reply) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for chunk in reply.stream or []:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):  # 看板上点了“停”
                pass

        def _static(self, path: str) -> None:
            rel = path.lstrip("/") or "index.html"
            target = (root / rel).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                target = root / "index.html"  # 单页应用：其他路径都回到首页
            if not target.is_file():
                return self._json(Reply(500, {"error": f"找不到看板页面：{root}"}))
            data = target.read_bytes()
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header(
                "Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else "")
            )
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def is_running(port: int = 8765) -> bool:
    """这个端口上是不是已经开着一个本地看板。"""
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机地址不走代理
    try:
        with opener.open(f"http://127.0.0.1:{port}/api/job", timeout=2) as r:
            return r.headers.get("Server", "").startswith("robot-times-local")
    except OSError:
        return False


def stop(port: int = 8765, wait: float = 10.0) -> bool:
    """让正在运行的本地看板退出。返回 True 表示已经停了（或者本来就没在运行）。"""
    import urllib.request

    if not is_running(port):
        return True
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        opener.open(req, timeout=5).read()
    except OSError:
        pass
    deadline = time.time() + wait
    while time.time() < deadline:
        if not is_running(port):
            return True
        time.sleep(0.3)
    return False


def serve(api: LocalApi, port: int = 8765, web_dir: Path = WEB_DIR) -> ThreadingHTTPServer:
    """建好服务（还没开始处理请求），调用方 serve_forever()。"""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api, web_dir))
    httpd.daemon_threads = True
    return httpd
