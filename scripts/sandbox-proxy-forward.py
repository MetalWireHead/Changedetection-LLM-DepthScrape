#!/usr/bin/env python3
"""Test-environment-only: let the docker containers use the Claude Code sandbox's egress proxy.

The sandbox has no direct DNS/internet, only an HTTPS proxy on 127.0.0.1 (see /root/.ccr/README.md), which containers on the
docker bridge cannot reach. This forwards TCP from the bridge to that proxy, so the containers' traffic still goes through
the sandbox's policy-enforcing proxy. Usage: sandbox-proxy-forward.py [listen_port]  (proxy port is read from $HTTPS_PROXY)
"""
import asyncio
import os
import sys
from urllib.parse import urlsplit

UPSTREAM = urlsplit(os.environ['HTTPS_PROXY'])
LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 40640


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def handle(client_reader, client_writer):
    try:
        up_reader, up_writer = await asyncio.open_connection(UPSTREAM.hostname, UPSTREAM.port)
    except OSError:
        client_writer.close()
        return
    await asyncio.gather(pipe(client_reader, up_writer), pipe(up_reader, client_writer))


async def main():
    server = await asyncio.start_server(handle, '0.0.0.0', LISTEN_PORT)
    print(f'forwarding 0.0.0.0:{LISTEN_PORT} -> {UPSTREAM.hostname}:{UPSTREAM.port}', flush=True)
    async with server:
        await server.serve_forever()


asyncio.run(main())
