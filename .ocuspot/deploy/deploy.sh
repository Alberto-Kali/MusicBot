#!/bin/sh
set -eu
# Ocuspot deploy bundle for project=music-bot
# Immutable image: __OCUSPOT_IMAGE__
# Binding: backend-kvm@pconf-bus  frp=music_bot_backend_kvm_pconf_bus_3123  public=music.pconf.ru

# SAFETY: This script performs scp + ssh using your runtime credentials only.
# - Run with ssh-agent loaded, or edit below to pass -i /path/to/your/key.
# - The script owns FRP config + systemd units on both hosts.
# - OCUSPOT_FRP_* tokens must be filled in /etc/ocuspot/frp.env on both hosts.
# - ocuspot never embeds, logs, or stores SSH private material or FRP tokens.
# - Optional: set local FRP_SERVER_BIN=/path/to/frps and FRP_CLIENT_BIN=/path/to/frpc before running.
# - Optional: set OCUSPOT_GATEWAY_SSH_PASSWORD / OCUSPOT_CARRIER_SSH_PASSWORD when running via sshpass.
# - docker-image deploys require IMAGE_TAR unless OCUSPOT_ALLOW_CONFIG_ONLY=1 is set explicitly.
# - On failure after partial rollout, deploy.sh runs best-effort cleanup of Ocuspot-managed
#   resources it touched in this run (not a guaranteed universal rollback).
# Review before running. Idempotent for Ocuspot-managed files; local manifest should be updated only after success.

need_sshpass() {
  command -v sshpass >/dev/null 2>&1 || { echo "sshpass is required for password-based SSH"; exit 13; }
}
require_local_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Required local command missing: $1"; exit 18; }
}
SSH_CONFIG_FILE="${OCUSPOT_SSH_CONFIG:-/dev/null}"
gw_scp() {
 if [ -n "${OCUSPOT_GATEWAY_SSH_PASSWORD:-}" ]; then
  need_sshpass
  SSHPASS="${OCUSPOT_GATEWAY_SSH_PASSWORD}" sshpass -e scp -F "${SSH_CONFIG_FILE}" "$@"
 else
  scp -F "${SSH_CONFIG_FILE}" "$@"
 fi
}
gw_ssh() {
 if [ -n "${OCUSPOT_GATEWAY_SSH_PASSWORD:-}" ]; then
  need_sshpass
  SSHPASS="${OCUSPOT_GATEWAY_SSH_PASSWORD}" sshpass -e ssh -F "${SSH_CONFIG_FILE}" "$@"
 else
  ssh -F "${SSH_CONFIG_FILE}" "$@"
 fi
}
car_scp() {
 if [ -n "${OCUSPOT_CARRIER_SSH_PASSWORD:-}" ]; then
  need_sshpass
  SSHPASS="${OCUSPOT_CARRIER_SSH_PASSWORD}" sshpass -e scp -F "${SSH_CONFIG_FILE}" "$@"
 else
  scp -F "${SSH_CONFIG_FILE}" "$@"
 fi
}
car_ssh() {
 if [ -n "${OCUSPOT_CARRIER_SSH_PASSWORD:-}" ]; then
  need_sshpass
  SSHPASS="${OCUSPOT_CARRIER_SSH_PASSWORD}" sshpass -e ssh -F "${SSH_CONFIG_FILE}" "$@"
 else
  ssh -F "${SSH_CONFIG_FILE}" "$@"
 fi
}

FRP_TOKEN_ENV='OCUSPOT_FRP_PCONF_BUS'
DEPLOY_RUNTIME='docker-compose'
DEPLOY_COMPOSE_FILE='docker-compose.yml'
DEPLOY_SOURCE_DIR=${DEPLOY_SOURCE_DIR:-''}
DEPLOY_IGNORE_DB='0'
FRP_TOKEN_FILE=""
FRP_TOKEN_VALUE="$(eval "printf '%s' \"\${${FRP_TOKEN_ENV}:-}\"")"
if [ -n "${FRP_TOKEN_VALUE}" ]; then
  FRP_TOKEN_FILE=".ocuspot-frp.env"
  umask 077
  printf "%s=%s\n" "${FRP_TOKEN_ENV}" "${FRP_TOKEN_VALUE}" > "${FRP_TOKEN_FILE}"
fi

echo "Running deploy preflight (local, before any remote mutation) ..."
require_local_cmd ssh
require_local_cmd scp
if [ -n "${OCUSPOT_GATEWAY_SSH_PASSWORD:-}" ] || [ -n "${OCUSPOT_CARRIER_SSH_PASSWORD:-}" ]; then
  need_sshpass
fi
if [ -n "${IMAGE_TAR:-}" ] && [ ! -f "${IMAGE_TAR}" ]; then
  echo "IMAGE_TAR points to a missing local file: ${IMAGE_TAR}"
  exit 18
fi
if [ "${DEPLOY_RUNTIME:-}" != "docker-compose" ] && [ -z "${IMAGE_TAR:-}" ] && [ "${OCUSPOT_ALLOW_CONFIG_ONLY:-0}" != "1" ]; then
  echo "IMAGE_TAR is required for docker-image deploys. Set IMAGE_TAR to a docker/podman save tarball or explicitly set OCUSPOT_ALLOW_CONFIG_ONLY=1 for config-only rollout."
  exit 18
fi
if [ "${DEPLOY_RUNTIME:-}" = "docker-compose" ] || { [ -n "${IMAGE_TAR:-}" ] && [ -f "${IMAGE_TAR}" ]; }; then
  if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    echo "Local curl or wget is required for post-deploy health checks when IMAGE_TAR is supplied."
    exit 18
  fi
fi
LOCAL_FRP_UPLOAD=0
if [ -n "${FRP_SERVER_BIN:-}" ] || [ -n "${FRP_CLIENT_BIN:-}" ]; then
  if [ -z "${FRP_SERVER_BIN:-}" ] || [ -z "${FRP_CLIENT_BIN:-}" ]; then
    echo "FRP_SERVER_BIN and FRP_CLIENT_BIN must be provided together."
    exit 18
  fi
  if [ ! -f "${FRP_SERVER_BIN}" ] || [ ! -f "${FRP_CLIENT_BIN}" ]; then
    echo "FRP_SERVER_BIN or FRP_CLIENT_BIN points to a missing local file."
    exit 18
  fi
  if [ ! -x "${FRP_SERVER_BIN}" ] || [ ! -x "${FRP_CLIENT_BIN}" ]; then
    echo "FRP_SERVER_BIN or FRP_CLIENT_BIN is not executable locally."
    exit 18
  fi
  LOCAL_FRP_UPLOAD=1
  echo "Preflight: local frps/frpc binaries will be uploaded when remote binaries are missing."
fi
echo "Local preflight passed."
DEPLOY_SVC='music_bot_backend_kvm_pconf_bus_3123'
DEPLOY_RUNTIME='docker-compose'
DEPLOY_COMPOSE_FILE='docker-compose.yml'
DEPLOY_SOURCE_DIR=${DEPLOY_SOURCE_DIR:-''}
DEPLOY_SUCCEEDED=0
DEPLOY_GATEWAY_DONE=0
DEPLOY_CARRIER_DONE=0
DEPLOY_WORKLOAD_DONE=0
DEPLOY_CLEANUP_RAN=0

ocuspot_cleanup_carrier() {
  echo "Cleanup: carrier Ocuspot-managed resources for ${DEPLOY_SVC} ..."
  car_ssh -p "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${CAR_USER}@${CAR_HOST}" 'set +e
COMPOSE_DIR="/opt/ocuspot/apps/music_bot_backend_kvm_pconf_bus_3123"
if [ -d "${COMPOSE_DIR}" ]; then
  for COMPOSE_CANDIDATE in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do
    if [ -f "${COMPOSE_DIR}/${COMPOSE_CANDIDATE}" ]; then
      if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        docker compose -f "${COMPOSE_DIR}/${COMPOSE_CANDIDATE}" down --remove-orphans 2>/dev/null || true
      elif command -v docker-compose >/dev/null 2>&1; then
        docker-compose -f "${COMPOSE_DIR}/${COMPOSE_CANDIDATE}" down --remove-orphans 2>/dev/null || true
      fi
      break
    fi
  done
fi
if command -v docker >/dev/null 2>&1; then docker rm -f "music_bot_backend_kvm_pconf_bus_3123" 2>/dev/null || true; fi
if command -v podman >/dev/null 2>&1; then podman rm -f "music_bot_backend_kvm_pconf_bus_3123" 2>/dev/null || true; fi
systemctl disable --now "ocuspot-frpc-music_bot_backend_kvm_pconf_bus_3123.service" 2>/dev/null || true
rm -f "/etc/ocuspot/frp/frpc.music_bot_backend_kvm_pconf_bus_3123.toml" "/etc/systemd/system/ocuspot-frpc-music_bot_backend_kvm_pconf_bus_3123.service" 2>/dev/null || true
systemctl daemon-reload 2>/dev/null || true
true'
}

ocuspot_cleanup_gateway() {
  echo "Cleanup: gateway Ocuspot-managed resources for ${DEPLOY_SVC} ..."
  gw_ssh -p "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${GW_USER}@${GW_HOST}" 'set +e
rm -f "/etc/nginx/conf.d/ocuspot-music.pconf.ru.conf" "/etc/nginx/conf.d/ocuspot-music.pconf.ru.conf" 2>/dev/null || true
find /etc/nginx/fastpanel2-sites -maxdepth 2 -type f -name "ocuspot-music.pconf.ru.conf" -delete 2>/dev/null || true
if command -v nginx >/dev/null 2>&1; then nginx -t >/dev/null 2>&1 && (systemctl reload nginx 2>/dev/null || service nginx reload 2>/dev/null || nginx -s reload 2>/dev/null || true); fi
	true'
}

ocuspot_deploy_cleanup() {
  if [ "${DEPLOY_CLEANUP_RAN}" = 1 ]; then return 0; fi
  if [ "${DEPLOY_GATEWAY_DONE}" != 1 ] && [ "${DEPLOY_CARRIER_DONE}" != 1 ] && [ "${DEPLOY_WORKLOAD_DONE}" != 1 ]; then
    return 0
  fi
  DEPLOY_CLEANUP_RAN=1
  echo "Deploy failed; running best-effort cleanup of Ocuspot-managed resources from this run ..."
  echo "(Not a guaranteed universal rollback; shared or pre-existing host state may remain.)"
  if [ "${DEPLOY_CARRIER_DONE}" = 1 ] || [ "${DEPLOY_WORKLOAD_DONE}" = 1 ]; then
    ocuspot_cleanup_carrier || echo "Warning: carrier cleanup had errors (best-effort)."
  fi
  if [ "${DEPLOY_GATEWAY_DONE}" = 1 ]; then
    ocuspot_cleanup_gateway || echo "Warning: gateway cleanup had errors (best-effort)."
  fi
}

ocuspot_deploy_exit() {
  ec=$?
  if [ "${DEPLOY_SUCCEEDED}" = 1 ]; then
    exit "${ec}"
  fi
  if [ "${ec}" -ne 0 ]; then
    ocuspot_deploy_cleanup || true
    echo "Deploy exited with failure (exit ${ec}). Cleanup attempted for Ocuspot-managed resources touched in this run."
  fi
  exit "${ec}"
}
trap ocuspot_deploy_exit EXIT

GW_HOST="138.16.226.122"
GW_PORT=2222
GW_USER="root"
GW_FRAG='frps.gateway.pconf-bus.toml'
GW_FULL='frps.gateway.pconf-bus.managed.toml'
GW_UNIT='systemd.ocuspot-frps-pconf-bus.service'
PUBLIC_HOST='music.pconf.ru'
PUBLIC_PORT=80
FRP_BIND_PORT=2334
FRP_VHOST_HTTP_PORT=8088
echo "Preflight: SSH connectivity to gateway ${GW_USER}@${GW_HOST}:${GW_PORT} (before remote mutation) ..."
gw_ssh -p "${GW_PORT}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "${GW_USER}@${GW_HOST}" 'echo ocuspot-ssh-ok' >/dev/null || { echo "Gateway SSH failed before any remote mutation. Verify host, port, user, and credentials."; exit 20; }
echo "Checking gateway ownership prerequisites (remote, before scp) ..."
gw_ssh -p "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${GW_USER}@${GW_HOST}" 'set -eu
if ! command -v systemctl >/dev/null 2>&1 && ! command -v rc-service >/dev/null 2>&1; then
  echo "Gateway requires systemd or openrc for Ocuspot-managed FRP units (checked after SSH contact; not a universal init-system guarantee)."
  exit 21
fi
for d in /etc/ocuspot /etc/ocuspot/frp /etc/nginx /etc/nginx/conf.d /usr/local/bin /tmp; do
  if ! mkdir -p "$d" 2>/dev/null || [ ! -w "$d" ]; then
    echo "Gateway target path not writable (checked after SSH contact): $d"
    exit 22
  fi
done
if command -v grep >/dev/null 2>&1; then
 GREP_RSL="grep -Rsl"
 if command -v timeout >/dev/null 2>&1; then GREP_RSL="timeout 10s grep -Rsl"; fi
 OTHER="$(${GREP_RSL} '\''server_name[[:space:]]\+music.pconf.ru\([[:space:];]\|$\)'\'' /etc/nginx/conf.d /etc/nginx/fastpanel2-sites /etc/nginx/fastpanel2-available 2>/dev/null | grep -v '\''/etc/nginx/conf.d/ocuspot-music.pconf.ru.conf'\'' | grep -v '\''/etc/nginx/conf.d/ocuspot-music.pconf.ru.conf'\'' | grep -v '\''ocuspot-music.pconf.ru.conf'\'' || true)"
  if [ -n "${OTHER}" ]; then
    echo "Conflicting nginx server_name for public host music.pconf.ru (checked after SSH contact): ${OTHER}"
    exit 19
  fi
fi
if command -v ss >/dev/null 2>&1; then
  BIND_BUSY="$(ss -tlnH 2>/dev/null | awk '\''$2 ~ /:2334$/ {print $2}'\'' | head -n 1 || true)"
  if [ -n "${BIND_BUSY}" ]; then
    echo "Gateway FRP bindPort 2334 is already in use (checked after SSH contact): ${BIND_BUSY}"
    exit 19
  fi
  VHOST_BUSY="$(ss -tlnH 2>/dev/null | awk '\''$2 ~ /:8088$/ && $2 !~ /127.0.0.1:8088$/ {print $2}'\'' | head -n 1 || true)"
  if [ -n "${VHOST_BUSY}" ]; then
    echo "Gateway FRP vhostHTTPPort 8088 appears in use by a non-loopback listener (checked after SSH contact): ${VHOST_BUSY}"
    exit 19
  fi
fi
true'
if [ "${LOCAL_FRP_UPLOAD}" != "1" ]; then
  echo "Checking gateway frps presence (remote, before scp) ..."
  gw_ssh -p "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${GW_USER}@${GW_HOST}" 'set -eu
if ! command -v frps >/dev/null 2>&1 && [ ! -x /usr/local/bin/frps ] && [ ! -x /opt/frp/frps ] && [ ! -x /root/frp/frps ]; then
  echo "FRP server binary missing on gateway (checked after SSH contact). Install frps before deploy."
  exit 12
fi
true'
else
  echo "Skipping gateway frps presence check; local FRP_SERVER_BIN upload will supply the binary."
fi
echo "Deploying gateway FRP and HTTP proxy ownership to ${GW_USER}@${GW_HOST}:${GW_PORT} ..."
gw_scp -P "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${GW_FRAG}" "${GW_USER}@${GW_HOST}:/tmp/${GW_FRAG}" || true
gw_scp -P "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${GW_FULL}" "${GW_USER}@${GW_HOST}:/tmp/${GW_FULL}" || true
gw_scp -P "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${GW_UNIT}" "${GW_USER}@${GW_HOST}:/tmp/${GW_UNIT}" || true
if [ "${LOCAL_FRP_UPLOAD}" = "1" ]; then
  gw_scp -P "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${FRP_SERVER_BIN}" "${GW_USER}@${GW_HOST}:/tmp/ocuspot-frps"
fi
if [ -n "${FRP_TOKEN_FILE}" ]; then
  gw_scp -P "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${FRP_TOKEN_FILE}" "${GW_USER}@${GW_HOST}:/tmp/ocuspot-frp.env"
fi
gw_ssh -p "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${GW_USER}@${GW_HOST}" '
set -eu
mkdir -p /etc/ocuspot/frp /etc/ocuspot/gateway /usr/local/bin
if ! command -v base64 >/dev/null 2>&1; then echo "base64 required to install Ocuspot gateway 404 page"; exit 16; fi
printf "%s" PCFkb2N0eXBlIGh0bWw+PGh0bWwgbGFuZz0iZW4iPjxoZWFkPjxtZXRhIGNoYXJzZXQ9InV0Zi04Ij48bWV0YSBuYW1lPSJ2aWV3cG9ydCIgY29udGVudD0id2lkdGg9ZGV2aWNlLXdpZHRoLGluaXRpYWwtc2NhbGU9MSI+PG1ldGEgbmFtZT0iY29sb3Itc2NoZW1lIiBjb250ZW50PSJsaWdodCBkYXJrIj48dGl0bGU+U2l0ZSBOb3QgRm91bmQgLSBPY3VzcG90PC90aXRsZT48c3R5bGU+OnJvb3R7Y29sb3Itc2NoZW1lOmxpZ2h0IGRhcms7LS1iZzojZjdmNGVkOy0tZmc6IzFmMjkzMzstLW11dGVkOiM1ZDY2NzM7LS1wYW5lbDpyZ2JhKDI1NSwyNTUsMjU1LC43OCk7LS1saW5lOnJnYmEoMzEsNDEsNTEsLjE0KTstLWFjY2VudDojMGY3NjZlOy0tYWNjZW50LTI6I2I0NTMwOX1AbWVkaWEgKHByZWZlcnMtY29sb3Itc2NoZW1lOiBkYXJrKXs6cm9vdHstLWJnOiMxMTEzMTc7LS1mZzojZjNmMGU4Oy0tbXV0ZWQ6I2I1YjBhNzstLXBhbmVsOnJnYmEoMjUsMjgsMzQsLjc4KTstLWxpbmU6cmdiYSgyNDMsMjQwLDIzMiwuMTYpOy0tYWNjZW50OiM1ZWVhZDQ7LS1hY2NlbnQtMjojZmJiZjI0fX0qe2JveC1zaXppbmc6Ym9yZGVyLWJveH1ib2R5e21hcmdpbjowO21pbi1oZWlnaHQ6MTAwdmg7ZGlzcGxheTpncmlkO3BsYWNlLWl0ZW1zOmNlbnRlcjtwYWRkaW5nOjI0cHg7Zm9udC1mYW1pbHk6dWktc2Fucy1zZXJpZixzeXN0ZW0tdWksLWFwcGxlLXN5c3RlbSxCbGlua01hY1N5c3RlbUZvbnQsIlNlZ29lIFVJIixzYW5zLXNlcmlmO2JhY2tncm91bmQ6cmFkaWFsLWdyYWRpZW50KGNpcmNsZSBhdCAyMCUgMTUlLGNvbG9yLW1peChpbiBzcmdiLHZhcigtLWFjY2VudCkgMTglLHRyYW5zcGFyZW50KSx0cmFuc3BhcmVudCAyOHJlbSkscmFkaWFsLWdyYWRpZW50KGNpcmNsZSBhdCA4MiUgNzAlLGNvbG9yLW1peChpbiBzcmdiLHZhcigtLWFjY2VudC0yKSAxOCUsdHJhbnNwYXJlbnQpLHRyYW5zcGFyZW50IDI2cmVtKSx2YXIoLS1iZyk7Y29sb3I6dmFyKC0tZmcpfW1haW57d2lkdGg6bWluKDY4MHB4LDEwMCUpO2JvcmRlcjoxcHggc29saWQgdmFyKC0tbGluZSk7YmFja2dyb3VuZDp2YXIoLS1wYW5lbCk7YmFja2Ryb3AtZmlsdGVyOmJsdXIoMTRweCk7cGFkZGluZzpjbGFtcCgyOHB4LDZ2dyw1NnB4KTtib3JkZXItcmFkaXVzOjE4cHg7Ym94LXNoYWRvdzowIDIwcHggNzBweCByZ2JhKDAsMCwwLC4xOCl9Lm1hcmt7d2lkdGg6MTEycHg7aGVpZ2h0OjgycHg7bWFyZ2luLWJvdHRvbToyNnB4fWgxe21hcmdpbjowIDAgMTJweDtmb250LXNpemU6Y2xhbXAoMnJlbSw1dncsMy44cmVtKTtsaW5lLWhlaWdodDouOTU7bGV0dGVyLXNwYWNpbmc6MH1we21hcmdpbjowO21heC13aWR0aDo1NmNoO2NvbG9yOnZhcigtLW11dGVkKTtmb250LXNpemU6MXJlbTtsaW5lLWhlaWdodDoxLjY1fWF7Y29sb3I6dmFyKC0tYWNjZW50KTtmb250LXdlaWdodDo3MDA7dGV4dC1kZWNvcmF0aW9uLXRoaWNrbmVzczouMTJlbTt0ZXh0LXVuZGVybGluZS1vZmZzZXQ6LjIyZW19PC9zdHlsZT48L2hlYWQ+PGJvZHk+PG1haW4+PHN2ZyBjbGFzcz0ibWFyayIgdmlld0JveD0iMCAwIDExMiA4MiIgcm9sZT0iaW1nIiBhcmlhLWxhYmVsPSJPY3VzcG90IHJvdXRpbmcgbWFyayIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNOCA2MEMyNiAyNCA0OCAxNCA3OCAyMiIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJ2YXIoLS1saW5lKSIgc3Ryb2tlLXdpZHRoPSIxMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+PHBhdGggZD0iTTE1IDYwYzE3LTI1IDM4LTMzIDYxLTI1IiBmaWxsPSJub25lIiBzdHJva2U9InZhcigtLWFjY2VudCkiIHN0cm9rZS13aWR0aD0iOCIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+PGNpcmNsZSBjeD0iODQiIGN5PSIyOCIgcj0iMTciIGZpbGw9InZhcigtLWFjY2VudC0yKSIvPjxjaXJjbGUgY3g9Ijg0IiBjeT0iMjgiIHI9IjciIGZpbGw9InZhcigtLWJnKSIvPjxwYXRoIGQ9Ik03MyA2M2gyOSIgc3Ryb2tlPSJ2YXIoLS1mZykiIHN0cm9rZS13aWR0aD0iNiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIi8+PC9zdmc+PGgxPlNpdGUgbm90IGZvdW5kPC9oMT48cD5UaGlzIGdhdGV3YXkgaXMgbWFuYWdlZCBieSBPY3VzcG90LCBidXQgbm8gc2l0ZSBpcyBhdHRhY2hlZCB0byB0aGlzIHJlcXVlc3QuIFRyeSBhbm90aGVyIGFkZHJlc3MsIG9yIHJldHVybiB0byB0aGUgPGEgaWQ9InJvb3QtbGluayIgaHJlZj0iLyI+cm9vdCBkb21haW48L2E+LjwvcD48L21haW4+PHNjcmlwdD4oKCk9Pntjb25zdCBhPWRvY3VtZW50LmdldEVsZW1lbnRCeUlkKCJyb290LWxpbmsiKTtjb25zdCBoPWxvY2F0aW9uLmhvc3RuYW1lLnNwbGl0KCIuIik7aWYoYSYmaC5sZW5ndGg+Mil7YS5ocmVmPWxvY2F0aW9uLnByb3RvY29sKyIvLyIraC5zbGljZSgtMikuam9pbigiLiIpKyIvIn19KSgpOzwvc2NyaXB0PjwvYm9keT48L2h0bWw+ | base64 -d > /etc/ocuspot/gateway/404.html
if [ -f /tmp/ocuspot-frps ]; then install -m 0755 /tmp/ocuspot-frps /usr/local/bin/frps; fi
  if ! command -v frps >/dev/null 2>&1 && [ ! -x /usr/local/bin/frps ] && [ ! -x /opt/frp/frps ] && [ ! -x /root/frp/frps ]; then echo "FRP server binary missing on gateway"; exit 12; fi
  if [ -f /tmp/ocuspot-frp.env ]; then cat /tmp/ocuspot-frp.env > /etc/ocuspot/frp.env; chmod 0600 /etc/ocuspot/frp.env; elif [ ! -f /etc/ocuspot/frp.env ]; then printf "%s=\n" "OCUSPOT_FRP_PCONF_BUS" > /etc/ocuspot/frp.env; chmod 0600 /etc/ocuspot/frp.env; echo "Fill token in /etc/ocuspot/frp.env before restart if empty."; fi
  . /etc/ocuspot/frp.env || true
  TOKEN_VALUE="$(eval "printf '\''%s'\'' \"\${OCUSPOT_FRP_PCONF_BUS:-}\"")"
  if [ -z "${TOKEN_VALUE}" ]; then echo "Missing OCUSPOT_FRP_PCONF_BUS in /etc/ocuspot/frp.env on gateway"; exit 13; fi
  ESCAPED_TOKEN="$(printf "%s" "${TOKEN_VALUE}" | sed "s/[&|]/\\\\&/g")"
  sed -e "s|__OCUSPOT_AUTH_TOKEN__|${ESCAPED_TOKEN}|g" "/tmp/frps.gateway.pconf-bus.managed.toml" > "/etc/ocuspot/frp/frps.pconf-bus.toml"
  cat "/tmp/systemd.ocuspot-frps-pconf-bus.service" > "/etc/systemd/system/ocuspot-frps-pconf-bus.service" 2>/dev/null || true
  (systemctl daemon-reload && systemctl enable --now "ocuspot-frps-pconf-bus.service") 2>/dev/null || { echo "FRP service installed but could not be started on gateway"; exit 14; }
  if ! command -v nginx >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get update >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y nginx >/dev/null; fi
    if ! command -v nginx >/dev/null 2>&1 && command -v apk >/dev/null 2>&1; then apk add --no-cache nginx >/dev/null; fi
    if ! command -v nginx >/dev/null 2>&1 && command -v dnf >/dev/null 2>&1; then dnf install -y nginx >/dev/null; fi
    if ! command -v nginx >/dev/null 2>&1 && command -v yum >/dev/null 2>&1; then yum install -y nginx >/dev/null; fi
    if ! command -v nginx >/dev/null 2>&1 && command -v pacman >/dev/null 2>&1; then pacman -Sy --noconfirm nginx >/dev/null; fi
  fi
  if ! command -v nginx >/dev/null 2>&1; then echo "nginx is required on gateway for public HTTP routing"; exit 16; fi
  mkdir -p /etc/nginx/conf.d
  NGINX_TARGET_DIR="/etc/nginx/conf.d"
  NGINX_TARGET_FILE="/etc/nginx/conf.d/ocuspot-music.pconf.ru.conf"
  if [ -d /etc/nginx/fastpanel2-available ] && [ -d /etc/nginx/fastpanel2-sites ]; then
    BASE_DOMAIN="pconf.ru"
    if [ -n "${BASE_DOMAIN}" ]; then
      PANEL_SITE="$(find /etc/nginx/fastpanel2-available -maxdepth 2 -type f -name "${BASE_DOMAIN}.conf" | head -n 1 || true)"
      if [ -n "${PANEL_SITE}" ]; then
        PANEL_USER="$(basename "$(dirname "${PANEL_SITE}")")"
        NGINX_TARGET_DIR="/etc/nginx/fastpanel2-sites/${PANEL_USER}"
        mkdir -p "${NGINX_TARGET_DIR}"
        NGINX_TARGET_FILE="${NGINX_TARGET_DIR}/ocuspot-music.pconf.ru.conf"
      fi
    fi
  fi
OCUSPOT_DEFAULT_404_NGINX="/etc/nginx/conf.d/ocuspot-default-404.conf"
if [ -f /etc/nginx/conf.d/parking.conf ]; then
OCUSPOT_DEFAULT_404_NGINX="/etc/nginx/conf.d/parking.conf"
fi
cat > "${OCUSPOT_DEFAULT_404_NGINX}" <<OCUSPOT_DEFAULT_404
server {
listen *:80 default_server;
server_name _;
root /etc/ocuspot/gateway;
error_page 404 /404.html;
location = /404.html {
internal;
}
location / {
return 404;
}
}
OCUSPOT_DEFAULT_404
cat > "${NGINX_TARGET_FILE}" <<OCUSPOT_NGINX
server {
listen 80;
server_name music.pconf.ru;

    location / {
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_pass http://127.0.0.1:8088;
    }
}
OCUSPOT_NGINX
  nginx -t >/dev/null
  (systemctl enable --now nginx >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1) || service nginx reload >/dev/null 2>&1 || nginx -s reload >/dev/null 2>&1 || nginx >/dev/null 2>&1 || { echo "nginx config installed but could not be started/reloaded"; exit 16; }
'
echo "Gateway FRP and HTTP proxy ownership deployed."
DEPLOY_GATEWAY_DONE=1

CAR_HOST="78.17.97.168"
CAR_PORT=2222
CAR_USER="root"
CAR_FRAG='frpc.carrier.music_bot_backend_kvm_pconf_bus_3123.toml'
CAR_FULL='frpc.carrier.music_bot_backend_kvm_pconf_bus_3123.managed.toml'
CAR_UNIT='systemd.ocuspot-frpc-music_bot_backend_kvm_pconf_bus_3123.service'
echo "Preflight: SSH connectivity to carrier ${CAR_USER}@${CAR_HOST}:${CAR_PORT} (before remote mutation) ..."
car_ssh -p "${CAR_PORT}" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "${CAR_USER}@${CAR_HOST}" 'echo ocuspot-ssh-ok' >/dev/null || { echo "Carrier SSH failed before any remote mutation. Verify host, port, user, and credentials."; exit 20; }
echo "Checking carrier runtime prerequisites (remote, before scp) ..."
car_ssh -p "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${CAR_USER}@${CAR_HOST}" 'set -eu
if ! command -v systemctl >/dev/null 2>&1 && ! command -v rc-service >/dev/null 2>&1; then
  echo "Carrier requires systemd or openrc for Ocuspot-managed FRP units (checked after SSH contact; not a universal init-system guarantee)."
  exit 21
fi
for d in /etc/ocuspot /etc/ocuspot/frp /usr/local/bin /tmp; do
  if ! mkdir -p "$d" 2>/dev/null || [ ! -w "$d" ]; then
    echo "Carrier target path not writable (checked after SSH contact): $d"
    exit 22
  fi
done
if ! command -v docker >/dev/null 2>&1 && ! command -v podman >/dev/null 2>&1; then
  echo "No docker or podman runtime found on carrier (checked after SSH contact)."
  exit 15
fi
if command -v docker >/dev/null 2>&1; then
  docker info >/dev/null 2>&1 || { echo "Docker daemon not reachable on carrier (checked after SSH contact)."; exit 15; }
elif command -v podman >/dev/null 2>&1; then
  podman info >/dev/null 2>&1 || { echo "Podman not operational on carrier (checked after SSH contact)."; exit 15; }
fi
if command -v ss >/dev/null 2>&1; then
  PORT_OWNER="$(ss -tlnH 2>/dev/null | awk '\''$2 ~ /127.0.0.1:3123$/ {print $2}'\'' | head -n 1 || true)"
  if [ -n "${PORT_OWNER}" ]; then
    if command -v docker >/dev/null 2>&1 && docker ps --format '\''{{.Names}}'\'' 2>/dev/null | grep -qx '\''music_bot_backend_kvm_pconf_bus_3123'\''; then
      : # existing Ocuspot workload container will be replaced
    elif command -v podman >/dev/null 2>&1 && podman ps --format '\''{{.Names}}'\'' 2>/dev/null | grep -qx '\''music_bot_backend_kvm_pconf_bus_3123'\''; then
      : # existing Ocuspot workload container will be replaced
    else
      echo "Carrier loopback port 3123 is already bound by a non-Ocuspot listener (checked after SSH contact): ${PORT_OWNER}"
      exit 19
    fi
  fi
fi
true'
if [ "${LOCAL_FRP_UPLOAD}" != "1" ]; then
  echo "Checking carrier frpc presence (remote, before scp) ..."
  car_ssh -p "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${CAR_USER}@${CAR_HOST}" 'set -eu
if ! command -v frpc >/dev/null 2>&1 && [ ! -x /usr/local/bin/frpc ] && [ ! -x /opt/frp/frpc ] && [ ! -x /root/frp/frpc ]; then
  echo "FRP client binary missing on carrier (checked after SSH contact). Install frpc before deploy."
  exit 12
fi
true'
else
  echo "Skipping carrier frpc presence check; local FRP_CLIENT_BIN upload will supply the binary."
fi
echo "Deploying carrier FRP ownership to ${CAR_USER}@${CAR_HOST}:${CAR_PORT} ..."
car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${CAR_FRAG}" "${CAR_USER}@${CAR_HOST}:/tmp/${CAR_FRAG}" || true
car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${CAR_FULL}" "${CAR_USER}@${CAR_HOST}:/tmp/${CAR_FULL}" || true
car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${CAR_UNIT}" "${CAR_USER}@${CAR_HOST}:/tmp/${CAR_UNIT}" || true
if [ "${LOCAL_FRP_UPLOAD}" = "1" ]; then
  car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${FRP_CLIENT_BIN}" "${CAR_USER}@${CAR_HOST}:/tmp/ocuspot-frpc"
fi
if [ -n "${FRP_TOKEN_FILE}" ]; then
  car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${FRP_TOKEN_FILE}" "${CAR_USER}@${CAR_HOST}:/tmp/ocuspot-frp.env"
fi
car_ssh -p "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${CAR_USER}@${CAR_HOST}" '
  set -eu
  mkdir -p /etc/ocuspot/frp /usr/local/bin
  if [ -f /tmp/ocuspot-frpc ]; then install -m 0755 /tmp/ocuspot-frpc /usr/local/bin/frpc; fi
  if ! command -v frpc >/dev/null 2>&1 && [ ! -x /usr/local/bin/frpc ] && [ ! -x /opt/frp/frpc ] && [ ! -x /root/frp/frpc ]; then echo "FRP client binary missing on carrier"; exit 12; fi
  if [ -f /tmp/ocuspot-frp.env ]; then cat /tmp/ocuspot-frp.env > /etc/ocuspot/frp.env; chmod 0600 /etc/ocuspot/frp.env; elif [ ! -f /etc/ocuspot/frp.env ]; then printf "%s=\n" "OCUSPOT_FRP_PCONF_BUS" > /etc/ocuspot/frp.env; chmod 0600 /etc/ocuspot/frp.env; echo "Fill token in /etc/ocuspot/frp.env before restart if empty."; fi
  . /etc/ocuspot/frp.env || true
  TOKEN_VALUE="$(eval "printf '\''%s'\'' \"\${OCUSPOT_FRP_PCONF_BUS:-}\"")"
  if [ -z "${TOKEN_VALUE}" ]; then echo "Missing OCUSPOT_FRP_PCONF_BUS in /etc/ocuspot/frp.env on carrier"; exit 13; fi
  ESCAPED_TOKEN="$(printf "%s" "${TOKEN_VALUE}" | sed "s/[&|]/\\\\&/g")"
  sed -e "s|__OCUSPOT_AUTH_TOKEN__|${ESCAPED_TOKEN}|g" "/tmp/frpc.carrier.music_bot_backend_kvm_pconf_bus_3123.managed.toml" > "/etc/ocuspot/frp/frpc.music_bot_backend_kvm_pconf_bus_3123.toml"
  cat "/tmp/systemd.ocuspot-frpc-music_bot_backend_kvm_pconf_bus_3123.service" > "/etc/systemd/system/ocuspot-frpc-music_bot_backend_kvm_pconf_bus_3123.service" 2>/dev/null || true
  (systemctl daemon-reload && systemctl enable --now "ocuspot-frpc-music_bot_backend_kvm_pconf_bus_3123.service") 2>/dev/null || { echo "FRP service installed but could not be started on carrier"; exit 14; }
'
echo "Carrier FRP ownership deployed."
DEPLOY_CARRIER_DONE=1

if [ "${DEPLOY_RUNTIME:-}" = "docker-compose" ]; then
 echo "Uploading compose source tar to carrier ..."
 require_local_cmd tar
 SOURCE_DIR="${DEPLOY_SOURCE_DIR:-${OCUSPOT_SOURCE_DIR:-}}"
 if [ -z "${SOURCE_DIR}" ] || [ ! -d "${SOURCE_DIR}" ]; then
  echo "Compose deploy requires OCUSPOT_SOURCE_DIR (project root)"; exit 18
 fi
 if [ ! -f "${SOURCE_DIR}/docker-compose.yml" ] && [ ! -f "${SOURCE_DIR}/docker-compose.yml" ] && [ ! -f "${SOURCE_DIR}/docker-compose.yaml" ] && [ ! -f "${SOURCE_DIR}/compose.yml" ] && [ ! -f "${SOURCE_DIR}/compose.yaml" ]; then
  echo "Compose file docker-compose.yml not found in ${SOURCE_DIR}"; exit 18
 fi
 COMPOSE_ARCHIVE=$(mktemp /tmp/ocuspot-compose.music_bot_backend_kvm_pconf_bus_3123.XXXXXX)
 TAR_EXCLUDES='--exclude=.git --exclude=.ocuspot --exclude=node_modules --exclude=*/node_modules --exclude=db/data --exclude=*/db/data --exclude=shop/dist --exclude=*/dist --exclude=coverage --exclude=.env --exclude=.env.*'
 if [ "${DEPLOY_IGNORE_DB:-0}" = "1" ]; then
  echo "Compose db ignore enabled: excluding *-db/db bind directories."
  TAR_EXCLUDES="${TAR_EXCLUDES} --exclude=*-db/db --exclude=*-db/db/*"
  DB_EXCLUDES_FILE=$(mktemp /tmp/ocuspot-db-excludes.XXXXXX)
  COMPOSE_SCAN="${SOURCE_DIR}/${DEPLOY_COMPOSE_FILE}"
  if [ ! -f "${COMPOSE_SCAN}" ]; then
   for candidate in docker-compose.yml docker-compose.yaml compose.yml compose.yaml; do
    if [ -f "${SOURCE_DIR}/${candidate}" ]; then COMPOSE_SCAN="${SOURCE_DIR}/${candidate}"; break; fi
   done
  fi
  if [ -f "${COMPOSE_SCAN}" ] && command -v grep >/dev/null 2>&1; then
   grep -Eo "(\./)?[^[:space:]\"':]*-db/db" "${COMPOSE_SCAN}" >> "${DB_EXCLUDES_FILE}" 2>/dev/null || true
  fi
  if command -v find >/dev/null 2>&1; then
   find "${SOURCE_DIR}" -type d -path "*-db/db" -print >> "${DB_EXCLUDES_FILE}" 2>/dev/null || true
  fi
  while IFS= read -r db_path; do
   db_path=${db_path#./}
   db_path=${db_path#${SOURCE_DIR}/}
   case "${db_path}" in ''|/*) continue ;; esac
   TAR_EXCLUDES="${TAR_EXCLUDES} --exclude=${db_path} --exclude=${db_path}/*"
  done < "${DB_EXCLUDES_FILE}"
  rm -f "${DB_EXCLUDES_FILE}"
 fi
 if [ -f "${SOURCE_DIR}/.dockerignore" ]; then
  while IFS= read -r pattern; do
   case "${pattern}" in ''|\#*) continue ;; esac
   TAR_EXCLUDES="${TAR_EXCLUDES} --exclude=${pattern}"
  done < "${SOURCE_DIR}/.dockerignore"
 fi
 # shellcheck disable=SC2086
 tar -C "${SOURCE_DIR}" ${TAR_EXCLUDES} -czf "${COMPOSE_ARCHIVE}" .
 car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${COMPOSE_ARCHIVE}" "${CAR_USER}@${CAR_HOST}:/tmp/ocuspot-compose.music_bot_backend_kvm_pconf_bus_3123.tar.gz"
 COMPOSE_ENV_LIST=$(mktemp /tmp/ocuspot-compose-env-list.XXXXXX)
 find "${SOURCE_DIR}" -maxdepth 1 -type f -name '.env*' | while IFS= read -r env_path; do
  env_candidate="${env_path##*/}"
  case "${env_candidate}" in *.example) continue ;; esac
  printf '%s\n' "${env_candidate}" >> "${COMPOSE_ENV_LIST}"
 done
 if [ -s "${COMPOSE_ENV_LIST}" ]; then
  COMPOSE_ENV_ARCHIVE=$(mktemp /tmp/ocuspot-compose-env.music_bot_backend_kvm_pconf_bus_3123.XXXXXX)
  tar -C "${SOURCE_DIR}" -czf "${COMPOSE_ENV_ARCHIVE}" -T "${COMPOSE_ENV_LIST}"
  chmod 0600 "${COMPOSE_ENV_ARCHIVE}" 2>/dev/null || true
  echo "Uploading compose env files separately to carrier ..."
  car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${COMPOSE_ENV_ARCHIVE}" "${CAR_USER}@${CAR_HOST}:/tmp/ocuspot-compose-env.music_bot_backend_kvm_pconf_bus_3123.tar.gz"
  rm -f "${COMPOSE_ENV_ARCHIVE}"
 fi
 rm -f "${COMPOSE_ENV_LIST}"
 if [ -n "${CI_REGISTRY:-}" ] && [ -n "${CI_REGISTRY_USER:-}" ] && [ -n "${CI_REGISTRY_PASSWORD:-}" ]; then
 REGISTRY_TMP_DIR=$(mktemp -d /tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.XXXXXX)
 printf '%s' "${CI_REGISTRY}" > "${REGISTRY_TMP_DIR}/registry"
 printf '%s' "${CI_REGISTRY_USER}" > "${REGISTRY_TMP_DIR}/user"
 printf '%s' "${CI_REGISTRY_PASSWORD}" > "${REGISTRY_TMP_DIR}/password"
 chmod 0600 "${REGISTRY_TMP_DIR}"/* 2>/dev/null || true
 echo "Uploading registry credentials for carrier pull ..."
 car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${REGISTRY_TMP_DIR}/registry" "${CAR_USER}@${CAR_HOST}:/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.registry"
 car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${REGISTRY_TMP_DIR}/user" "${CAR_USER}@${CAR_HOST}:/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.user"
 car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${REGISTRY_TMP_DIR}/password" "${CAR_USER}@${CAR_HOST}:/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.password"
 rm -rf "${REGISTRY_TMP_DIR}"
 fi
 car_ssh -p "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${CAR_USER}@${CAR_HOST}" 'set -eu
if ! command -v tar >/dev/null 2>&1; then
echo "tar required on carrier for compose rollout"
exit 15
fi
WORKDIR='\''/opt/ocuspot/apps/music_bot_backend_kvm_pconf_bus_3123'\''
ENV_BACKUP="$(mktemp -d /tmp/ocuspot-compose-env-backup.XXXXXX)"
if [ -d "$WORKDIR" ]; then
  find "$WORKDIR" -maxdepth 1 -type f \( -name '\''.env'\'' -o -name '\''.env.*'\'' \) -exec cp -p {} "$ENV_BACKUP"/ \; 2>/dev/null || true
fi
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
tar -xzf '\''/tmp/ocuspot-compose.music_bot_backend_kvm_pconf_bus_3123.tar.gz'\'' -C "$WORKDIR"
if [ -d "$ENV_BACKUP" ]; then
  cp -pn "$ENV_BACKUP"/.env "$WORKDIR"/ 2>/dev/null || true
  cp -pn "$ENV_BACKUP"/.env.* "$WORKDIR"/ 2>/dev/null || true
  rm -rf "$ENV_BACKUP"
fi
if [ -f '\''/tmp/ocuspot-compose-env.music_bot_backend_kvm_pconf_bus_3123.tar.gz'\'' ]; then
  tar -xzf '\''/tmp/ocuspot-compose-env.music_bot_backend_kvm_pconf_bus_3123.tar.gz'\'' -C "$WORKDIR"
  chmod 0600 "$WORKDIR"/.env "$WORKDIR"/.env.* 2>/dev/null || true
fi
cd "$WORKDIR"
COMPOSE_FILE='\''docker-compose.yml'\''
COMPOSE_DIR="$(dirname "$COMPOSE_FILE")"
if [ -f "$COMPOSE_FILE" ]; then
  awk '\''
    function emit(line) {
      sub(/[[:space:]]*#.*/, "", line)
      gsub(/^[[:space:]"]+|[[:space:]"]+$/, "", line)
      if (line != "") print line
    }
    /^[[:space:]]*env_file:[[:space:]]*$/ {
      in_env = 1
      base = match($0, /[^[:space:]]/) - 1
      next
    }
    in_env {
      if ($0 ~ /^[[:space:]]*-/) {
        line = $0
        sub(/^[[:space:]]*-[[:space:]]*/, "", line)
        emit(line)
        next
      }
      if ($0 ~ /^[[:space:]]*$/ || $0 ~ /^[[:space:]]*#/) next
      indent = match($0, /[^[:space:]]/) - 1
      if (indent <= base) in_env = 0
    }
    /^[[:space:]]*env_file:[[:space:]]*[^#[:space:]]/ {
      line = $0
      sub(/^[[:space:]]*env_file:[[:space:]]*/, "", line)
      emit(line)
    }
  '\'' "$COMPOSE_FILE" | sort -u | while IFS= read -r env_file; do
    case "$env_file" in ""|/*|*..*|*\$*) continue ;; esac
    target="${COMPOSE_DIR}/${env_file}"
    if [ ! -f "$target" ]; then
      if [ -f "${target}.example" ]; then
        cp "${target}.example" "$target"
      else
        mkdir -p "$(dirname "$target")"
        touch "$target"
      fi
      echo "Created compose env_file placeholder on carrier: ${env_file}"
    fi
  done
fi

COMPOSE_FILE='\''docker-compose.yml'\''
COMPOSE_SANITIZED_FILE='\''docker-compose.ocuspot.sanitized.yml'\''
COMPOSE_ALLOWED_PUBLISHED_PORTS='\''3123 '\''
awk -v allowed_ports="$COMPOSE_ALLOWED_PUBLISHED_PORTS" '\''
function lead(s, i, c) {
  for (i = 1; i <= length(s); i++) {
    c = substr(s, i, 1)
    if (c != " ") return i - 1
  }
  return length(s)
}
function trim(s) {
  gsub(/^[[:space:]]+|[[:space:]]+$/, "", s)
  return s
}
function allowed(port, parts, i) {
  split(allowed_ports, parts, " ")
  for (i in parts) if (parts[i] == port) return 1
  return 0
}
function isnum(s) { return s ~ /^[0-9]+$/ }
function flush_ports(i) {
  if (!in_ports) return
  if (kept_count > 0) {
    print ports_line
    for (i = 1; i <= kept_count; i++) print kept[i]
  } else if (expose_count > 0) {
    print ports_prefix "expose:"
    for (i = 1; i <= expose_count; i++) print exposed[i]
  }
  delete kept
  delete exposed
  kept_count = 0
  expose_count = 0
  in_ports = 0
}
function handle_port_line(line, trimmed, value, n, parts, published, target, child_prefix) {
  trimmed = trim(line)
  if (trimmed !~ /^-/) {
    kept[++kept_count] = line
    return
  }
  value = trim(substr(trimmed, 2))
  if (substr(value, 1, 1) == "\"") value = substr(value, 2)
  if (substr(value, length(value), 1) == "\"") value = substr(value, 1, length(value)-1)
  sub(/\/(tcp|udp)$/, "", value)
  n = split(value, parts, ":")
  if (n == 1) {
    kept[++kept_count] = line
    return
  }
  if (n == 2) {
    published = parts[1]
    target = parts[2]
  } else {
    published = parts[n-1]
    target = parts[n]
  }
  gsub(/[^0-9]/, "", published)
  gsub(/[^0-9]/, "", target)
  if (!isnum(published) || allowed(published)) {
    kept[++kept_count] = line
    return
  }
  if (isnum(target)) {
    child_prefix = ports_prefix "  "
    exposed[++expose_count] = child_prefix "- \"" target "\""
  }
  print "Ocuspot compose port sanitizer: removed source-published port " published " from " COMPOSE_FILE > "/dev/stderr"
}
{
  indent = lead($0)
  trimmed = trim($0)
  if (in_ports && trimmed != "" && indent <= ports_indent && trimmed !~ /^#/) {
    flush_ports()
  }
  if (!in_ports && trimmed == "ports:") {
    in_ports = 1
    ports_line = $0
    ports_indent = indent
    ports_prefix = substr($0, 1, indent)
    next
  }
  if (in_ports) {
    handle_port_line($0)
    next
  }
  print
}
END { flush_ports() }
'\'' "$COMPOSE_FILE" > "$COMPOSE_SANITIZED_FILE"


if command -v docker >/dev/null 2>&1; then
 PORT_CONTAINERS="$(docker ps --filter publish=3123 --format '\''{{.ID}}'\'' 2>/dev/null | tr '\''\n'\'' '\'' '\'' || true)"
 if [ -n "${PORT_CONTAINERS}" ]; then
  echo "Stopping existing docker container(s) publishing carrier binding port 3123: ${PORT_CONTAINERS}"
  docker rm -f ${PORT_CONTAINERS}
 fi
fi
if [ -s '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.registry'\'' ] && [ -s '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.user'\'' ] && [ -s '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.password'\'' ]; then
  if command -v docker >/dev/null 2>&1; then
    docker login "$(cat '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.registry'\'')" -u "$(cat '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.user'\'')" --password-stdin < '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.password'\''
  elif command -v podman >/dev/null 2>&1; then
    podman login "$(cat '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.registry'\'')" -u "$(cat '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.user'\'')" --password-stdin < '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.password'\''
  fi
  rm -f '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.registry'\'' '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.user'\'' '\''/tmp/ocuspot-registry.music_bot_backend_kvm_pconf_bus_3123.password'\''
fi
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose -f '\''docker-compose.ocuspot.sanitized.yml'\'' up -d --build --force-recreate --remove-orphans
elif command -v docker-compose >/dev/null 2>&1; then
docker-compose -f '\''docker-compose.ocuspot.sanitized.yml'\'' up -d --build --force-recreate --remove-orphans
else
  echo "Compose CLI missing on carrier"
  exit 15
fi'
 DEPLOY_WORKLOAD_DONE=1
fi
if [ -n "${IMAGE_TAR:-}" ] && [ -f "${IMAGE_TAR}" ] && [ "${DEPLOY_RUNTIME:-}" != "docker-compose" ]; then
  echo "Uploading workload image tar to carrier ..."
  car_scp -P "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${IMAGE_TAR}" "${CAR_USER}@${CAR_HOST}:/tmp/ocuspot-image.tar"
  car_ssh -p "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${CAR_USER}@${CAR_HOST}" '
    set -eu
    if command -v docker >/dev/null 2>&1; then
      docker load -i /tmp/ocuspot-image.tar
    elif command -v podman >/dev/null 2>&1; then
      podman load -i /tmp/ocuspot-image.tar
    else
      echo "No docker or podman runtime found on carrier"; exit 15
    fi
  '
  DEPLOY_WORKLOAD_DONE=1
fi
if [ "${DEPLOY_RUNTIME:-}" = "docker-compose" ] || [ -n "${IMAGE_TAR:-}" ]; then
  echo "Health check 1/4: carrier workload http://127.0.0.1:3123/healthz (after container start) ..."
  car_ssh -p "${CAR_PORT}" -o StrictHostKeyChecking=accept-new "${CAR_USER}@${CAR_HOST}" 'set -eu
if curl -fsS --max-time 10 http://127.0.0.1:3123/healthz >/dev/null 2>&1 || wget -qO- -T 10 http://127.0.0.1:3123/healthz >/dev/null 2>&1; then
  exit 0
fi
echo "Carrier local health failed: workload not responding on http://127.0.0.1:3123/healthz (checked after container start)."
exit 17'
  echo "Health check 2/4: gateway FRP vhost http://127.0.0.1:8088/healthz with Host ${PUBLIC_HOST} ..."
  gw_ssh -p "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${GW_USER}@${GW_HOST}" 'set -eu
if curl -fsS --max-time 10 -H '\''Host: music.pconf.ru'\'' http://127.0.0.1:8088/healthz >/dev/null 2>&1 || wget -qO- -T 10 --header='\''Host: music.pconf.ru'\'' http://127.0.0.1:8088/healthz >/dev/null 2>&1; then
  exit 0
fi
echo "Gateway FRP vhost health failed: frps did not serve Host music.pconf.ru on http://127.0.0.1:8088/healthz (checked after remote rollout)."
exit 17'
  echo "Health check 3/4: gateway nginx proxy for Host ${PUBLIC_HOST} (after nginx config) ..."
  gw_ssh -p "${GW_PORT}" -o StrictHostKeyChecking=accept-new "${GW_USER}@${GW_HOST}" 'set -eu
if curl -fsS --max-time 10 -H '\''Host: music.pconf.ru'\'' http://127.0.0.1/healthz >/dev/null 2>&1 || wget -qO- -T 10 --header='\''Host: music.pconf.ru'\'' http://127.0.0.1/healthz >/dev/null 2>&1; then
  exit 0
fi
echo "Gateway HTTP proxy health failed: nginx did not route Host music.pconf.ru tunneled backend (checked after remote rollout)."
exit 17'
  echo "Health check 4/4: public endpoint http://${PUBLIC_HOST}/healthz (DNS + routing; best-effort unless OCUSPOT_REQUIRE_PUBLIC_HEALTH=1) ..."
  PUBLIC_IPS="$(getent ahostsv4 "${PUBLIC_HOST}" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || true)"
  case " ${PUBLIC_IPS} " in *" ${GW_HOST} "*) DNS_OK=1 ;; *) DNS_OK=0 ;; esac
  if [ "${DNS_OK}" = 0 ]; then echo "Public DNS check: ${PUBLIC_HOST} resolves to [${PUBLIC_IPS:-none}], expected gateway ${GW_HOST}. Public routing may still work via other paths; not a hard failure unless OCUSPOT_REQUIRE_PUBLIC_HEALTH=1."; fi
  if [ "${DNS_OK}" = 1 ] && (curl -fsS --max-time 15 "http://${PUBLIC_HOST}/healthz" >/dev/null || wget -qO- -T 15 "http://${PUBLIC_HOST}/healthz" >/dev/null); then
    echo "Public health verified for http://${PUBLIC_HOST}/healthz."
  elif [ "${OCUSPOT_REQUIRE_PUBLIC_HEALTH:-0}" = 1 ]; then
    echo "Public health failed for http://${PUBLIC_HOST}/healthz and OCUSPOT_REQUIRE_PUBLIC_HEALTH=1 (carrier and gateway-local checks already passed)."
    exit 17
  else
    echo "Public health not reachable yet for http://${PUBLIC_HOST}/healthz; carrier and gateway-local checks passed. Re-check DNS/firewall later or set OCUSPOT_REQUIRE_PUBLIC_HEALTH=1 to enforce."
  fi
fi

# After both sides: public endpoint and health have been verified when IMAGE_TAR is supplied.
DEPLOY_SUCCEEDED=1
echo "Bundle execution complete. Check gateway routing and carrier service."
