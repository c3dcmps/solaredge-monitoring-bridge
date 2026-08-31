"""Repository-level packaging and privacy checks."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "solaredge_one_bridge"
SKIP_DIRECTORIES = {".git", ".pytest_cache", ".ruff_cache", ".venv", "__pycache__"}


def test_manifest_is_hacs_shaped_and_installable() -> None:
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["domain"] == "solaredge_one_bridge"
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["requirements"] == []
    assert manifest["version"] == "0.1.0"
    assert manifest["codeowners"]


def test_strings_and_english_translation_are_identical() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    translation = json.loads(
        (COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")
    )
    assert strings == translation


def test_expected_hacs_files_and_original_brand_exist() -> None:
    for relative in (
        "hacs.json",
        "README.md",
        "LICENSE",
        ".github/workflows/validate.yml",
        "assets/bridge-mark.svg",
        "custom_components/solaredge_one_bridge/brand/icon.svg",
        "custom_components/solaredge_one_bridge/brand/icon.png",
        "custom_components/solaredge_one_bridge/brand/icon@2x.png",
    ):
        assert (ROOT / relative).is_file(), relative


def test_no_environment_specific_literals_or_private_keys() -> None:
    forbidden_literals = (
        "ino" + "uye",
        "ha-chatgpt" + "-mcp",
        "homeassistant" + "-cloud",
        "my" + "sce",
        "10.44" + ".0.1",
    )
    private_key_header = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
    for path in ROOT.rglob("*"):
        if not path.is_file() or SKIP_DIRECTORIES.intersection(path.parts):
            continue
        if path.suffix.lower() in {".png", ".pyc"}:
            continue
        text = path.read_text(encoding="utf-8").casefold()
        assert not any(value in text for value in forbidden_literals), path
        assert not private_key_header.search(text), path


def test_portal_implementation_is_explicitly_fixed_and_bridge_is_retained() -> None:
    portal = (COMPONENT / "portal.py").read_text(encoding="utf-8")
    config_flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert '_LOGIN_HOST = "login.solaredge.com"' in portal
    assert '_MONITORING_HOST = "monitoring.solaredge.com"' in portal
    assert (
        '_DASHBOARD_BASE_URL = "https://monitoring.solaredge.com/services/dashboard"'
        in portal
    )
    assert "def request(" not in portal
    assert "MODE_PORTAL" in config_flow
    assert "MODE_BRIDGE" in config_flow
    assert "BRIDGE_SECRET_HEADER" in (COMPONENT / "const.py").read_text(
        encoding="utf-8"
    )
