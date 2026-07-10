"""Validate app configuration and expose immutable build metadata."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class AddonMetadata:
    """Validated metadata consumed by build workflows."""

    architectures: tuple[str, ...]
    description: str
    name: str
    slug: str
    target: str

    def github_outputs(self) -> dict[str, str]:
        """Serialize metadata into deterministic, single-line values."""
        return {
            "architectures": json.dumps(self.architectures, separators=(",", ":")),
            "description": self.description,
            "name": self.name,
            "slug": self.slug,
            "target": self.target,
        }


def _required_text(config: Mapping[object, object], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"config {key} must be a non-empty string")
    return value


def extract_metadata(
    raw_config: object, *, target: str, slug_override: str | None = None
) -> AddonMetadata:
    """Validate an untyped YAML value and construct immutable metadata."""
    if not isinstance(raw_config, Mapping):
        raise ValueError("config must be a mapping")

    raw_architectures = raw_config.get("arch")
    if not isinstance(raw_architectures, list) or not raw_architectures:
        raise ValueError("config arch must be a non-empty list")
    if not all(isinstance(architecture, str) for architecture in raw_architectures):
        raise ValueError("every config architecture must be a string")

    return AddonMetadata(
        architectures=tuple(raw_architectures),
        description=_required_text(raw_config, "description"),
        name=_required_text(raw_config, "name"),
        slug=slug_override or _required_text(raw_config, "slug"),
        target=target,
    )


def load_metadata(
    config_path: Path, *, slug_override: str | None = None
) -> AddonMetadata:
    """Load and validate metadata from a YAML configuration file."""
    raw_config: object = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return extract_metadata(
        raw_config,
        target=config_path.parent.as_posix(),
        slug_override=slug_override,
    )


def write_github_outputs(values: Mapping[str, str], output_path: Path) -> None:
    """Append single-line values using GitHub Actions' output-file protocol."""
    invalid_key = next((key for key, value in values.items() if "\n" in value), None)
    if invalid_key is not None:
        raise ValueError(f"metadata value {invalid_key} must be a single line")

    with output_path.open("a", encoding="utf-8") as output:
        output.writelines(f"{key}={value}\n" for key, value in values.items())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="path to the app config.yaml")
    return parser.parse_args()


def main() -> None:
    """Translate process inputs and outputs at the CLI boundary."""
    args = _parse_args()
    try:
        metadata = load_metadata(
            args.config,
            slug_override=os.environ.get("SLUG_OVERRIDE"),
        )
        values = metadata.github_outputs()
        if output_path := os.environ.get("GITHUB_OUTPUT"):
            write_github_outputs(values, Path(output_path))
        else:
            print(json.dumps(values, indent=2, sort_keys=True))
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
