# Home Assistant end-to-end testing

The `Home Assistant E2E` workflow is intentionally independent from required CI,
release, and deployment workflows. It starts on every push to `main`, runs daily
to detect upstream regressions, and can be started manually. Its concurrency
policy cancels an obsolete E2E run when a newer revision arrives. A slow or
failed E2E run never delays CI or a release, but remains visibly failed and keeps
fourteen days of diagnostic artifacts.

## Production fidelity

Each run builds the production add-on Dockerfile and pulls the current
`ghcr.io/home-assistant/home-assistant:stable` image. Both containers run on an
isolated Docker network under their production DNS relationship. The test uses
an authenticated Supervisor-compatible options endpoint backed by
`/data/options.json`, mounts the real Home Assistant configuration at
`/homeassistant`, and runs the add-on's real base image, s6 startup graph,
preparation service, entrypoint, BusyBox Digital Asset Links server, and
installed Cloudflared command boundary. The options endpoint re-reads persisted
configuration for every container start, matching Supervisor restart semantics.

Two deliberately narrow test doubles complete boundaries absent from the
standalone Home Assistant image: the Supervisor options endpoint required by
Bashio startup and the Cloudflared process. Exercising the real Cloudflare
control plane would require privileged account credentials, mutate DNS and
tunnel state, introduce Internet nondeterminism, and make pull requests unsafe.
The deterministic Cloudflared replacement validates arguments, proves it can
reach the real Home Assistant frontend and the internal Digital Asset Links
server, and exposes a metrics readiness endpoint.

## Lifecycle coverage

The orchestrator verifies:

- Home Assistant reaches HTTP readiness and remains alive.
- The exact Home Assistant version is captured in artifacts.
- The production add-on starts and remains alive.
- The add-on can resolve and reach Home Assistant over its container network.
- Remote-tunnel options reach Cloudflared without losing required defaults.
- Post-quantum and additional run parameters are propagated.
- Digital Asset Links are generated, sorted, served, and parse as expected.
- Metrics readiness is externally observable.
- Restart preserves a healthy runtime.
- Configuration mutation is applied on the next start.
- Removing Digital Asset Links removes generated state.
- Removing `post_quantum` removes the corresponding process argument.
- A malformed local hostname fails fast with a non-zero exit and useful log.
- Home Assistant remains healthy after all add-on lifecycle operations.

Container logs, inspections, generated add-on state, image references, and the
exact Home Assistant version are uploaded even when a test fails.

## Deliberate limits

This suite does not validate ownership of a Cloudflare account, public DNS
propagation, Cloudflare edge availability, or an externally reachable tunnel.
Those checks require a dedicated disposable Cloudflare staging account and
should never receive production credentials. Everything up to that external
trust boundary is exercised automatically.
