#!/bin/sh
set -eu

printf '%s\n' "$@" > /data/e2e-cloudflared-args
mkdir -p /data/e2e-metrics

wget -q -O /data/e2e-home-assistant-root http://homeassistant:8123/
if ! wget -q -O /data/e2e-assetlinks \
    http://127.0.0.1:36555/.well-known/assetlinks.json; then
    rm -f /data/e2e-assetlinks
fi

cat > /data/e2e-metrics/metrics <<'EOF'
# HELP cloudflared_e2e_up Whether the deterministic E2E tunnel double is ready.
# TYPE cloudflared_e2e_up gauge
cloudflared_e2e_up 1
EOF

exec busybox-extras httpd -f -p 0.0.0.0:36500 -h /data/e2e-metrics
