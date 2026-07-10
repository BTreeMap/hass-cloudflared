from __future__ import annotations

import json
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast

import pytest
from conftest import PROJECT_ROOT

RUN_SH = (
    PROJECT_ROOT
    / "cloudflared"
    / "rootfs"
    / "etc"
    / "s6-overlay"
    / "s6-rc.d"
    / "prepare"
    / "run.sh"
)
BASHIO_REF = "9b30bab926bdba7b9fc0e0f2d2871ef14e17e8d6"

_SETUP_SCRIPT = r"""
set -euo pipefail
umask 022

readonly cache_dir="$1"
readonly log_level="$2"
readonly bashio_dir="$3"
readonly bashio_lib_dir="$4"
readonly config_json="$5"
readonly dal_root="$6"
readonly run_sh="$7"

export CACHE_DIR="$cache_dir"
export LOG_LEVEL="$log_level"
export BASHIO_DIR="$bashio_dir"
source "$bashio_lib_dir/bashio.sh"

bashio::addon.config() { cat "$config_json"; }
export DAL_ROOT_OVERRIDE="$dal_root"
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


class AssetTarget(TypedDict):
    site: str


class AssetLink(TypedDict):
    target: AssetTarget


@dataclass(frozen=True, slots=True)
class SetupResult:
    process: subprocess.CompletedProcess[str]
    data_dir: Path

    @property
    def assetlinks_path(self) -> Path:
        return self.data_dir / "www" / ".well-known" / "assetlinks.json"


@pytest.fixture(scope="session")
def bashio_lib(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Clone the immutable bashio revision once for the integration-test session."""
    bashio_dir = tmp_path_factory.mktemp("bashio")
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "https://github.com/hassio-addons/bashio",
            str(bashio_dir),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(bashio_dir), "checkout", "--quiet", BASHIO_REF],
        check=True,
        capture_output=True,
        text=True,
    )
    return bashio_dir / "lib"


def _run_setup(
    config_payload: Mapping[str, object],
    *,
    tmp_path: Path,
    bashio_lib: Path,
    log_level: int = 0,
) -> SetupResult:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    for directory in (config_dir, data_dir, cache_dir):
        directory.mkdir(parents=True, exist_ok=True)

    config_json = config_dir / "config.json"
    config_json.write_text(json.dumps(config_payload), encoding="utf-8")
    process = subprocess.run(
        [
            "/bin/bash",
            "-c",
            _SETUP_SCRIPT,
            "prepare-integration-test",
            str(cache_dir),
            str(log_level),
            str(bashio_lib.parent),
            str(bashio_lib),
            str(config_json),
            str(data_dir),
            str(RUN_SH),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return SetupResult(process=process, data_dir=data_dir)


def _read_assetlinks(path: Path) -> list[AssetLink]:
    raw_links: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_links, list):
        raise AssertionError("assetlinks document must be a list")
    for raw_link in raw_links:
        if not isinstance(raw_link, dict):
            raise AssertionError("every asset link must be a mapping")
        target = raw_link.get("target")
        if not isinstance(target, dict) or not isinstance(target.get("site"), str):
            raise AssertionError("every asset link must contain a string target.site")
    return cast(list[AssetLink], raw_links)


def _assert_success(result: SetupResult) -> None:
    assert result.process.returncode == 0, result.process.stderr


@pytest.mark.parametrize(
    ("site", "expects_warning"),
    [
        ("http://example.com", True),
        ("https://example.com/path", True),
        ("https://example.com:99999", False),
        ("https://bad_host", False),
        ("https://example.com:abc", False),
    ],
)
def test_preserves_configured_site_and_reports_pattern_mismatch(
    site: str,
    expects_warning: bool,
    tmp_path: Path,
    bashio_lib: Path,
) -> None:
    result = _run_setup(
        {
            "digital_asset_links_sites": [site],
            "external_hostname": "ha.example.com",
        },
        tmp_path=tmp_path,
        bashio_lib=bashio_lib,
        log_level=5,
    )
    warning = (
        f"'{site}' in 'digital_asset_links_sites' does not match the expected "
        "https://hostname[:port] pattern. Continuing with original value."
    )

    _assert_success(result)
    assert (warning in result.process.stdout) is expects_warning
    assert _read_assetlinks(result.assetlinks_path)[0]["target"]["site"] == site


def test_deduplicates_and_sorts_sites(tmp_path: Path, bashio_lib: Path) -> None:
    result = _run_setup(
        {
            "digital_asset_links_sites": [
                "https://b.example.com",
                "https://a.example.com",
                "https://a.example.com",
            ],
            "external_hostname": "ha.example.com",
        },
        tmp_path=tmp_path,
        bashio_lib=bashio_lib,
    )

    _assert_success(result)
    sites = [
        entry["target"]["site"] for entry in _read_assetlinks(result.assetlinks_path)
    ]
    assert sites == ["https://a.example.com", "https://b.example.com"]


def test_accepts_valid_port(tmp_path: Path, bashio_lib: Path) -> None:
    result = _run_setup(
        {
            "digital_asset_links_sites": ["https://example.com:8443"],
            "external_hostname": "ha.example.com",
        },
        tmp_path=tmp_path,
        bashio_lib=bashio_lib,
    )

    _assert_success(result)
    assert _read_assetlinks(result.assetlinks_path)[0]["target"]["site"] == (
        "https://example.com:8443"
    )


def test_docroot_permissions_are_private(tmp_path: Path, bashio_lib: Path) -> None:
    result = _run_setup(
        {
            "digital_asset_links_sites": ["https://example.com"],
            "external_hostname": "ha.example.com",
        },
        tmp_path=tmp_path,
        bashio_lib=bashio_lib,
    )

    _assert_success(result)
    mode = (result.data_dir / "www").stat().st_mode
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_empty_site_list_removes_output(tmp_path: Path, bashio_lib: Path) -> None:
    result = _run_setup(
        {
            "digital_asset_links_sites": [],
            "external_hostname": "ha.example.com",
        },
        tmp_path=tmp_path,
        bashio_lib=bashio_lib,
    )

    _assert_success(result)
    assert not (result.data_dir / "www").exists()
