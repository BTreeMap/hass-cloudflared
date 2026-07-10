#!/bin/sh
set -eu

printf '%s\n' "$@" > /data/e2e-cloudflared-args
mkdir -p /data/e2e-metrics

cat > /data/e2e-metrics/metrics <<'EOF'
# HELP cloudflared_e2e_up Whether the deterministic E2E tunnel double is ready.
# TYPE cloudflared_e2e_up gauge
cloudflared_e2e_up 1
EOF

busybox-extras httpd -f -p 0.0.0.0:36500 -h /data/e2e-metrics &
metrics_pid=$!
trap 'kill "${metrics_pid}" 2>/dev/null || true' EXIT INT TERM

attempt=0
until wget -q -O /data/e2e-home-assistant-root http://homeassistant:8123/; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge 60 ]; then
        echo "Home Assistant did not become reachable from the add-on" >&2
        exit 1
    fi
    sleep 1
done
if ! wget -q -O /data/e2e-assetlinks \
    http://127.0.0.1:36555/.well-known/assetlinks.json; then
    rm -f /data/e2e-assetlinks
fi

wait "${metrics_pid}"
