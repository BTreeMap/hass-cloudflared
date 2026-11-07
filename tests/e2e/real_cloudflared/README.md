# Real-cloudflared E2E harness

`scripts/e2e_home_assistant.py` replaces cloudflared with a test double. This
harness keeps the real cloudflared binary from the image and checks how the
add-on scripts and that binary work together. It was written to validate
cloudflared and base image bumps before a release.

## How it works

- `edge_dns.py` answers the Cloudflare edge SRV records with a non-routable
  address. cloudflared starts, serves metrics and keeps retrying the edge
  instead of exiting.
- `cloudflared_api_shim.sh` is mounted at `/usr/local/bin/cloudflared`, ahead
  of `/usr/bin/cloudflared`. It answers `tunnel list` and `tunnel route dns`,
  the two calls that need the Cloudflare API, and records them. Every other
  call, including `ingress validate` and `tunnel run`, goes to the real binary.
- `supervisor_stub.py` serves the add-on options to Bashio.
- `run-e2e.sh` runs each scenario on the last released image and on the new
  image with the same `/data`, compares the results, and prints a PASS/FAIL
  table.

## Usage

```sh
sudo tests/e2e/real_cloudflared/run-e2e.sh            # tests this checkout
sudo tests/e2e/real_cloudflared/run-e2e.sh /other/checkout
```

Root is needed because the fake DNS binds port 53 on the Docker bridge. It
needs a running Docker daemon, jq, curl and python3, and pulls the released
image from GHCR. Logs go to `/tmp/hass-cloudflared-e2e` (or `$WORK`), outside
the repo so the linters skip them.

Environment variables:

- `NEW_IMAGE`: test an existing image instead of building one.
- `OLD_IMAGE`: released image to compare against (default: the newest `v*`
  tag on the repo's origin).
- `EXTRA_CA`: PEM file to trust during the build, for TLS-intercepting
  proxies. Only the build context copy of the Dockerfile is changed.
- `WORK`: where logs and per-scenario state go.
- `KEEP=1`: leave containers and helpers running.

## What it covers

Named-tunnel scenarios: `basic` (external hostname only), `full` (Digital
Asset Links, two additional hosts, catch-all service, post-quantum and run
parameters), `npm` (Nginx Proxy Manager fallback) and `ha-tls` (Home
Assistant on HTTPS with a custom port). For each one:

- Prepare builds the config and the real `ingress validate` accepts it.
- The generated config and the cloudflared command line are the same on the
  old and new image.
- Both binaries accept the config and route a fixed set of URLs to the same
  ingress rules.
- DNS routes are created for every hostname.
- The metrics server answers `/ready` and `/metrics`, and `/debug/vars` holds
  no credentials.

Also: expected routing for `full`, `npm` and `basic`; a restart that reuses the
tunnel; stop time; and the remote-managed (`tunnel_token`) mode, including a
check that the token is not exposed on `/debug/vars`.

## Known gaps

- No traffic flows through a tunnel, since there is no Cloudflare edge. Origin
  reachability is covered by `scripts/e2e_home_assistant.py`.
- `tunnel login` and `tunnel create` are not run; the harness starts with a
  certificate and tunnel credentials already in `/data`.
- amd64 only.
