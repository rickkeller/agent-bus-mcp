"""Shared fixtures. Nothing here runs a model, and no test needs the internet."""

import os
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from shutil import which

import pytest


def _server_binary() -> Path | None:
    """The server's own binary, or any nats-server via env or PATH."""
    requested = os.environ.get("AGENT_BUS_NATS_SERVER_BIN")
    if requested:
        return Path(requested)
    found = which("nats-server")
    if found:
        return Path(found)
    server = Path("/home/app/.local/bin/nats-server")
    return server if server.exists() else None


@pytest.fixture()
def nats_url() -> Iterator[str]:
    """Start the local NATS server on one free loopback port, then stop it."""
    binary = _server_binary()
    if binary is None:
        if os.environ.get("AGENT_BUS_REQUIRE_NATS") == "1":
            pytest.fail("AGENT_BUS_REQUIRE_NATS is 1 and no nats-server binary was found.")
        pytest.skip("no nats-server binary; set AGENT_BUS_NATS_SERVER_BIN or install nats-server on PATH")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    process = subprocess.Popen(
        [str(binary), "-a", "127.0.0.1", "-p", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            if process.poll() is not None:
                raise AssertionError("the NATS server exited before accepting connections")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise AssertionError("the NATS server did not start")
                time.sleep(0.05)
        yield f"nats://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


nats_server = nats_url
