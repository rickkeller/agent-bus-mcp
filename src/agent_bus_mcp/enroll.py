"""Local enrollment for the TLS host. This is not a remote or MCP tool."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .queue import QueueRefused, _caller_key
from .remote import certificate_sha256, load_enrollment


def _read(path: Path) -> dict:
    if not path.exists():
        return {"credentials": []}
    try:
        load_enrollment(path)
    except QueueRefused:
        raise SystemExit("enrollment file is invalid")
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, document: dict) -> None:
    """Replace the file only when the new document validates. A bad file is left untouched."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    encoded = json.dumps(document, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        load_enrollment(temporary)
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except QueueRefused:
        temporary.unlink(missing_ok=True)
        raise SystemExit("enrollment file is invalid")
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def add_certificate(path: Path, endpoint: str, certificate: Path) -> None:
    if not _caller_key(endpoint):
        raise SystemExit("enrollment file is invalid")
    fingerprint = certificate_sha256(certificate.read_text(encoding="utf-8")).lower()
    document = _read(path)
    for row in document["credentials"]:
        if row["sha256"] == fingerprint and row["endpoint"] != endpoint:
            raise SystemExit("enrollment file is invalid")
    rows = [row for row in document["credentials"] if row["endpoint"] != endpoint]
    rows.append({"endpoint": endpoint, "sha256": fingerprint})
    rows.sort(key=lambda row: row["endpoint"])
    _write(path, {"credentials": rows})


def revoke_endpoint(path: Path, endpoint: str) -> None:
    document = _read(path)
    rows = [row for row in document["credentials"] if row["endpoint"] != endpoint]
    if len(rows) == len(document["credentials"]):
        raise SystemExit("enrollment file is invalid")
    _write(path, {"credentials": rows})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Enroll or revoke one endpoint certificate on this machine.",
        epilog=(
            "An invalid enrollment file refuses every certificate. "
            "add and revoke leave an invalid file unchanged. "
            "Recovery is to move that file aside and add the certificates that should still be trusted. "
            "A certificate that is not added again stays revoked."
        ),
    )
    parser.add_argument("action", choices=("add", "revoke", "check"))
    parser.add_argument("--file", required=True)
    parser.add_argument("--endpoint")
    parser.add_argument("--cert")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    path = Path(args.file)
    if args.action == "check":
        try:
            enrolled = load_enrollment(path)
        except QueueRefused:
            raise SystemExit("enrollment file is invalid")
        print("enrollment valid: %s" % len(enrolled))
        return 0
    if not args.endpoint:
        raise SystemExit("required argument is missing")
    if args.action == "add":
        if not args.cert:
            raise SystemExit("required argument is missing")
        add_certificate(path, args.endpoint, Path(args.cert))
        return 0
    revoke_endpoint(path, args.endpoint)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
