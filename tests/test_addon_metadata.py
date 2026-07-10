from pathlib import Path

import pytest

from scripts.addon_metadata import AddonMetadata, extract_metadata, write_github_outputs


def test_extract_metadata_normalizes_workflow_values() -> None:
    metadata = extract_metadata(
        {
            "arch": ["aarch64", "amd64"],
            "description": "Cloudflare Tunnel",
            "name": "Cloudflared",
            "slug": "cloudflared",
        },
        target="cloudflared",
    )

    assert metadata == AddonMetadata(
        architectures=("aarch64", "amd64"),
        description="Cloudflare Tunnel",
        name="Cloudflared",
        slug="cloudflared",
        target="cloudflared",
    )
    assert metadata.github_outputs() == {
        "architectures": '["aarch64","amd64"]',
        "description": "Cloudflare Tunnel",
        "name": "Cloudflared",
        "slug": "cloudflared",
        "target": "cloudflared",
    }


def test_extract_metadata_applies_slug_override() -> None:
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

    assert metadata.slug == "override"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (None, "config must be a mapping"),
        ({}, "config arch must be a non-empty list"),
        (
            {"arch": [], "description": "D", "name": "N", "slug": "s"},
            "config arch must be a non-empty list",
        ),
        (
            {"arch": [1], "description": "D", "name": "N", "slug": "s"},
            "every config architecture must be a string",
        ),
        (
            {"arch": ["amd64"], "description": "", "name": "N", "slug": "s"},
            "config description must be a non-empty string",
        ),
    ],
)
def test_extract_metadata_rejects_invalid_config(config: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        extract_metadata(config, target="app")


def test_write_github_outputs_appends_deterministically(tmp_path: Path) -> None:
    output_path = tmp_path / "github-output"

    write_github_outputs({"name": "Cloudflared", "slug": "cloudflared"}, output_path)

    assert output_path.read_text(encoding="utf-8") == (
        "name=Cloudflared\nslug=cloudflared\n"
    )


def test_write_github_outputs_rejects_multiline_values(tmp_path: Path) -> None:
    output_path = tmp_path / "github-output"

    with pytest.raises(ValueError, match="must be a single line"):
        write_github_outputs({"description": "first\nsecond"}, output_path)
    assert not output_path.exists()
