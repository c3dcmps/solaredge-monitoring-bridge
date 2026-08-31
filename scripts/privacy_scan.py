"""Fail closed on known private-environment markers and key material."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE)
WINDOWS_USER_PATH = re.compile(r"[A-Z]:\\Users\\[^\\\s]+", re.IGNORECASE)
PRIVATE_IPV4 = re.compile(
    r"\b(?:10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)"
    r"\d{1,3}(?:\.\d{1,3}){2}\b"
)
DOCUMENTED_NETWORK_BASES = {"10.0.0.0"}
SKIP_SUFFIXES = {".png", ".pyc"}
SKIP_DIRECTORIES = {".git", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__"}


def main() -> int:
    """Scan text files without printing their contents."""
    findings: list[str] = []
    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or SKIP_DIRECTORIES.intersection(path.parts)
        ):
            continue
        if path.suffix.casefold() in SKIP_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if (
            PRIVATE_KEY.search(text)
            or WINDOWS_USER_PATH.search(text)
            or any(
                address not in DOCUMENTED_NETWORK_BASES
                for address in PRIVATE_IPV4.findall(text)
            )
        ):
            findings.append(str(path.relative_to(ROOT)))
    if findings:
        print("Privacy scan failed in: " + ", ".join(sorted(findings)))
        return 1
    print("Privacy scan passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
