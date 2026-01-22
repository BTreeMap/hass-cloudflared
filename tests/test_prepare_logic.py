import json
import subprocess
import tempfile
from pathlib import Path
from string import Template

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

BASHIO_REF = "9b30bab926bdba7b9fc0e0f2d2871ef14e17e8d6"  # Update when bashio changes


def _ensure_bashio_lib(tmp_path):
    bashio_dir = tmp_path / "bashio"
    if not bashio_dir.exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", "https://github.com/hassio-addons/bashio", str(bashio_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(bashio_dir), "checkout", BASHIO_REF],
            check=True,
            capture_output=True,
            text=True,
        )
    return bashio_dir / "lib"


def _run_setup(config_payload):
    tmp_root = Path(tempfile.mkdtemp())
    lib_dir = _ensure_bashio_lib(tmp_root)
    config_dir = tmp_root / "config"
    data_dir = tmp_root / "data"
    cache_dir = tmp_root / "cache"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    config_json = config_dir / "config.json"
    config_json.write_text(json.dumps(config_payload), encoding="utf-8")

    script = Template(
        r"""
set -euo pipefail
export CACHE_DIR="$cache_dir"
export LOG_LEVEL=0
export BASHIO_DIR="$bashio_dir"
source "$bashio_lib_dir/bashio.sh"
bashio::addon.config() { cat "$config_json"; }
DAL_ROOT_OVERRIDE="$dal_root"
export SUPERVISOR_API="http://127.0.0.1"
export SUPERVISOR_TOKEN=""
export LOG_FORMAT=""
export LOG_TIMESTAMP=""
bashio::cache.exists() { return 1; }
bashio::cache.set() { :; }
bashio::cache.get() { return 1; }
bashio::cache.flush_all() { :; }
bashio::api.supervisor() { return 1; }
source "$run_sh"
setupDigitalAssetLinks
"""
    ).substitute(
        cache_dir=cache_dir,
        bashio_dir=lib_dir.parent,
        bashio_lib_dir=lib_dir,
        config_json=config_json,
        dal_root=data_dir,
        run_sh=RUN_SH,
    )
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


def test_rejects_invalid_hostname():
    result, _ = _run_setup(
        {
            "digital_asset_links_sites": ["https://bad_host"],
            "external_hostname": "ha.example.com",
        }
    )
    assert result.returncode != 0


def test_rejects_non_numeric_port():
    result, _ = _run_setup(
        {
            "digital_asset_links_sites": ["https://example.com:abc"],
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


def test_empty_list_removes_output():
    result, data_dir = _run_setup(
        {
            "digital_asset_links_sites": [],
            "external_hostname": "ha.example.com",
        }
    )
    assert result.returncode == 0
    assert not (Path(data_dir) / "www").exists()
