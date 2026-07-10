from collections.abc import Mapping
from typing import cast

from tests.conftest import PROJECT_ROOT, YamlMapping, load_yaml_mapping

TRANSLATIONS_DIR = PROJECT_ROOT / "cloudflared" / "translations"


def _mapping_at(mapping: Mapping[str, object], key: str) -> YamlMapping:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise AssertionError(f"{key} must be a mapping")
    return cast(Mapping[str, object], value)


def test_only_english_translation_present() -> None:
    files = sorted(TRANSLATIONS_DIR.glob("*.yaml"))

    assert [file.name for file in files] == ["en.yaml"]


def test_translation_has_all_schema_keys(addon_config: YamlMapping) -> None:
    translation = load_yaml_mapping(TRANSLATIONS_DIR / "en.yaml")
    schema_keys = frozenset(_mapping_at(addon_config, "schema"))
    translation_keys = frozenset(_mapping_at(translation, "configuration"))
    missing = schema_keys - translation_keys

    assert not missing, f"Missing translation entries for: {sorted(missing)}"
