"""Smoke-test the production shape: built React assets served by FastAPI."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "web" / "dist"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(url: str) -> tuple[int, str, str]:
    try:
        with urlopen(url, timeout=10) as response:  # noqa: S310 - localhost only
            return (
                response.status,
                response.headers.get("content-type", ""),
                response.read().decode(),
            )
    except HTTPError as exc:
        return exc.code, exc.headers.get("content-type", ""), exc.read().decode()


def main() -> None:
    if not (DIST / "index.html").is_file():
        raise SystemExit("web/dist/index.html is missing; run `npm run build` first")

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.pop("UFC_ELO_REFRESH_TOKEN", None)
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "api.main:app",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise RuntimeError(f"server exited during startup:\n{output}")
            try:
                status, _, body = _request(f"{base}/api/health")
                if status == 200 and json.loads(body).get("caches_loaded") is True:
                    break
            except (URLError, TimeoutError, json.JSONDecodeError):
                time.sleep(0.25)
        else:
            raise RuntimeError("server did not become healthy within 30 seconds")

        status, content_type, index = _request(f"{base}/")
        assert status == 200 and "text/html" in content_type
        asset = re.search(r'(?:src|href)="(/assets/[^"]+)"', index)
        assert asset, "built index did not reference a hashed asset"
        asset_status, _, _ = _request(base + asset.group(1))
        assert asset_status == 200

        search_status, _, search_body = _request(f"{base}/api/fighters?search=makhachev")
        fighter_id = json.loads(search_body)["results"][0]["id"]
        assert search_status == 200
        for route in ("/events", "/matchups", f"/fighter/{fighter_id}"):
            route_status, route_type, _ = _request(base + route)
            assert route_status == 200 and "text/html" in route_type, route

        api_status, api_type, api_body = _request(f"{base}/api/rankings")
        assert api_status == 200 and "application/json" in api_type
        assert json.loads(api_body)["divisions"]

        missing_status, missing_type, missing_body = _request(f"{base}/api/not-a-route")
        assert missing_status == 404 and "application/json" in missing_type
        assert json.loads(missing_body)["error"]
        print("deployment smoke test passed")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
