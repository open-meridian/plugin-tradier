"""The version's declaration as the upload reads it (contract v25): printed
from the plugin's root, as `meridian-declaration` prints it in the built
image, it carries the interface -- its pages and the tools their routes
make -- and each section of docs/ by its digest and size; and the texts the
upload sends beside it are the files, byte for byte. A version registers
exactly this, so the port is fixed, never read from the environment.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from meridian.declaration import main

from tradier import declaration as version

ROOT = Path(__file__).resolve().parents[1]
NAMED = "tradier.declaration:DECLARATION"
SECTIONS = ["overview", "setup", "troubleshooting", "terms", "pages"]


def printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *flags: str
) -> object:
    monkeypatch.chdir(ROOT)
    assert main([*flags, NAMED]) == 0
    return json.loads(capsys.readouterr().out)


def test_the_declaration_carries_its_pages_tools_and_documentation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    declared = printed(monkeypatch, capsys)
    assert isinstance(declared, dict)
    assert [page["title"] for page in declared["interface"]["pages"]] == [
        "Connection",
        "Datasets",
        "Account links",
        "Statements",
        "History",
        "Raw records",
    ]
    # One Connection for data and connectivity (457): no Trading page, and
    # Enable trading made on Connection.
    tools = {tool["name"] for tool in declared["tools"]}
    assert "read_trading" not in tools
    assert {"read_connection", "enable_trading", "check_enable_trading"} <= tools
    sections = declared["documentation"]
    assert [section["name"] for section in sections] == SECTIONS
    for section in sections:
        held = (ROOT / "docs" / f"{section['name']}.md").read_bytes()
        assert section["digest"] == f"sha256:{hashlib.sha256(held).hexdigest()}"
        assert section["bytes"] == len(held)


def test_the_texts_uploaded_are_the_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    texts = printed(monkeypatch, capsys, "--documentation")
    assert texts == [
        {"section": name, "markdown": (ROOT / "docs" / f"{name}.md").read_text("utf-8")}
        for name in SECTIONS
    ]


def test_the_port_is_fixed() -> None:
    interface = version.DECLARATION.interface
    assert interface is not None
    assert interface.port == version.PORT == 8000


def test_each_datasets_terms_and_gap_recovery() -> None:
    # Contract 30 (ruling 1032): Tradier's terms in words, never a licence,
    # OPRA's first on the chains; how a gap in each is recovered.
    from tradier.declared import CATALOGUE

    by_key = {d.key: d for d in CATALOGUE}
    for dataset in CATALOGUE:
        assert "personal use only" in dataset.terms_and_pricing
        assert len(dataset.terms_and_pricing) <= 2000 and not dataset.sessions_calendar_id
        assert dataset.terms_and_pricing.startswith("US options") == dataset.key.startswith(
            "options"
        )
    assert {k: d.gap_recovery for k, d in by_key.items()} == {
        "daily": "backfill",
        "live": "stream_only",
        "live_delayed": "none",
        "options": "none",
        "options_delayed": "none",
        "trades": "stream_only",
    }
