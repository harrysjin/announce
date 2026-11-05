#!/usr/bin/env bash
# Local checks only: no deployment and no live dictionary requests.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
if [[ -x .venv/bin/python ]]; then
  python_bin=.venv/bin/python
else
  python_bin=python3
fi
command -v npm >/dev/null
command -v "$python_bin" >/dev/null
if [[ ! -d node_modules ]]; then
  echo 'Frontend dependencies missing. Run npm ci first.' >&2
  exit 1
fi
npm run build
"$python_bin" -m unittest discover -s tests -v
exec "$python_bin" - <<'PY'
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

def stop_check(signum, _frame):
    raise SystemExit(128 + signum)

signal.signal(signal.SIGTERM, stop_check)
signal.signal(signal.SIGINT, stop_check)

# Only loopback checks: proxy settings remain untouched for normal API traffic.
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
environment = os.environ.copy()
environment["PORT"] = str(port)
opener = build_opener(ProxyHandler({}))
base = f"http://127.0.0.1:{port}"
with tempfile.TemporaryFile() as server_log:
    server = None
    try:
        server = subprocess.Popen(
            ["bash", str(Path("scripts/start.sh").resolve())],
            env=environment, stdout=server_log, stderr=server_log,
        )
        deadline = time.monotonic() + 30
        while True:
            if server.poll() is not None:
                raise RuntimeError("Server exited before readiness; run scripts/start.sh to diagnose.")
            try:
                with opener.open(base + "/api/health", timeout=2) as response:
                    health = json.load(response)
                    assert response.status == 200 and health["ok"] is True
                    assert response.headers.get("Cache-Control") == "no-store"
                break
            except (URLError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Local server was not ready within 30 seconds.") from None
                time.sleep(0.2)
        with opener.open(base + "/", timeout=5) as response:
            html = response.read().decode("utf-8")
            assert response.status == 200 and '<div id="root"></div>' in html
        asset_paths = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
        assert asset_paths, "Built frontend assets are missing from the HTML."
        for path in asset_paths:
            with opener.open(base + path, timeout=5) as response:
                assert response.status == 200 and response.read()
        with opener.open(base + "/api/sample", timeout=5) as response:
            assert response.status == 200 and response.read().startswith(b"%PDF-")
        print("Release checks passed: build, unit tests, local health, frontend assets, sample PDF.")
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
PY
