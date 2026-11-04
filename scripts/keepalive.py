#!/usr/bin/env python3
"""
Blitz Tailscale Node — Keep-Alive Daemon
防止 Blitz 平台在 15 分钟无公网流量时休眠容器。
默认每 180 秒 (3 分钟) 向公网健康检查接口发起带防缓存时间戳的 HTTPS 请求。
"""
import argparse
import datetime
import os
import time
import urllib.error
import urllib.request

DEFAULT_URL = os.environ.get("KEEPALIVE_URL", "https://ts-exit.blitz48.blitz.cloud/api/health")
DEFAULT_INTERVAL = int(os.environ.get("KEEPALIVE_INTERVAL", "180"))


def ping_once(url: str, timeout: int = 15) -> tuple[bool, str]:
    sep = "&" if "?" in url else "?"
    req_url = f"{url}{sep}_src=daemon&_t={int(time.time()*1000)}"
    req = urllib.request.Request(
        req_url,
        headers={
            "User-Agent": "Blitz-Tailscale-KeepAlive-Daemon/1.0",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
            elapsed = (time.time() - start) * 1000
            return True, f"HTTP {resp.status} ({elapsed:.0f}ms) - {body}"
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        return False, f"FAILED ({elapsed:.0f}ms) - {e}"


def main():
    parser = argparse.ArgumentParser(description="Keep Blitz Tailscale Node awake.")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Health URL (default: {DEFAULT_URL})")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help=f"Ping interval (default: {DEFAULT_INTERVAL}s)")
    parser.add_argument("--once", action="store_true", help="Send a single ping and exit")
    parser.add_argument("--startup-delay", type=int, default=8, help="Initial delay in seconds (default: 8s)")
    args = parser.parse_args()

    if args.once:
        ok, detail = ping_once(args.url)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] KeepAlive -> {args.url} | {detail}")
        raise SystemExit(0 if ok else 1)

    print(f"[KeepAlive] Tailscale Node daemon started: target={args.url} interval={args.interval}s (initial delay {args.startup_delay}s)...", flush=True)
    if args.startup_delay > 0:
        time.sleep(args.startup_delay)
    while True:
        ok, detail = ping_once(args.url)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] {'OK' if ok else 'ERR'} | {detail}", flush=True)
        # Fast retry in 15 seconds if failed, otherwise wait the normal interval
        sleep_time = args.interval if ok else 15
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
