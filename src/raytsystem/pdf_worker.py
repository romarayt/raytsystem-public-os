from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys

MAX_PAGES = 500
MAX_TEXT_CHARS = 10_000_000


class _NetworkDeniedSocket(socket.socket):
    def connect(self, _address: object) -> None:
        raise OSError("network disabled in PDF worker")

    def connect_ex(self, _address: object) -> int:
        raise OSError("network disabled in PDF worker")


def _deny_network(*_args: object, **_kwargs: object) -> None:
    raise OSError("network disabled in PDF worker")


def _deny_filesystem(*_args: object, **_kwargs: object) -> None:
    raise OSError("filesystem disabled during PDF parsing")


class _ProcessDenied:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise OSError("subprocess disabled during PDF parsing")


def main() -> int:
    raw = sys.stdin.buffer.read()
    try:
        socket.socket = _NetworkDeniedSocket  # type: ignore[misc]
        socket.create_connection = _deny_network  # type: ignore[assignment]
        socket.getaddrinfo = _deny_network  # type: ignore[assignment]
        from pypdf import PdfReader

        # Dependencies are imported before these defense-in-depth guards. The parser
        # receives an in-memory stream and has no legitimate file/process requirement.
        io.open = _deny_filesystem  # type: ignore[assignment]
        os.open = _deny_filesystem  # type: ignore[assignment]
        os.system = _deny_filesystem  # type: ignore[assignment]
        subprocess.Popen = _ProcessDenied  # type: ignore[misc,assignment]

        reader = PdfReader(io.BytesIO(raw), strict=True, root_object_recovery_limit=10_000)
        if len(reader.pages) > MAX_PAGES:
            raise ValueError("page_limit")
        pages: list[str] = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            total += len(text)
            if total > MAX_TEXT_CHARS:
                raise ValueError("text_limit")
            pages.append(text)
        reader.close()
    except Exception:
        sys.stdout.write('{"error":"pdf_parse_failed"}\n')
        return 2
    sys.stdout.write(json.dumps({"pages": pages}, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
