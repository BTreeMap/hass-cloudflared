import json
import stat
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
        clone_result = subprocess.run(
            ["git", "clone", "https://github.com/hassio-addons/bashio", str(bashio_dir)],
            capture_output=True,
            text=True,
        )
        if clone_result.returncode != 0:
            raise RuntimeError(
                f"Failed to clone bashio: {clone_result.stdout}\n{clone_result.stderr}"
            )
        checkout_result = subprocess.run(
            ["git", "-C", str(bashio_dir), "checkout", BASHIO_REF],
            capture_output=True,
            text=True,
        )
        if checkout_result.returncode != 0:
            raise RuntimeError(
                f"Failed to checkout bashio ref: {checkout_result.stdout}\n{checkout_result.stderr}"
            )
    return bashio_dir / "lib"


def _run_setup(config_payload, log_level=0):
    log_level = int(log_level)
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
# Ensure predictable permissions when the script creates docroot files.
umask 022
set -euo pipefail
export CACHE_DIR="$cache_dir"
export LOG_LEVEL=$log_level
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
        log_level=log_level,
        run_sh=RUN_SH,
    )
    result = subprocess.run(
        script, shell=True, text=True, capture_output=True, executable="/bin/bash"
    )
    return result, data_dir


def _read_assetlinks(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_warns_non_https():
    result, data_dir = _run_setup(
        {
            "digital_asset_links_sites": ["http://example.com"],
            "external_hostname": "ha.example.com",
        },
        log_level=5,
    )
    assert result.returncode == 0
    assert (
        "'http://example.com' in 'digital_asset_links_sites' should start with 'https://'. Continuing with original value."
        in result.stdout
    )
    assetlinks = Path(data_dir) / "www" / ".well-known" / "assetlinks.json"
    data = _read_assetlinks(assetlinks)
    assert data[0]["target"]["site"] == "http://example.com"


def test_warns_path():
    result, data_dir = _run_setup(
        {
            "digital_asset_links_sites": ["https://example.com/path"],
            "external_hostname": "ha.example.com",
        },
        log_level=5,
    )
    assert result.returncode == 0
    assert (
        "'https://example.com/path' in 'digital_asset_links_sites' should be an HTTPS origin without a path. Continuing with original value."
        in result.stdout
    )
    assetlinks = Path(data_dir) / "www" / ".well-known" / "assetlinks.json"
    data = _read_assetlinks(assetlinks)
    assert data[0]["target"]["site"] == "https://example.com/path"


def test_warns_port_out_of_range():
    result, data_dir = _run_setup(
        {
            "digital_asset_links_sites": ["https://example.com:99999"],
            "external_hostname": "ha.example.com",
        },
        log_level=5,
    )
    assert result.returncode == 0
    assert (
        "'https://example.com:99999' in 'digital_asset_links_sites' includes an invalid port. Continuing with original value."
        in result.stdout
    )
    assetlinks = Path(data_dir) / "www" / ".well-known" / "assetlinks.json"
    data = _read_assetlinks(assetlinks)
    assert data[0]["target"]["site"] == "https://example.com:99999"


def test_warns_invalid_hostname():
    result, data_dir = _run_setup(
        {
            "digital_asset_links_sites": ["https://bad_host"],
            "external_hostname": "ha.example.com",
        },
        log_level=5,
    )
    assert result.returncode == 0
    assert (
        "'https://bad_host' in 'digital_asset_links_sites' does not contain a valid hostname. Continuing with original value."
        in result.stdout
    )
    assetlinks = Path(data_dir) / "www" / ".well-known" / "assetlinks.json"
    data = _read_assetlinks(assetlinks)
    assert data[0]["target"]["site"] == "https://bad_host"


def test_warns_non_numeric_port():
    result, data_dir = _run_setup(
        {
            "digital_asset_links_sites": ["https://example.com:abc"],
            "external_hostname": "ha.example.com",
        },
        log_level=5,
    )
    assert result.returncode == 0
    assert (
        "'https://example.com:abc' in 'digital_asset_links_sites' includes an invalid port. Continuing with original value."
        in result.stdout
    )
    assetlinks = Path(data_dir) / "www" / ".well-known" / "assetlinks.json"
    data = _read_assetlinks(assetlinks)
    assert data[0]["target"]["site"] == "https://example.com:abc"


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


def test_docroot_permissions():
    result, data_dir = _run_setup(
        {
            "digital_asset_links_sites": ["https://example.com"],
            "external_hostname": "ha.example.com",
        }
    )
    assert result.returncode == 0
    docroot = Path(data_dir) / "www"
    mode = docroot.stat().st_mode
    assert mode & stat.S_IRWXG == 0
    assert mode & stat.S_IRWXO == 0


def test_empty_list_removes_output():
    result, data_dir = _run_setup(
        {
            "digital_asset_links_sites": [],
            "external_hostname": "ha.example.com",
        }
    )
    assert result.returncode == 0
    assert not (Path(data_dir) / "www").exists()
