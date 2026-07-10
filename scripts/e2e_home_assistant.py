"""Exercise the add-on lifecycle against a real Home Assistant container."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    addon_image: str
    home_assistant_image: str
    artifacts: Path
    mock_cloudflared: Path


@dataclass(frozen=True, slots=True)
class Runtime:
    network: str
    home_assistant: str
    addon: str
    invalid_addon: str
    root: Path
    home_assistant_config: Path
    addon_data: Path
    invalid_addon_data: Path


def run(
    command: Sequence[str],
    *,
    check: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess without a shell and preserve diagnostics on failure."""
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        rendered = " ".join(command)
        raise RuntimeError(
            f"command failed ({result.returncode}): {rendered}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def docker(*arguments: str, check: bool = True, timeout: float | None = None) -> str:
    """Run Docker and return normalized stdout."""
    return run(("docker", *arguments), check=check, timeout=timeout).stdout.strip()


def docker_logs(container: str) -> str:
    """Return both output streams emitted by a container."""
    result = run(("docker", "logs", container), check=False)
    return result.stdout + result.stderr


def wait_for_http(url: str, *, timeout: float) -> bytes:
    """Wait until an HTTP endpoint responds successfully or fail with context."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                body: object = response.read()
                if 200 <= response.status < 300:
                    if not isinstance(body, bytes):
                        raise TypeError("HTTP response body must be bytes")
                    return body
        except (TimeoutError, urllib.error.URLError) as error:
            last_error = error
        time.sleep(1)
    raise TimeoutError(f"{url} did not become ready within {timeout}s: {last_error}")


def assert_running(container: str) -> None:
    """Assert that Docker still considers a container alive."""
    state = docker("inspect", "--format", "{{.State.Running}}", container)
    if state != "true":
        raise AssertionError(f"container {container} is not running")


def write_json(path: Path, value: Mapping[str, object]) -> None:
    """Atomically replace a JSON configuration document."""
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def addon_options(*, sites: Sequence[str], post_quantum: bool) -> dict[str, object]:
    """Construct a realistic remote-tunnel configuration for lifecycle testing."""
    return {
        "additional_hosts": [],
        "digital_asset_links_sites": list(sites),
        "external_hostname": "",
        "log_level": "debug",
        "post_quantum": post_quantum,
        "run_parameters": ["--protocol=http2", "--loglevel=debug"],
        "tunnel_token": "deterministic-e2e-token-not-a-secret",
    }


def create_runtime(root: Path) -> Runtime:
    """Allocate isolated names and persistent directories for one E2E run."""
    suffix = uuid.uuid4().hex[:10]
    home_assistant_config = root / "home-assistant"
    addon_data = root / "addon-data"
    invalid_addon_data = root / "invalid-addon-data"
    for directory in (home_assistant_config, addon_data, invalid_addon_data):
        directory.mkdir(parents=True)
    return Runtime(
        network=f"cloudflared-e2e-{suffix}",
        home_assistant=f"home-assistant-e2e-{suffix}",
        addon=f"cloudflared-e2e-{suffix}",
        invalid_addon=f"cloudflared-invalid-e2e-{suffix}",
        root=root,
        home_assistant_config=home_assistant_config,
        addon_data=addon_data,
        invalid_addon_data=invalid_addon_data,
    )


def start_home_assistant(settings: Settings, runtime: Runtime) -> str:
    """Start the latest real Home Assistant image and return its exact version."""
    (runtime.home_assistant_config / "configuration.yaml").write_text(
        "default_config:\n"
        "http:\n"
        "  use_x_forwarded_for: true\n"
        "  trusted_proxies:\n"
        "    - 172.16.0.0/12\n",
        encoding="utf-8",
    )
    docker("pull", settings.home_assistant_image)
    docker("network", "create", runtime.network)
    docker(
        "run",
        "--detach",
        "--name",
        runtime.home_assistant,
        "--network",
        runtime.network,
        "--network-alias",
        "homeassistant",
        "--publish",
        "127.0.0.1::8123",
        "--env",
        "TZ=UTC",
        "--volume",
        f"{runtime.home_assistant_config}:/config",
        settings.home_assistant_image,
    )
    home_assistant_url = (
        f"http://127.0.0.1:{published_port(runtime.home_assistant, 8123)}/"
    )
    wait_for_http(home_assistant_url, timeout=300)
    assert_running(runtime.home_assistant)
    return docker(
        "exec",
        runtime.home_assistant,
        "python",
        "-m",
        "homeassistant",
        "--version",
    )


def published_port(container: str, container_port: int) -> int:
    """Return the random loopback host port assigned by Docker."""
    binding = docker("port", container, f"{container_port}/tcp").splitlines()[0]
    try:
        return int(binding.rsplit(":", maxsplit=1)[1])
    except (IndexError, ValueError) as error:
        raise AssertionError(f"unexpected Docker port binding: {binding}") from error


def start_addon(settings: Settings, runtime: Runtime) -> None:
    """Start the production add-on image with persisted Supervisor-style options."""
    write_json(
        runtime.addon_data / "options.json",
        addon_options(
            sites=("https://ha.example.com", "https://vault.example.com:8443"),
            post_quantum=True,
        ),
    )
    settings.mock_cloudflared.chmod(0o755)
    docker(
        "run",
        "--detach",
        "--name",
        runtime.addon,
        "--network",
        runtime.network,
        "--publish",
        "127.0.0.1::36500",
        "--volume",
        f"{runtime.addon_data}:/data",
        "--volume",
        f"{runtime.home_assistant_config}:/homeassistant:ro",
        "--volume",
        f"{settings.mock_cloudflared}:/usr/bin/cloudflared:ro",
        settings.addon_image,
    )


def assetlink_sites(document: object) -> list[str]:
    """Decode and validate the DAL subset relevant to this system test."""
    if not isinstance(document, list):
        raise AssertionError("Digital Asset Links response is not a JSON list")

    sites: list[str] = []
    for entry in document:
        if not isinstance(entry, Mapping):
            raise AssertionError("Digital Asset Links entries must be mappings")
        target = entry.get("target")
        if not isinstance(target, Mapping):
            raise AssertionError("Digital Asset Links target must be a mapping")
        site = target.get("site")
        if not isinstance(site, str):
            raise AssertionError("Digital Asset Links target.site must be a string")
        sites.append(site)
    return sites


def assert_initial_addon_behavior(runtime: Runtime) -> None:
    """Verify readiness, HA reachability, DAL serving, and tunnel arguments."""
    metrics_url = f"http://127.0.0.1:{published_port(runtime.addon, 36500)}/metrics"
    metrics = wait_for_http(metrics_url, timeout=120).decode()
    if "cloudflared_e2e_up 1" not in metrics:
        raise AssertionError(f"unexpected metrics payload: {metrics}")

    time.sleep(5)
    assert_running(runtime.addon)
    if not (runtime.addon_data / "e2e-home-assistant-root").read_bytes():
        raise AssertionError("add-on could not fetch the Home Assistant frontend")

    assetlinks_document: object = json.loads(
        (runtime.addon_data / "e2e-assetlinks").read_text(encoding="utf-8")
    )
    expected_sites = ["https://ha.example.com", "https://vault.example.com:8443"]
    actual_sites = assetlink_sites(assetlinks_document)
    if actual_sites != expected_sites:
        raise AssertionError(f"unexpected Digital Asset Links sites: {actual_sites}")

    arguments = (runtime.addon_data / "e2e-cloudflared-args").read_text(
        encoding="utf-8"
    )
    for expected in ("--no-autoupdate", "--post-quantum", "--protocol=http2", "run"):
        if expected not in arguments:
            raise AssertionError(
                f"cloudflared arguments omitted {expected}: {arguments}"
            )


def assert_restart_and_reconfiguration(runtime: Runtime) -> None:
    """Verify restart stability, persistent state, and option removal behavior."""
    docker("restart", runtime.addon)
    wait_for_http(
        f"http://127.0.0.1:{published_port(runtime.addon, 36500)}/metrics",
        timeout=120,
    )
    assert_running(runtime.addon)

    docker("stop", runtime.addon)
    write_json(
        runtime.addon_data / "options.json",
        addon_options(sites=(), post_quantum=False),
    )
    for marker in ("e2e-assetlinks", "e2e-cloudflared-args"):
        (runtime.addon_data / marker).unlink(missing_ok=True)
    docker("start", runtime.addon)
    wait_for_http(
        f"http://127.0.0.1:{published_port(runtime.addon, 36500)}/metrics",
        timeout=120,
    )
    assert_running(runtime.addon)
    if (runtime.addon_data / "digital-asset-links").exists():
        raise AssertionError("removing DAL sites did not remove generated state")
    arguments = (runtime.addon_data / "e2e-cloudflared-args").read_text(
        encoding="utf-8"
    )
    if "--post-quantum" in arguments:
        raise AssertionError("removed post_quantum option persisted after restart")


def assert_invalid_config_is_rejected(settings: Settings, runtime: Runtime) -> None:
    """Verify fail-fast behavior for malformed local-tunnel configuration."""
    write_json(
        runtime.invalid_addon_data / "options.json",
        {
            "additional_hosts": [],
            "digital_asset_links_sites": [],
            "external_hostname": "https://invalid.example.com:8123",
            "log_level": "debug",
        },
    )
    docker(
        "run",
        "--detach",
        "--name",
        runtime.invalid_addon,
        "--network",
        runtime.network,
        "--volume",
        f"{runtime.invalid_addon_data}:/data",
        "--volume",
        f"{runtime.home_assistant_config}:/homeassistant:ro",
        "--volume",
        f"{settings.mock_cloudflared}:/usr/bin/cloudflared:ro",
        settings.addon_image,
    )
    docker("wait", runtime.invalid_addon, timeout=60)
    inspection_document: object = json.loads(docker("inspect", runtime.invalid_addon))
    if not isinstance(inspection_document, list) or not inspection_document:
        raise AssertionError("invalid add-on inspection was malformed")
    inspection = inspection_document[0]
    if not isinstance(inspection, Mapping):
        raise AssertionError("invalid add-on inspection entry was malformed")
    state = inspection.get("State")
    if not isinstance(state, Mapping) or not isinstance(state.get("ExitCode"), int):
        raise AssertionError("invalid add-on state was malformed")
    if state["ExitCode"] == 0:
        raise AssertionError("invalid configuration unexpectedly exited successfully")
    logs = docker_logs(runtime.invalid_addon)
    if "is not a valid hostname" not in logs:
        raise AssertionError(
            f"invalid configuration lacked a useful diagnostic: {logs}"
        )


def collect_artifacts(
    settings: Settings, runtime: Runtime, *, version: str | None
) -> None:
    """Always preserve versions, container state, and logs for diagnosis."""
    settings.artifacts.mkdir(parents=True, exist_ok=True)
    containers = {
        "home-assistant": runtime.home_assistant,
        "addon": runtime.addon,
        "invalid-addon": runtime.invalid_addon,
    }
    for label, container in containers.items():
        (settings.artifacts / f"{label}.log").write_text(
            docker_logs(container), encoding="utf-8"
        )
        inspection = docker("inspect", container, check=False)
        (settings.artifacts / f"{label}.inspect.json").write_text(
            inspection,
            encoding="utf-8",
        )
    if runtime.addon_data.exists():
        shutil.copytree(
            runtime.addon_data,
            settings.artifacts / "addon-data",
            dirs_exist_ok=True,
        )
    (settings.artifacts / "summary.json").write_text(
        json.dumps(
            {
                "addon_image": settings.addon_image,
                "home_assistant_image": settings.home_assistant_image,
                "home_assistant_version": version,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def cleanup(runtime: Runtime) -> None:
    """Remove E2E resources independently and idempotently."""
    for container in (runtime.invalid_addon, runtime.addon, runtime.home_assistant):
        docker("rm", "--force", container, check=False)
    docker("network", "rm", runtime.network, check=False)


def parse_settings() -> Settings:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--addon-image", required=True)
    parser.add_argument("--home-assistant-image", required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument(
        "--mock-cloudflared",
        type=Path,
        default=Path("tests/e2e/mock_cloudflared.sh"),
    )
    arguments = parser.parse_args()
    return Settings(
        addon_image=arguments.addon_image,
        home_assistant_image=arguments.home_assistant_image,
        artifacts=arguments.artifacts.resolve(),
        mock_cloudflared=arguments.mock_cloudflared.resolve(),
    )


def main() -> None:
    settings = parse_settings()
    with tempfile.TemporaryDirectory(prefix="hass-cloudflared-e2e-") as temporary:
        runtime = create_runtime(Path(temporary))
        version: str | None = None
        try:
            version = start_home_assistant(settings, runtime)
            start_addon(settings, runtime)
            assert_initial_addon_behavior(runtime)
            assert_restart_and_reconfiguration(runtime)
            assert_invalid_config_is_rejected(settings, runtime)
            assert_running(runtime.home_assistant)
            print(f"E2E passed against Home Assistant {version}")
        finally:
            collect_artifacts(settings, runtime, version=version)
            cleanup(runtime)


if __name__ == "__main__":
    main()
