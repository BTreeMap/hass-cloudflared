#!/bin/sh
# Mounted at /usr/local/bin/cloudflared, ahead of the real /usr/bin/cloudflared
# in PATH. Answers only the subcommands that need the Cloudflare API (tunnel
# list, tunnel route dns) with canned output and records the call. Every other
# invocation, including "ingress validate" and "tunnel run", goes to the real
# binary unchanged.
set -eu

real=/usr/bin/cloudflared
log=/data/e2e-api-calls

case " $* " in
*" tunnel list "* | *" list --output"*)
    printf 'list %s\n' "$*" >>"${log}"
    id=""
    for arg in "$@"; do
        case "${arg}" in --id=*) id="${arg#--id=}" ;; esac
    done
    name="$(cat /data/e2e-tunnel-name 2>/dev/null || echo homeassistant)"
    printf '[{"id":"%s","name":"%s","created_at":"2026-01-01T00:00:00Z","connections":[]}]\n' "${id}" "${name}"
    exit 0
    ;;
*" route dns "*)
    printf 'route %s\n' "$*" >>"${log}"
    echo "Added CNAME (shim)"
    exit 0
    ;;
esac

exec "${real}" "$@"
