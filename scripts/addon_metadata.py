"""Validate app configuration and expose build metadata to GitHub Actions."""

from __future__ import annotations

import json
import os
import pathlib
import sys
from collections.abc import Mapping
from typing import Any

import yaml


def extract_metadata(
    config: Any, *, target: str, slug_override: str | None = None
) -> dict[str, str]:
    """Validate an app config and return normalized workflow metadata."""
    if not isinstance(config, Mapping):
        raise ValueError("config must be a mapping")
    required = {"arch", "description", "name", "slug"}
    missing = required - config.keys()
    if missing:
        raise ValueError(f"missing required config keys: {', '.join(sorted(missing))}")

    architectures = config["arch"]
    if not isinstance(architectures, list) or not architectures:
        raise ValueError("config arch must be a non-empty list")
    if not all(isinstance(architecture, str) for architecture in architectures):
        raise ValueError("every config architecture must be a string")

    return {
        "architectures": json.dumps(architectures, separators=(",", ":")),
        "description": str(config["description"]),
        "name": str(config["name"]),
        "slug": slug_override or str(config["slug"]),
        "target": target,
    }


def write_github_outputs(values: Mapping[str, str], output_path: pathlib.Path) -> None:
    """Append single-line values using GitHub Actions' output-file protocol."""
    with output_path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            if "\n" in value:
                raise ValueError(f"metadata value {key} must be a single line")
            print(f"{key}={value}", file=output)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: addon_metadata.py PATH_TO_CONFIG")

    config_path = pathlib.Path(sys.argv[1])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    try:
        values = extract_metadata(
            config,
            target=config_path.parent.as_posix(),
            slug_override=os.environ.get("SLUG_OVERRIDE"),
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        try:
            write_github_outputs(values, pathlib.Path(output_path))
        except ValueError as error:
            raise SystemExit(str(error)) from error
    else:
        print(json.dumps(values, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
