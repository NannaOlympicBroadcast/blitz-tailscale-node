#!/usr/bin/env sh
set -e

mkdir -p /app/data/tailscale

echo "[Tailscale] Starting tailscaled in userspace-networking mode (SOCKS5: 0.0.0.0:1080, HTTP: 0.0.0.0:8080)..."
tailscaled \
  --tun=userspace-networking \
  --statedir=/app/data/tailscale \
  --port=41641 \
  --socks5-server=0.0.0.0:1080 \
  --outbound-http-proxy-listen=0.0.0.0:8080 &

# Wait for tailscaled socket to become ready
for i in $(seq 1 10); do
  if tailscale status >/dev/null 2>&1; then
    echo "[Tailscale] tailscaled is ready."
    break
  fi
  sleep 1
done

# Background worker to automatically register serve ports once Tailscale is connected
(
  for i in $(seq 1 60); do
    if tailscale status 2>/dev/null | grep -q "100\."; then
      echo "[Tailscale] Exposing SOCKS5 (1080) and HTTP (8080) to Tailnet via serve..."
      tailscale serve reset 2>/dev/null || true
      tailscale serve --bg --tcp=1080 tcp://127.0.0.1:1080 2>/dev/null || true
      tailscale serve --bg --tcp=8080 tcp://127.0.0.1:8080 2>/dev/null || true
      echo "[Tailscale] SOCKS5 and HTTP serve active on Tailnet!"
      break
    fi
    sleep 2
  done
) &

echo "[KeepAlive] Starting standalone keep-alive daemon..."
python /app/scripts/keepalive.py \
  --url "${KEEPALIVE_URL:-https://ts-exit.blitz48.blitz.cloud/api/health}" \
  --interval "${KEEPALIVE_INTERVAL:-180}" &

echo "[App] Starting Web & KeepAlive Server on 0.0.0.0:${PORT:-8000}..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
