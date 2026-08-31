"""Small offline manifest gate; hassfest remains authoritative in CI."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

MANIFEST = (
    Path(__file__).parents[1]
    / "custom_components"
    / "solaredge_one_bridge"
    / "manifest.json"
)
REQUIRED = {
    "domain",
    "name",
    "codeowners",
    "config_flow",
    "documentation",
    "integration_type",
    "iot_class",
    "issue_tracker",
    "requirements",
    "version",
}


def main() -> int:
    """Validate structural fields without importing Home Assistant."""
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    missing = REQUIRED - data.keys()
    if missing:
        raise ValueError(f"manifest missing fields: {sorted(missing)}")
    if data["domain"] != MANIFEST.parent.name:
        raise ValueError("manifest domain does not match component directory")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", data["version"]):
        raise ValueError("manifest version is not SemVer-shaped")
    if data["iot_class"] != "local_polling":
        raise ValueError("bridge integration must remain local_polling")
    print("Manifest validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

