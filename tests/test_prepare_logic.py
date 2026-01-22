import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
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

BASHIO_REF = "eb2f6f0f90c0a3e4a8d98d2ac0f2b1a1a8aa6d7d"
BASHIO_TARBALL = f"https://github.com/hassio-addons/bashio/archive/{BASHIO_REF}.tar.gz"


def _ensure_bashio_lib():
    cache_root = Path(tempfile.gettempdir()) / "bashio-test-cache"
    lib_dir = cache_root / "bashio-main" / "lib"
    if lib_dir.exists():
        return lib_dir
    if cache_root.exists():
        shutil.rmtree(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    tar_path = cache_root / "bashio.tar.gz"
    with urllib.request.urlopen(BASHIO_TARBALL) as response, tar_path.open("wb") as handle:
        handle.write(response.read())
    with tarfile.open(tar_path) as tar:
        tar.extractall(cache_root, filter="data")
    return lib_dir


def _run_setup(config_payload):
    lib_dir = _ensure_bashio_lib()
    config_dir = Path(tempfile.mkdtemp())
    data_dir = Path(tempfile.mkdtemp())
    cache_dir = Path(tempfile.mkdtemp())
    config_json = config_dir / "config.json"
    config_json.write_text(json.dumps(config_payload), encoding="utf-8")

    script = Template(
        r"""
set -euo pipefail
export CACHE_DIR="$cache_dir"
export LOG_LEVEL=0
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
