from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
type YamlMapping = Mapping[str, object]


def load_yaml_mapping(path: Path) -> YamlMapping:
    """Load a YAML document whose root is a string-keyed mapping."""
    document: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not all(
        isinstance(key, str) for key in document
    ):
        raise ValueError(f"{path} must contain a string-keyed mapping")
    return cast(dict[str, object], document)


@pytest.fixture(scope="session")
def addon_config() -> YamlMapping:
    """Provide the immutable-by-convention app configuration mapping."""
    return load_yaml_mapping(PROJECT_ROOT / "cloudflared" / "config.yaml")
