from collections.abc import Iterator, Mapping
from typing import cast

from conftest import YamlMapping


def _mapping_at(mapping: Mapping[str, object], key: str) -> YamlMapping:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        raise AssertionError(f"{key} must be a mapping")
    if not all(isinstance(child_key, str) for child_key in value):
        raise AssertionError(f"{key} must use string keys")
    return cast(Mapping[str, object], value)


def _schema_paths(schema: YamlMapping, prefix: str = "") -> Iterator[str]:
    """Lazily yield dotted paths for every leaf in a nested schema."""
    for key, value in schema.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            yield from _schema_paths(cast(Mapping[str, object], value), f"{path}.")
        else:
            yield path


def test_all_options_have_schema_entries(addon_config: YamlMapping) -> None:
    options = frozenset(_mapping_at(addon_config, "options"))
    schema = _mapping_at(addon_config, "schema")
    schema_keys = frozenset(path.partition(".")[0] for path in _schema_paths(schema))

    missing = options - schema_keys
    assert not missing, f"Missing schema entries for options: {sorted(missing)}"


def test_schema_contains_expected_options(addon_config: YamlMapping) -> None:
    schema = _mapping_at(addon_config, "schema")
    expected = {
        "external_hostname",
        "additional_hosts",
        "tunnel_name",
        "catch_all_service",
        "nginx_proxy_manager",
        "tunnel_token",
        "post_quantum",
        "run_parameters",
        "log_level",
        "digital_asset_links_sites",
    }

    assert expected <= schema.keys()


def test_additional_hosts_schema_shape(addon_config: YamlMapping) -> None:
    additional_hosts = _mapping_at(addon_config, "schema").get("additional_hosts")
    assert isinstance(additional_hosts, list) and additional_hosts
    host_schema = additional_hosts[0]
    assert isinstance(host_schema, Mapping)
    assert host_schema == {
        "hostname": "str",
        "service": "str",
        "disableChunkedEncoding": "bool?",
    }


def test_run_parameters_schema_pattern(addon_config: YamlMapping) -> None:
    run_parameters = _mapping_at(addon_config, "schema").get("run_parameters")
    assert isinstance(run_parameters, list) and run_parameters
    assert isinstance(run_parameters[0], str)
    assert run_parameters[0].startswith("match(")


def test_digital_asset_links_schema_entries(addon_config: YamlMapping) -> None:
    schema = _mapping_at(addon_config, "schema")
    assert schema.get("digital_asset_links_sites") == ["str"]
