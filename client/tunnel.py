#!/usr/bin/env python3
from __future__ import annotations
"""
Blitz High-Speed WebSocket SOCKS5 Tunnel Client (Zero Dependencies)
通过 Cloudflare Anycast CDN 与 Blitz 德国出口建立极速 WebSocket 隧道，
彻底绕过跨国劣化的 Tailscale DERP 中继，跑满真实带宽。
"""
import argparse
import asyncio
import base64
import os
import secrets
import socket
import ssl
import struct
import sys
import time
from typing import Optional, Tuple
import urllib.parse

DEFAULT_SERVER = "wss://ts-exit.blitz48.blitz.cloud/tunnel"
DEFAULT_TOKEN = "snh48ssr"
DEFAULT_PORT = 1080


def create_ws_client_handshake(host: str, port: int, path: str, token: str) -> Tuple[bytes, str]:
    sec_key = base64.b64encode(secrets.token_bytes(16)).decode()
    sep = "&" if "?" in path else "?"
    full_path = f"{path}{sep}token={urllib.parse.quote(token)}"
    headers = [
        f"GET {full_path} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {sec_key}",
        "Sec-WebSocket-Version: 13",
        "User-Agent: BlitzTunnelClient/1.0",
        "",
        "",
    ]
    return "\r\n".join(headers).encode("latin1"), sec_key


async def ws_connect(url: str, token: str) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    p = urllib.parse.urlparse(url)
    is_ssl = p.scheme in ("wss", "https")
    host = p.hostname or "localhost"
    port = p.port or (443 if is_ssl else 80)
    path = p.path or "/"

    ssl_ctx = ssl.create_default_context() if is_ssl else None
    reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)

    handshake, sec_key = create_ws_client_handshake(host, port, path, token)
    writer.write(handshake)
    await writer.drain()

    # Read HTTP 101 Switching Protocols response
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = await reader.read(1024)
        if not chunk:
            raise ConnectionError("Server closed connection during WebSocket handshake")
        resp += chunk

    status_line = resp.split(b"\r\n", 1)[0].decode("latin1", "ignore")
    if "101" not in status_line:
        raise ConnectionError(f"WebSocket handshake rejected: {status_line}")

    return reader, writer


def encode_ws_frame(data: bytes, opcode: int = 2) -> bytes:
    # Opcode 2 = binary frame, mask bit = 1 for client
    header = bytearray([0x80 | (opcode & 0x0F)])
    length = len(data)
    mask = secrets.token_bytes(4)

    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))

    header.extend(mask)
    # Mask payload
    masked_data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return bytes(header) + masked_data


async def read_ws_frame(reader: asyncio.StreamReader) -> Optional[bytes]:
    try:
        h = await reader.readexactly(2)
    except (asyncio.IncompleteReadError, ConnectionResetError):
        return None

    opcode = h[0] & 0x0F
    is_masked = bool(h[1] & 0x80)
    payload_len = h[1] & 0x7F

    if opcode == 8:  # Close frame
        return None

    if payload_len == 126:
        ext = await reader.readexactly(2)
        payload_len = struct.unpack("!H", ext)[0]
    elif payload_len == 127:
        ext = await reader.readexactly(8)
        payload_len = struct.unpack("!Q", ext)[0]

    mask = await reader.readexactly(4) if is_masked else None
    payload = await reader.readexactly(payload_len)
    if is_masked and mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return payload


async def bridge_connection(
    local_reader: asyncio.StreamReader,
    local_writer: asyncio.StreamWriter,
    server_url: str,
    token: str,
    conn_id: int,
):
    try:
        ws_reader, ws_writer = await ws_connect(server_url, token)
    except Exception as e:
        print(f"[{conn_id}] 隧道连接失败: {e}", file=sys.stderr)
        local_writer.close()
        return

    async def local_to_ws():
        try:
            while True:
                data = await local_reader.read(65536)
                if not data:
                    break
                ws_writer.write(encode_ws_frame(data, opcode=2))
                await ws_writer.drain()
        except Exception:
            pass
        finally:
            try:
                ws_writer.write(encode_ws_frame(b"", opcode=8))  # send close
                await ws_writer.drain()
                ws_writer.close()
            except Exception:
                pass

    async def ws_to_local():
        try:
            while True:
                data = await read_ws_frame(ws_reader)
                if data is None:
                    break
                local_writer.write(data)
                await local_writer.drain()
        except Exception:
            pass
        finally:
            try:
                local_writer.close()
            except Exception:
                pass

    t1 = asyncio.create_task(local_to_ws())
    t2 = asyncio.create_task(ws_to_local())
    await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    t1.cancel()
    t2.cancel()


async def main_async(args):
    conn_count = 0

    async def handle_client(reader, writer):
        nonlocal conn_count
        conn_count += 1
        cid = conn_count
        await bridge_connection(reader, writer, args.server, args.token, cid)

    server = await asyncio.start_server(handle_client, args.bind, args.port)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets)
    print("=" * 65)
    print(f"  Blitz 极速公网 CDN 隧道客户端已就绪！")
    print(f"  本地 SOCKS5 代理地址: socks5h://127.0.0.1:{args.port}")
    print(f"  远端加速端点        : {args.server}")
    print(f"  网络通道            : Cloudflare Anycast CDN ➔ 德国 Hetzner 出口")
    print("=" * 65)
    print(f"现在可直接在另一个终端测试:")
    print(f'  curl.exe -x socks5h://127.0.0.1:{args.port} https://ipinfo.io/json')
    print(f'  curl.exe -x socks5h://127.0.0.1:{args.port} https://speed.cloudflare.com/__down?bytes=26214400 -o NUL')
    print("=" * 65 + "\n")

    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Blitz High-Speed WebSocket Tunnel Client")
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"WebSocket endpoint (default: {DEFAULT_SERVER})")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help=f"Security token (default: {DEFAULT_TOKEN})")
    parser.add_argument("--bind", default="127.0.0.1", help="Local bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Local SOCKS5 port (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n已退出隧道。")


if __name__ == "__main__":
    main()
