import json
from pathlib import Path

import pytest

from scripts.e2e_home_assistant import (
    addon_options,
    assetlink_sites,
    write_json,
)


def test_addon_options_constructs_independent_serializable_values() -> None:
    sites = ["https://ha.example.com"]

    options = addon_options(sites=sites, post_quantum=True)
    sites.append("https://later.example.com")

    assert options["digital_asset_links_sites"] == ["https://ha.example.com"]
    assert options["post_quantum"] is True
    json.dumps(options)


def test_assetlink_sites_decodes_validated_sites() -> None:
    document: object = [
        {"target": {"site": "https://ha.example.com"}},
        {"target": {"site": "https://vault.example.com:8443"}},
    ]

    assert assetlink_sites(document) == [
        "https://ha.example.com",
        "https://vault.example.com:8443",
    ]


@pytest.mark.parametrize(
    "document",
    [None, {}, [{}], [{"target": None}], [{"target": {"site": 42}}]],
)
def test_assetlink_sites_rejects_malformed_documents(document: object) -> None:
    with pytest.raises(AssertionError):
        assetlink_sites(document)


def test_write_json_replaces_document_deterministically(tmp_path: Path) -> None:
    destination = tmp_path / "options.json"

    write_json(destination, {"z": 1, "a": True})

    assert destination.read_text(encoding="utf-8") == ('{\n  "a": true,\n  "z": 1\n}\n')
    assert not destination.with_suffix(".json.tmp").exists()
