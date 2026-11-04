FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    PORT=8000 \
    TAILSCALE_VERSION=1.78.1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    iproute2 \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Download and install official Tailscale static binary
RUN set -eux; \
    ARCH="$(uname -m)"; \
    case "$ARCH" in \
        x86_64) TS_ARCH="amd64" ;; \
        aarch64) TS_ARCH="arm64" ;; \
        *) TS_ARCH="amd64" ;; \
    esac; \
    curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_${TS_ARCH}.tgz" -o tailscale.tgz; \
    tar -xzf tailscale.tgz; \
    mv "tailscale_${TAILSCALE_VERSION}_${TS_ARCH}/tailscale" /usr/local/bin/; \
    mv "tailscale_${TAILSCALE_VERSION}_${TS_ARCH}/tailscaled" /usr/local/bin/; \
    rm -rf tailscale.tgz "tailscale_${TAILSCALE_VERSION}_${TS_ARCH}"; \
    tailscale version

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && mkdir -p /app/data

VOLUME ["/app/data"]
EXPOSE 8000 1080 8080

CMD ["/app/entrypoint.sh"]
