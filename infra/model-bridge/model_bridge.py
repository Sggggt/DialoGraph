from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


class BridgeConfig:
    target_base_url: str = ""
    resolve_ip: str | None = None
    timeout: int = 120


class ModelBridgeHandler(BaseHTTPRequestHandler):
    server_version = "CourseKGModelBridge/1.0"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"status": "ok", "target_base_url": BridgeConfig.target_base_url})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        target_url = BridgeConfig.target_base_url.rstrip("/") + self.path
        body_length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(body_length)
        try:
            status_code, response_body = self._forward_with_curl(target_url, body)
        except Exception as exc:
            self._send_json(502, {"error": {"message": str(exc), "type": type(exc).__name__}})
            return
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response_body)

    def log_message(self, format: str, *args: object) -> None:
        if self.path != "/health":
            super().log_message(format, *args)

    def _forward_with_curl(self, target_url: str, body: bytes) -> tuple[int, bytes]:
        parsed = urlparse(target_url)
        if not parsed.hostname:
            raise ValueError(f"Invalid target URL: {target_url}")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        auth_header = self.headers.get("Authorization")
        if not auth_header:
            raise ValueError("Missing Authorization header")

        body_path = None
        output_path = None
        config_path = None
        try:
            with tempfile.NamedTemporaryFile("wb", delete=False, suffix=".json") as body_file:
                body_file.write(body)
                body_path = body_file.name
            with tempfile.NamedTemporaryFile("wb", delete=False, suffix=".json") as output_file:
                output_path = output_file.name
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".curl") as config_file:
                config_file.write(f'url = "{target_url}"\n')
                config_file.write("request = POST\n")
                config_file.write(f"connect-timeout = {min(BridgeConfig.timeout, 30)}\n")
                config_file.write(f"max-time = {BridgeConfig.timeout}\n")
                config_file.write("retry = 2\n")
                config_file.write("retry-delay = 1\n")
                config_file.write("retry-all-errors\n")
                if BridgeConfig.resolve_ip:
                    config_file.write(f'resolve = "{parsed.hostname}:{port}:{BridgeConfig.resolve_ip}"\n')
                config_file.write(f'header = "Authorization: {auth_header}"\n')
                config_file.write('header = "Content-Type: application/json"\n')
                config_file.write(f'data-binary = "@{body_path.replace("\\", "/")}"\n')
                config_file.write(f'output = "{output_path.replace("\\", "/")}"\n')
                config_file.write('write-out = "%{http_code}"\n')
                config_path = config_file.name
            result = subprocess.run(
                ["curl.exe", "-sS", "-K", config_path],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=BridgeConfig.timeout + 10,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or f"curl exited with {result.returncode}")
            try:
                status_code = int(result.stdout.strip()[-3:])
            except ValueError as exc:
                raise RuntimeError(f"curl did not return a valid HTTP status: {result.stdout!r}") from exc
            with open(output_path, "rb") as output_file:
                response_body = output_file.read()
            if not response_body:
                response_body = b"{}"
            return status_code, response_body
        finally:
            for path in (body_path, output_path, config_path):
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

    def _send_json(self, status_code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--target-base-url", required=True)
    parser.add_argument("--resolve-ip", default="")
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    BridgeConfig.target_base_url = args.target_base_url.rstrip("/")
    BridgeConfig.resolve_ip = args.resolve_ip or None
    BridgeConfig.timeout = args.timeout
    server = ThreadingHTTPServer((args.host, args.port), ModelBridgeHandler)
    print(f"Model bridge listening on http://{args.host}:{args.port} -> {BridgeConfig.target_base_url}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
