import json
from http import HTTPStatus
from pathlib import Path
from types import TracebackType
from typing import Self

import pytest

from scripts.e2e_home_assistant import (
    addon_options,
    assetlink_sites,
    wait_for_http,
    write_json,
)
from tests.e2e.mock_supervisor import supervisor_response


class SuccessfulResponse:
    """Minimal typed context manager matching the urllib response boundary."""

    status = 200

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def read(self) -> bytes:
        return b"ready"


def test_addon_options_constructs_independent_serializable_values() -> None:
    sites = ["https://ha.example.com"]

    options = addon_options(sites=sites, post_quantum=True)
    sites.append("https://later.example.com")

    assert options["digital_asset_links_sites"] == ["https://ha.example.com"]
    assert options["post_quantum"] is True
    json.dumps(options)


def test_assetlink_sites_decodes_validated_sites() -> None:
    document: object = [
        {"target": {"site": "https://ha.example.com"}},
        {"target": {"site": "https://vault.example.com:8443"}},
    ]

    assert assetlink_sites(document) == [
        "https://ha.example.com",
        "https://vault.example.com:8443",
    ]


@pytest.mark.parametrize(
    "document",
    [None, {}, [{}], [{"target": None}], [{"target": {"site": 42}}]],
)
def test_assetlink_sites_rejects_malformed_documents(document: object) -> None:
    with pytest.raises(AssertionError):
        assetlink_sites(document)


def test_write_json_replaces_document_deterministically(tmp_path: Path) -> None:
    destination = tmp_path / "options.json"

    write_json(destination, {"z": 1, "a": True})

    assert destination.read_text(encoding="utf-8") == ('{\n  "a": true,\n  "z": 1\n}\n')
    assert not destination.with_suffix(".json.tmp").exists()


def test_wait_for_http_retries_connection_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def open_with_startup_reset(url: str, *, timeout: float) -> SuccessfulResponse:
        nonlocal attempts
        del url, timeout
        attempts += 1
        if attempts == 1:
            raise ConnectionResetError("service is still starting")
        return SuccessfulResponse()

    monkeypatch.setattr("urllib.request.urlopen", open_with_startup_reset)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    assert wait_for_http("http://home-assistant.test", timeout=1) == b"ready"
    assert attempts == 2


def test_wait_for_http_fails_immediately_when_process_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_request(url: str, *, timeout: float) -> SuccessfulResponse:
        del url, timeout
        raise AssertionError("HTTP must not be attempted after process exit")

    def assert_live() -> None:
        raise AssertionError("container exited")

    monkeypatch.setattr("urllib.request.urlopen", reject_request)

    with pytest.raises(AssertionError, match="container exited"):
        wait_for_http(
            "http://cloudflared.test/metrics",
            timeout=120,
            assert_live=assert_live,
        )


def test_supervisor_response_authenticates_and_reloads_options(tmp_path: Path) -> None:
    options_path = tmp_path / "options.json"
    token_paths = {"Bearer accepted": options_path}
    write_json(options_path, {"log_level": "debug"})

    status, document = supervisor_response(
        "/addons/self/options/config",
        "Bearer accepted",
        token_paths=token_paths,
    )
    assert status is HTTPStatus.OK
    assert document == {"result": "ok", "data": {"log_level": "debug"}}

    write_json(options_path, {"log_level": "info"})
    _, reloaded = supervisor_response(
        "/addons/self/options/config",
        "Bearer accepted",
        token_paths=token_paths,
    )
    assert reloaded["data"] == {"log_level": "info"}


def test_supervisor_response_rejects_unknown_token(tmp_path: Path) -> None:
    status, document = supervisor_response(
        "/addons/self/options/config",
        "Bearer rejected",
        token_paths={"Bearer accepted": tmp_path / "options.json"},
    )

    assert status is HTTPStatus.UNAUTHORIZED
    assert document == {"result": "error"}
