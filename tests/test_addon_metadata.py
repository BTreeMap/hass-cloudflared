import json

import pytest

from scripts.addon_metadata import extract_metadata, write_github_outputs


def test_extract_metadata_normalizes_workflow_values():
    metadata = extract_metadata(
        {
            "arch": ["aarch64", "amd64"],
            "description": "Cloudflare Tunnel",
            "name": "Cloudflared",
            "slug": "cloudflared",
        },
        target="cloudflared",
    )

    assert json.loads(metadata["architectures"]) == ["aarch64", "amd64"]
    assert metadata == {
        "architectures": '["aarch64","amd64"]',
        "description": "Cloudflare Tunnel",
        "name": "Cloudflared",
        "slug": "cloudflared",
        "target": "cloudflared",
    }


def test_extract_metadata_applies_slug_override():
    metadata = extract_metadata(
        {
            "arch": ["amd64"],
            "description": "Description",
            "name": "Name",
            "slug": "original",
        },
        target="app",
        slug_override="override",
    )

    assert metadata["slug"] == "override"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (None, "config must be a mapping"),
        ({}, "missing required config keys"),
        (
            {"arch": [], "description": "D", "name": "N", "slug": "s"},
            "config arch must be a non-empty list",
        ),
        (
            {"arch": [1], "description": "D", "name": "N", "slug": "s"},
            "every config architecture must be a string",
        ),
    ],
)
def test_extract_metadata_rejects_invalid_config(config, message):
    with pytest.raises(ValueError, match=message):
        extract_metadata(config, target="app")


def test_write_github_outputs_rejects_multiline_values(tmp_path):
    output_path = tmp_path / "github-output"

    with pytest.raises(ValueError, match="must be a single line"):
        write_github_outputs({"description": "first\nsecond"}, output_path)
