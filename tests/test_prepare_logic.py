import json
import subprocess
import tempfile
from pathlib import Path

import yaml

RUN_SH = (
    Path(__file__).resolve().parents[1]
    / "cloudflared"
    / "rootfs"
    / "etc"
    / "s6-overlay"
    / "s6-rc.d"
    / "prepare"
    / "run.sh"
)


def _render_config(path, payload):
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _run_setup(config_payload):
    config_dir = Path(tempfile.mkdtemp())
    data_dir = Path(tempfile.mkdtemp())
    config_path = config_dir / "config.yaml"
    _render_config(config_path, config_payload)

    script = r"""
set -euo pipefail
__BASHIO_EXIT_OK=0
__BASHIO_EXIT_NOK=1

DAL_ROOT_OVERRIDE="{dal_root}"

bashio::config.exists() {
  return 1
}

bashio::config.has_value() {
  python - "$@" <<'PY'
import sys, yaml
config = yaml.safe_load(open("{config_path}", "r", encoding="utf-8"))
key = sys.argv[1]
value = config.get(key)
if value is None or value == "":
    raise SystemExit(1)
if isinstance(value, list) and not value:
    raise SystemExit(1)
raise SystemExit(0)
PY
}

bashio::config.is_empty() {
  python - "$@" <<'PY'
import sys, yaml
config = yaml.safe_load(open("{config_path}", "r", encoding="utf-8"))
key = sys.argv[1]
value = config.get(key)
if value is None or value == "":
    raise SystemExit(0)
if isinstance(value, list) and not value:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

bashio::config.true() {
  python - "$@" <<'PY'
import sys, yaml
config = yaml.safe_load(open("{config_path}", "r", encoding="utf-8"))
key = sys.argv[1]
raise SystemExit(0 if config.get(key) is True else 1)
PY
}

bashio::addon.config() { echo "{config_path}"; }

bashio::jq() {
  python - "$@" <<'PY'
import json, sys, yaml
source = sys.argv[1]
query = sys.argv[2]
if source.endswith(".json"):
    data = json.load(open(source, "r", encoding="utf-8"))
elif source.endswith(".yaml"):
    data = yaml.safe_load(open(source, "r", encoding="utf-8"))
else:
    data = json.loads(source)

def print_json(value):
    print(json.dumps(value))

if query == ".digital_asset_links_sites[]?":
    for entry in data.get("digital_asset_links_sites", []) or []:
        print(entry)
    raise SystemExit(0)

if query == ".":
    print_json(data)
    raise SystemExit(0)

if ". +=" in query:
    if "relation" in query and "target" in query:
        site = query.split('"site": "')[1].split('"', 1)[0]
        entry = {
            "relation": ["delegate_permission/common.get_login_creds"],
            "target": {"namespace": "web", "site": site},
        }
        if not isinstance(data, list):
            data = []
        data.append(entry)
        print_json(data)
        raise SystemExit(0)

if " + " in query:
    _, addition = query.split(" + ", 1)
    add_value = json.loads(addition)
    if not isinstance(data, list):
        data = []
    data.extend(add_value)
    print_json(data)
    raise SystemExit(0)

print_json(data)
PY
}

bashio::var.is_empty() { [[ -z "$1" ]]; }
bashio::var.has_value() { [[ -n "$1" ]]; }
bashio::var.true() { [[ "$1" == "true" ]]; }
bashio::var.false() { [[ "$1" == "false" ]]; }

bashio::fs.file_exists() { [[ -f "$1" ]]; }
bashio::log.trace() { :; }
bashio::log.info() { :; }
bashio::log.debug() { :; }
bashio::log.notice() { :; }
bashio::log.warning() { :; }
bashio::log.error() { :; }
bashio::exit.nok() { exit 1; }
bashio::exit.ok() { exit 0; }

source "{run_sh}"
setupDigitalAssetLinks
"""
    script = script.replace("{config_path}", str(config_path))
    script = script.replace("{dal_root}", str(data_dir))
    script = script.replace("{run_sh}", str(RUN_SH))
    result = subprocess.run(
        script, shell=True, text=True, capture_output=True, executable="/bin/bash"
    )
    return result, data_dir


def _read_assetlinks(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_rejects_non_https():
    result, _ = _run_setup(
        {
            "digital_asset_links_sites": ["http://example.com"],
            "external_hostname": "ha.example.com",
        }
    )
    assert result.returncode != 0


def test_rejects_path():
    result, _ = _run_setup(
        {
            "digital_asset_links_sites": ["https://example.com/path"],
            "external_hostname": "ha.example.com",
        }
    )
    assert result.returncode != 0


def test_rejects_port_out_of_range():
    result, _ = _run_setup(
        {
            "digital_asset_links_sites": ["https://example.com:99999"],
            "external_hostname": "ha.example.com",
        }
    )
    assert result.returncode != 0


def test_dedupes_and_sorts():
    result, data_dir = _run_setup(
        {
            "digital_asset_links_sites": [
                "https://b.example.com",
                "https://a.example.com",
                "https://a.example.com",
            ],
            "external_hostname": "ha.example.com",
        }
    )
    assert result.returncode == 0
    assetlinks = Path(data_dir) / "www" / ".well-known" / "assetlinks.json"
    data = _read_assetlinks(assetlinks)
    sites = [entry["target"]["site"] for entry in data]
    assert sites == ["https://a.example.com", "https://b.example.com"]


def test_accepts_valid_port():
    result, data_dir = _run_setup(
        {
            "digital_asset_links_sites": ["https://example.com:8443"],
            "external_hostname": "ha.example.com",
        }
    )
    assert result.returncode == 0
    assetlinks = Path(data_dir) / "www" / ".well-known" / "assetlinks.json"
    data = _read_assetlinks(assetlinks)
    assert data[0]["target"]["site"] == "https://example.com:8443"
