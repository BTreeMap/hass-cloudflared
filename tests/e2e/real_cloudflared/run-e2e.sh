#!/usr/bin/env bash
# Real-binary end-to-end test for the hass-cloudflared add-on image.
#
# The repo's scripts/e2e_home_assistant.py replaces cloudflared with a test
# double. This harness runs the real cloudflared from the image instead. A
# fake DNS server points the Cloudflare edge at a non-routable address, so
# cloudflared starts, serves metrics and keeps retrying; a small shim answers
# the two Cloudflare API calls (tunnel list, route dns). Everything else, including
# "ingress validate" and "tunnel run", is the real binary.
#
# Each scenario runs on the last released image and on the new image with the
# same /data, and the results are compared. Needs: docker (daemon running),
# jq, curl, python3, and root (the fake DNS binds port 53 on the Docker bridge).
#
# Usage:
#   tests/e2e/real_cloudflared/run-e2e.sh [path/to/hass-cloudflared]
# Environment:
#   NEW_IMAGE   image under test; built from the repo if unset
#   OLD_IMAGE   released image (default ghcr.io/btreemap/cloudflared/amd64:<newest v* tag>)
#   EXTRA_CA    PEM file to trust during the build (TLS-intercepting proxies)
#   KEEP=1      leave containers and helpers running
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${1:-$HERE/../../..}" && pwd)"
WORK="${WORK:-${TMPDIR:-/tmp}/hass-cloudflared-e2e}" # outside the repo, so linters skip it
NET=cf-e2e
GW=172.31.254.1
ADDON_IP=172.31.254.20
HA_IP=172.31.254.30
SINK_IP=192.0.2.1 # TEST-NET-1, never answers
SUP_PORT=18096
PREFIX=cf-e2e
PASS=0
FAIL=0
declare -a RESULTS=()

log() { printf '\n== %s\n' "$*"; }
check() { # check "<name>" <command...>
    local name=$1
    shift
    if "$@" >>"$WORK/checks.log" 2>&1; then
        RESULTS+=("PASS  $name")
        PASS=$((PASS + 1))
    else
        RESULTS+=("FAIL  $name")
        FAIL=$((FAIL + 1))
    fi
}

cleanup() {
    [[ "${KEEP:-0}" == 1 ]] && {
        echo "KEEP=1: leaving environment up (work dir $WORK)"
        return
    }
    docker ps -aq --filter "name=^$PREFIX-" | xargs -r docker rm -f >/dev/null 2>&1
    docker network rm "$NET" >/dev/null 2>&1
    [[ -n "${DNS_PID:-}" ]] && kill "$DNS_PID" 2>/dev/null
    [[ -n "${SUP_PID:-}" ]] && kill "$SUP_PID" 2>/dev/null
}
trap cleanup EXIT

rm -rf "$WORK" && mkdir -p "$WORK"/{nodes,bin}
: >"$WORK/checks.log"

# ------------------------------------------------------------------ images
VERSION_IN_REPO=$(sed -nE 's/^version:[[:space:]]*([^[:space:]#]+).*/\1/p' "$REPO/cloudflared/config.yaml")
CF_PIN=$(sed -nE 's/^ARG CLOUDFLARED_VERSION="([^"]+)"/\1/p' "$REPO/cloudflared/Dockerfile")
log "repo $REPO: add-on $VERSION_IN_REPO, cloudflared pin $CF_PIN"

if [[ -z "${NEW_IMAGE:-}" ]]; then
    NEW_IMAGE="hass-cloudflared-e2e:$VERSION_IN_REPO"
    ctx="$WORK/ctx"
    cp -r "$REPO/cloudflared" "$ctx"
    if [[ -n "${EXTRA_CA:-}" ]]; then
        # Test-only: trust a TLS-intercepting proxy during the build. The shipped
        # Dockerfile is not modified.
        cp "$EXTRA_CA" "$ctx/extra-ca.crt"
        python3 - "$ctx/Dockerfile" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
s = s.replace("# Copy root filesystem\n", "COPY extra-ca.crt /tmp/extra-ca.crt\nRUN cat /tmp/extra-ca.crt >> /etc/ssl/certs/ca-certificates.crt\n# Copy root filesystem\n", 1)
open(p, "w").write(s)
PY
    fi
    log "building $NEW_IMAGE"
    docker build --network host \
        --build-arg HTTPS_PROXY="${HTTPS_PROXY:-}" --build-arg https_proxy="${HTTPS_PROXY:-}" \
        --build-arg BUILD_ARCH=amd64 --build-arg BUILD_VERSION="$VERSION_IN_REPO" \
        -t "$NEW_IMAGE" "$ctx" >"$WORK/build.log" 2>&1 || {
        echo "build failed, see $WORK/build.log"
        exit 1
    }
fi
check "new image runs pinned cloudflared $CF_PIN" bash -c \
    "docker run --rm --entrypoint cloudflared '$NEW_IMAGE' --version | grep -q 'version $CF_PIN '"

if [[ -z "${OLD_IMAGE:-}" ]]; then
    last_tag=$(git -C "$REPO" ls-remote --tags origin 2>/dev/null | sed -nE 's#.*refs/tags/v([0-9.]+)$#\1#p' | sort -V | tail -1)
    OLD_IMAGE="ghcr.io/btreemap/cloudflared/amd64:${last_tag:-8.0.8}"
fi
log "old (released) image: $OLD_IMAGE"
docker pull -q "$OLD_IMAGE" >/dev/null || {
    echo "cannot pull $OLD_IMAGE"
    exit 1
}
# Host copies of both binaries, for side-by-side ingress rule matching.
for tag in old new; do
    img=OLD_IMAGE
    [[ $tag == new ]] && img=NEW_IMAGE
    cid=$(docker create "${!img}")
    docker cp "$cid:/usr/bin/cloudflared" "$WORK/bin/cloudflared-$tag" >/dev/null
    docker rm "$cid" >/dev/null
done

# ------------------------------------------------------------------ helpers
docker network create --subnet "${GW%.*}.0/24" --gateway "$GW" "$NET" >/dev/null
python3 -I "$HERE/edge_dns.py" "$GW" "$SINK_IP" "homeassistant=$HA_IP" >"$WORK/dns.log" 2>&1 &
DNS_PID=$!
python3 -I "$HERE/supervisor_stub.py" "$SUP_PORT" "$WORK/nodes" "$WORK/supervisor.log" &
SUP_PID=$!
sleep 1
# Stand-in for Home Assistant at the "homeassistant" name.
docker run -d --name "$PREFIX-ha" --network "$NET" --ip "$HA_IP" --entrypoint sh "$NEW_IMAGE" -c \
    'mkdir -p /www && echo fake-ha > /www/index.html && exec busybox-extras httpd -f -p 8123 -h /www' >/dev/null

TUNNEL_ID=6f1e9a52-1c1b-4c0e-9f38-0a1d2b3c4d5e
mkdata() { # mkdata <dir> <tunnel name> [ha configuration.yaml]
    local d=$1
    mkdir -p "$d/data" "$d/homeassistant"
    printf '%s\n' "-----BEGIN ARGO TUNNEL TOKEN-----" \
        "$(printf '{"zoneID":"z0","accountID":"0123456789abcdef0123456789abcdef","apiToken":"e2e-not-a-real-token"}' | base64 -w0)" \
        "-----END ARGO TUNNEL TOKEN-----" >"$d/data/cert.pem"
    printf '{"AccountTag":"0123456789abcdef0123456789abcdef","TunnelSecret":"%s","TunnelID":"%s","Endpoint":""}\n' \
        "$(head -c 32 /dev/urandom | base64 -w0)" "$TUNNEL_ID" >"$d/data/tunnel.json"
    echo "$2" >"$d/data/e2e-tunnel-name"
    printf '%s\n' "${3:-homeassistant:}" >"$d/homeassistant/configuration.yaml"
}
run_addon() { # run_addon <container> <image> <dir> <options json>
    local c=$1 img=$2 d=$3
    jq -n --arg ip "$ADDON_IP" --argjson o "$4" '{hostname: "ha", ip: $ip, options: $o}' >"$WORK/nodes/cf.json"
    docker rm -f "$PREFIX-$c" >/dev/null 2>&1
    docker run -d --name "$PREFIX-$c" --network "$NET" --ip "$ADDON_IP" --dns "$GW" \
        --add-host "supervisor:$GW" -e SUPERVISOR_API="http://supervisor:$SUP_PORT" -e SUPERVISOR_TOKEN=cf \
        -v "$d/data:/data" -v "$d/homeassistant:/homeassistant:ro" \
        -v "$HERE/cloudflared_api_shim.sh:/usr/local/bin/cloudflared:ro" \
        "$img" >/dev/null
}
wait_tunnel() { # real cloudflared "tunnel ... run" process is up
    for _ in $(seq 40); do
        docker exec "$PREFIX-$1" sh -c 'ps -o args | grep -q "^/usr/bin/cloudflared tunnel .* run"' 2>/dev/null && return 0
        [[ "$(docker inspect -f '{{.State.Running}}' "$PREFIX-$1")" == true ]] || return 1
        sleep 1
    done
    return 1
}
argv() { docker exec "$PREFIX-$1" sh -c 'ps -o args | grep "^/usr/bin/cloudflared tunnel" | head -1'; }
probe() { # probe <path> -> HTTP code on line 1, then the body; from a sibling container
    docker run --rm --network "$NET" --entrypoint sh "$NEW_IMAGE" -c \
        "curl -s -m 5 -o /tmp/b -w '%{http_code}\n' http://$ADDON_IP:36500$1; cat /tmp/b 2>/dev/null"
}
rules() { # rules <binary> <config> -> matched rule per URL
    local u
    for u in "${URLS[@]}"; do
        printf '%s => ' "$u"
        "$1" tunnel --config "$2" ingress rule "$u" 2>&1 | tr '\n\t' '  ' | sed 's/  */ /g'
        echo
    done
}

URLS=(
    https://ha.example.test/
    https://ha.example.test/api/websocket
    https://ha.example.test/.well-known/assetlinks.json
    https://ha.example.test/x/../.well-known/assetlinks.json
    https://app.example.test/
    https://app.example.test/.well-known/assetlinks.json
    https://ssh.example.test/
    https://unknown.example.test/
)

# ------------------------------------------------------------------ scenarios
# name | options json | HA configuration.yaml
declare -a SCEN_NAMES=(basic full npm ha-tls)
declare -A SCEN_OPTS SCEN_HA
SCEN_OPTS[basic]='{"external_hostname":"ha.example.test","additional_hosts":[],"digital_asset_links_sites":[]}'
SCEN_OPTS[full]='{"external_hostname":"ha.example.test","tunnel_name":"my-home","post_quantum":true,
  "run_parameters":["--loglevel=debug","--grace-period=5s","--edge-ip-version=4","--retries=100","--protocol=quic","--ha-connections=2","--tag=env=e2e"],
  "additional_hosts":[{"hostname":"app.example.test","service":"http://homeassistant:8123","disableChunkedEncoding":true},
                      {"hostname":"ssh.example.test","service":"ssh://homeassistant:22"}],
  "catch_all_service":"http://homeassistant:8123",
  "digital_asset_links_sites":["https://b.example.test","https://a.example.test","https://b.example.test"]}'
SCEN_OPTS[npm]='{"external_hostname":"","nginx_proxy_manager":true,
  "additional_hosts":[{"hostname":"app.example.test","service":"http://homeassistant:8123"}],"digital_asset_links_sites":[]}'
SCEN_OPTS[ha-tls]='{"external_hostname":"ha.example.test","additional_hosts":[],"digital_asset_links_sites":[]}'
SCEN_HA[ha-tls]=$'homeassistant:\nhttp:\n  server_port: 8443\n  ssl_certificate: /ssl/fullchain.pem\n  ssl_key: /ssl/privkey.pem'

for s in "${SCEN_NAMES[@]}"; do
    log "scenario $s"
    name=$(jq -r '.tunnel_name // "homeassistant"' <<<"${SCEN_OPTS[$s]}")
    for tag in old new; do
        img=OLD_IMAGE
        [[ $tag == new ]] && img=NEW_IMAGE
        d="$WORK/$s-$tag"
        mkdata "$d" "$name" "${SCEN_HA[$s]:-}"
        run_addon "$s-$tag" "${!img}" "$d" "${SCEN_OPTS[$s]}"
        if wait_tunnel "$s-$tag"; then echo up >"$d/up"; fi
        docker cp "$PREFIX-$s-$tag:/tmp/config.json" "$d/config.json" >/dev/null 2>&1
        argv "$s-$tag" >"$d/argv" 2>/dev/null
        docker logs "$PREFIX-$s-$tag" >"$d/addon.log" 2>&1
        [[ $tag == new ]] && {
            for _ in $(seq 20); do [[ "$(probe /metrics | head -1)" == 200 ]] && break; sleep 1; done
            probe /ready >"$d/ready"
            probe /metrics >"$d/metrics"
            probe /debug/vars >"$d/debug-vars"
            docker exec "$PREFIX-$s-$tag" curl -s -m 5 http://127.0.0.1:36555/.well-known/assetlinks.json >"$d/assetlinks.json" 2>/dev/null
        }
        docker rm -f "$PREFIX-$s-$tag" >/dev/null # both use $ADDON_IP
    done
    o="$WORK/$s-old" n="$WORK/$s-new"
    check "$s: new image reaches 'tunnel run' with real cloudflared" test -f "$n/up"
    check "$s: config passed real 'ingress validate' in prepare" bash -c \
        "grep -q 'Validating config file' '$n/addon.log' && ! grep -q 'Validation of Config failed' '$n/addon.log'"
    check "$s: generated config identical on old and new image" bash -c \
        "diff <(jq -S . '$o/config.json') <(jq -S . '$n/config.json')"
    check "$s: both binaries accept the config" bash -c \
        "'$WORK/bin/cloudflared-old' tunnel --config '$n/config.json' ingress validate && '$WORK/bin/cloudflared-new' tunnel --config '$n/config.json' ingress validate"
    rules "$WORK/bin/cloudflared-old" "$n/config.json" >"$n/rules-old"
    rules "$WORK/bin/cloudflared-new" "$n/config.json" >"$n/rules-new"
    check "$s: ingress rule matching identical on both binaries" diff "$n/rules-old" "$n/rules-new"
    # Compare everything up to "run": images before 8.0.9 also passed
    # "run <tunnel_name>" (the string "null" when unset), which cloudflared
    # resolved through the credentials file.
    check "$s: cloudflared options identical on old and new image" bash -c \
        "diff <(sed -E 's/ run( [^ ]+)?\$/ run/' '$o/argv') <(sed -E 's/ run( [^ ]+)?\$/ run/' '$n/argv')"
    check "$s: argv runs the named tunnel with origincert and config" bash -c \
        "grep -qE -- '--origincert=/data/cert.pem --config=/tmp/config.json run( |\$)' '$n/argv' && grep -q 'Starting tunnel tunnelID=$TUNNEL_ID' '$n/addon.log'"
    check "$s: DNS routed with -f for every hostname" bash -c \
        "for h in \$(jq -r '.ingress[].hostname // empty' '$n/config.json' | sort -u); do grep -q \"route dns -f $TUNNEL_ID \$h\$\" '$n/data/e2e-api-calls' || exit 1; done"
    check "$s: metrics /ready answered (503 until the edge connects)" grep -qx 503 <(head -1 "$n/ready")
    check "$s: metrics /metrics answered 200" grep -qx 200 <(head -1 "$n/metrics")
    check "$s: no credentials in /debug/vars" bash -c \
        "! grep -qE 'TunnelSecret|apiToken|e2e-not-a-real-token' '$n/debug-vars'"
done

# Expected routing for the richest scenario, checked against the new binary.
f="$WORK/full-new/rules-new"
check "full: HA host routes to http://homeassistant:8123" grep -q '^https://ha.example.test/ => .*service: http://homeassistant:8123' "$f"
check "full: /.well-known/* goes to the DAL server" grep -q '^https://ha.example.test/.well-known/assetlinks.json => .*service: http://127.0.0.1:36555' "$f"
check "full: unknown host hits catch_all_service" grep -q '^https://unknown.example.test/ => .*service: http://homeassistant:8123' "$f"
check "full: ssh additional host keeps ssh:// service" grep -q '^https://ssh.example.test/ => .*service: ssh://homeassistant:22' "$f"
check "full: disableChunkedEncoding and noTLSVerify in config" jq -e \
    '(.ingress[] | select(.hostname=="app.example.test") | .originRequest) == {"disableChunkedEncoding":true,"noTLSVerify":true}' \
    "$WORK/full-new/config.json"
check "full: --post-quantum and run_parameters passed" bash -c \
    "grep -q -- '--post-quantum --loglevel=debug --grace-period=5s --edge-ip-version=4 --retries=100 --protocol=quic --ha-connections=2 --tag=env=e2e' '$WORK/full-new/argv'"
check "full: assetlinks.json served, deduplicated and sorted" bash -c \
    "jq -e 'map(.target.site)==[\"https://a.example.test\",\"https://b.example.test\"]' '$WORK/full-new/assetlinks.json'"
check "npm: unmatched hosts go to Nginx Proxy Manager" grep -q '^https://unknown.example.test/ => .*service: http://a0d7b954-nginxproxymanager:80' "$WORK/npm-new/rules-new"
check "basic: unmatched hosts get http_status:404" grep -q '^https://unknown.example.test/ => .*service: http_status:404' "$WORK/basic-new/rules-new"
check "ha-tls: HA reached over https on server_port" jq -e \
    '.ingress[] | select(.hostname=="ha.example.test") | .service == "https://homeassistant:8443"' "$WORK/ha-tls-new/config.json"

log "restart with existing /data (fresh container, as Supervisor does)"
: >"$WORK/full-new/data/e2e-api-calls"
run_addon full-new "$NEW_IMAGE" "$WORK/full-new" "${SCEN_OPTS[full]}"
check "restart: tunnel reused, real cloudflared running again" wait_tunnel full-new
check "restart: no tunnel create, name checked via API" bash -c \
    "grep -q '^list ' '$WORK/full-new/data/e2e-api-calls' && ! docker logs $PREFIX-full-new 2>&1 | grep -q 'Creating new tunnel'"
t0=$(date +%s)
docker stop -t 30 "$PREFIX-full-new" >/dev/null
t1=$(date +%s)
check "stop: container exits within 10s ($((t1 - t0))s; grace period 5s)" test $((t1 - t0)) -le 10

log "remote-managed tunnel (tunnel_token)"
TOKEN=$(printf '{"a":"0123456789abcdef0123456789abcdef","t":"%s","s":"%s"}' "$TUNNEL_ID" "$(head -c 32 /dev/urandom | base64 -w0)" | base64 -w0)
TOKEN_OPTS=$(jq -n --arg t "$TOKEN" '{external_hostname:"",additional_hosts:[],tunnel_token:$t,post_quantum:true,log_level:"debug",
  digital_asset_links_sites:["https://a.example.test"]}')
for tag in old new; do
    img=OLD_IMAGE
    [[ $tag == new ]] && img=NEW_IMAGE
    d="$WORK/token-$tag"
    mkdir -p "$d/data" "$d/homeassistant"
    run_addon "token-$tag" "${!img}" "$d" "$TOKEN_OPTS"
    wait_tunnel "token-$tag" && echo up >"$d/up"
    argv "token-$tag" | sed "s/$TOKEN/<TOKEN>/" >"$d/argv"
    probe /debug/vars >"$d/debug-vars"
    docker logs "$PREFIX-token-$tag" >"$d/addon.log" 2>&1
    docker rm -f "$PREFIX-token-$tag" >/dev/null
done
check "token: new image reaches 'tunnel run' with real cloudflared" test -f "$WORK/token-new/up"
check "token: options before 'run' identical on old and new image" bash -c \
    "diff <(sed 's/ run .*//' '$WORK/token-old/argv') <(sed 's/ run .*//' '$WORK/token-new/argv')"
check "token: tunnel starts from the token's tunnel ID" grep -q "Starting tunnel tunnelID=$TUNNEL_ID" "$WORK/token-new/addon.log"
check "token: token not on the new image's command line" bash -c "! grep -q '<TOKEN>' '$WORK/token-new/argv'"
check "token: token not in the new image's log" bash -c "! grep -q '$TOKEN' '$WORK/token-new/addon.log'"
check "token: new image does not expose the token in /debug/vars" bash -c "! grep -q '$TOKEN' '$WORK/token-new/debug-vars'"
if grep -q "$TOKEN" "$WORK/token-old/debug-vars"; then
    RESULTS+=("NOTE  token: OLD image exposes the tunnel token at :36500/debug/vars")
fi
grep -q "$TOKEN" "$WORK/token-old/argv" "$WORK/token-old/addon.log" 2>/dev/null &&
    RESULTS+=("NOTE  token: OLD image puts the tunnel token on the command line and in the debug log")

# ------------------------------------------------------------------ report
log "results ($NEW_IMAGE vs $OLD_IMAGE)"
printf '%s\n' "${RESULTS[@]}"
printf '\n%d passed, %d failed. Logs: %s\n' "$PASS" "$FAIL" "$WORK"
[[ $FAIL -eq 0 ]]
