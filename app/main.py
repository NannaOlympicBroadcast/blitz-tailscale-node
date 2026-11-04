import asyncio
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TS_STATE_DIR = DATA_DIR / "tailscale"
TS_STATE_DIR.mkdir(parents=True, exist_ok=True)

APP_URL = os.environ.get("PUBLIC_SERVER_URL", "https://ts-exit.blitz48.blitz.cloud")
KEEPALIVE_INTERVAL = int(os.environ.get("KEEPALIVE_INTERVAL", "180"))

app = FastAPI(title="Blitz Tailscale Exit & SOCKS5 Node", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

KEEPALIVE_STATE: Dict[str, Any] = {
    "ping_count": 0,
    "last_ping_at": None,
    "last_status": None,
    "target_url": f"{APP_URL}/api/health",
}


def get_public_base_url(request: Request) -> str:
    env_public = os.environ.get("PUBLIC_SERVER_URL", "").strip()
    if env_public:
        return env_public.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    if host and "blitz.cloud" in host:
        proto = "https"
    return f"{proto}://{host}".rstrip("/")


def run_cmd(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, (res.stdout + res.stderr).strip()
    except Exception as e:
        return 1, str(e)


def get_tailscale_status() -> Dict[str, Any]:
    code, out = run_cmd(["tailscale", "status", "--json"], timeout=5)
    if code != 0 or not out:
        return {"connected": False, "raw": out or "Tailscale not ready"}
    try:
        data = json.loads(out)
        self_node = data.get("Self", {})
        addrs = self_node.get("TailscaleIPs", [])
        ipv4 = addrs[0] if addrs else None
        ipv6 = addrs[1] if len(addrs) > 1 else None
        return {
            "connected": data.get("BackendState") == "Running",
            "backend_state": data.get("BackendState"),
            "node_name": self_node.get("DNSName", "").rstrip("."),
            "host_name": self_node.get("HostName"),
            "tailscale_ip_v4": ipv4,
            "tailscale_ip_v6": ipv6,
            "tailnet": data.get("CurrentTailnet", {}).get("Name"),
            "exit_node_option": bool(self_node.get("ExitNodeOption")),
            "online": self_node.get("Online", False),
            "peers_count": len(data.get("Peer") or {}),
        }
    except Exception as e:
        return {"connected": False, "error": str(e), "raw": out}


async def _keepalive_loop():
    await asyncio.sleep(20)
    while True:
        try:
            target = f"{APP_URL}/api/health?_t={int(time.time()*1000)}"
            def _ping():
                req = urllib.request.Request(
                    target,
                    headers={"User-Agent": "Blitz-Tailscale-KeepAlive/1.0", "Cache-Control": "no-cache"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return resp.status
            status = await asyncio.to_thread(_ping)
            KEEPALIVE_STATE["ping_count"] += 1
            KEEPALIVE_STATE["last_ping_at"] = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
            KEEPALIVE_STATE["last_status"] = f"HTTP {status}"
        except Exception as e:
            KEEPALIVE_STATE["last_ping_at"] = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
            KEEPALIVE_STATE["last_status"] = f"ERR: {e}"
        await asyncio.sleep(KEEPALIVE_INTERVAL)


@app.on_event("startup")
async def on_startup():
    asyncio.create_task(_keepalive_loop())
    # Check if TS_AUTHKEY is in env or stored in /app/data/ts_authkey
    authkey_file = DATA_DIR / "ts_authkey.txt"
    authkey = os.environ.get("TS_AUTHKEY", "")
    if not authkey and authkey_file.exists():
        authkey = authkey_file.read_text(encoding="utf-8").strip()
    if authkey:
        hostname = os.environ.get("TS_HOSTNAME", "blitz-de-exit")
        print(f"[Tailscale] Initializing with authkey for hostname={hostname}...")
        cmd = [
            "tailscale", "up",
            f"--authkey={authkey}",
            f"--hostname={hostname}",
            "--advertise-exit-node",
            "--accept-routes=false",
            "--reset",
        ]
        code, out = run_cmd(cmd, timeout=30)
        print(f"[Tailscale up] code={code}: {out}")


@app.get("/api/health")
def health(request: Request):
    ua = request.headers.get("user-agent", "")
    if "KeepAlive" in ua or "_src" in request.query_params or "_t" in request.query_params:
        KEEPALIVE_STATE["ping_count"] += 1
        KEEPALIVE_STATE["last_ping_at"] = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
        KEEPALIVE_STATE["last_status"] = f"OK from {ua}"
    return {"status": "ok", "service": "blitz-tailscale-node", "time": time.time()}


@app.get("/api/status")
def status(request: Request):
    base_url = get_public_base_url(request)
    ts = get_tailscale_status()
    ip4 = ts.get("tailscale_ip_v4") or "<TAILSCALE_IP>"
    return {
        "tailscale": ts,
        "socks5h_proxy": f"socks5h://{ip4}:1080",
        "http_proxy": f"http://{ip4}:8080",
        "keepalive": KEEPALIVE_STATE,
        "server_url": base_url,
    }


class ConnectRequest(BaseModel):
    authkey: str
    hostname: Optional[str] = "blitz-de-exit"


@app.post("/api/connect")
def connect_tailscale(req: ConnectRequest):
    key = req.authkey.strip()
    if not key:
        raise HTTPException(status_code=400, detail="AuthKey cannot be empty")
    hname = req.hostname.strip() or "blitz-de-exit"
    (DATA_DIR / "ts_authkey.txt").write_text(key, encoding="utf-8")
    cmd = [
        "tailscale", "up",
        f"--authkey={key}",
        f"--hostname={hname}",
        "--advertise-exit-node",
        "--accept-routes=false",
        "--reset",
    ]
    code, out = run_cmd(cmd, timeout=35)
    if code != 0:
        raise HTTPException(status_code=500, detail=f"Tailscale connection failed: {out}")
    return {"status": "connected", "output": out, "info": get_tailscale_status()}


@app.get("/", response_class=HTMLResponse)
def index_page():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))
