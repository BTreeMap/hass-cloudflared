import pathlib

import yaml


def _load_config():
    config_path = pathlib.Path(__file__).resolve().parents[1] / "cloudflared" / "config.yaml"
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def _collect_schema_paths(schema, prefix=""):
    keys = []
    for key, value in schema.items():
        if isinstance(value, dict):
            keys.extend(_collect_schema_paths(value, f"{prefix}{key}."))
        else:
            keys.append(f"{prefix}{key}")
    return keys


def test_all_options_have_schema_entries():
    config = _load_config()
    options = set(config["options"].keys())
    schema = config["schema"]
    schema_keys = {entry.split(".", 1)[0] for entry in _collect_schema_paths(schema)}
    missing = options - schema_keys
    assert not missing, f"Missing schema entries for options: {sorted(missing)}"


def test_schema_contains_expected_options():
    config = _load_config()
    schema = config["schema"]
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
    assert expected.issubset(schema.keys())


def test_additional_hosts_schema_shape():
    config = _load_config()
    additional_hosts = config["schema"]["additional_hosts"]
    assert isinstance(additional_hosts, list)
    assert additional_hosts, "additional_hosts schema list should not be empty"
    host_schema = additional_hosts[0]
    assert host_schema["hostname"] == "str"
    assert host_schema["service"] == "str"
    assert host_schema["disableChunkedEncoding"] == "bool?"


def test_run_parameters_schema_pattern():
    config = _load_config()
    run_parameters = config["schema"]["run_parameters"]
    assert isinstance(run_parameters, list)
    assert run_parameters, "run_parameters schema list should not be empty"
    assert run_parameters[0].startswith("match(")


def test_digital_asset_links_schema_entries():
    config = _load_config()
    dal_schema = config["schema"]["digital_asset_links_sites"]
    assert isinstance(dal_schema, list)
    assert dal_schema == ["str"]
