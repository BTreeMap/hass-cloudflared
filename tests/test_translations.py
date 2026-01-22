import pathlib

import yaml


def test_only_english_translation_present():
    translations_dir = pathlib.Path(__file__).resolve().parents[1] / "cloudflared" / "translations"
    files = sorted(pathlib.Path(translations_dir).glob("*.yaml"))
    assert [file.name for file in files] == ["en.yaml"]


def test_translation_has_all_schema_keys():
    translations_dir = pathlib.Path(__file__).resolve().parents[1] / "cloudflared" / "translations"
    translation = yaml.safe_load((translations_dir / "en.yaml").read_text(encoding="utf-8"))
    config = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parents[1] / "cloudflared" / "config.yaml").read_text(
            encoding="utf-8"
        )
    )
    schema_keys = set(config["schema"].keys())
    translation_keys = set(translation["configuration"].keys())
    missing = schema_keys - translation_keys
    assert not missing, f"Missing translation entries for: {sorted(missing)}"
