# Ocuspot deployment

Project: Music-Bot

The built-in project contract is container-first. The default runtime is a
single OCI image built from a local Dockerfile and shipped by GitLab CI.
.ocuspot/deploy/deploy.sh is generated from the private control registry
(registry.json bindings) and manages FRP on both the gateway and carrier
for this project only.

## Real deployment paths

GitLab CI (after build) and the advanced local operator fallback 'ocuspot deploy apply'
both execute .ocuspot/deploy/deploy.sh.
GitLab CI then runs .ocuspot/deploy/publish-deployments.sh so the authoritative
control-repo deployments.json can advance without shipping the ocuspot binary into CI.
This repository contains no Ocuspot executable.

## Runtime contract

- Runtime: docker-compose
- Service port behind FRP: 3123
- Compose file: docker-compose.yml
- Service directories: (not declared)
- Built-in deploy executor supports docker-image via IMAGE_TAR and docker-compose
  via source archive upload plus remote compose build.
- Compose/microservice repositories declare root compose file, per-service
  Dockerfiles, and per-service src dirs validated by CI.

## CI / operator credentials

Configure masked/protected GitLab CI variables (or supply when running deploy apply):

- OCUSPOT_SSH_PRIVATE_KEY: SSH key for carrier and gateway.
- OCUSPOT_CARRIER_SSH_PASSWORD / OCUSPOT_GATEWAY_SSH_PASSWORD: optional password fallback.
- FRP_SERVER_BIN / FRP_CLIENT_BIN: optional local frps/frpc binaries uploaded to hosts when missing.
- IMAGE_TAR: docker/podman save tarball for workload rollout. GitLab CI sets this
  from the build-image artifact (ocuspot-image.tar) automatically.

The generated .gitlab-ci.yml is language-agnostic:

- verify-container-contract: always runs and explains the container contract.
- validate-compose: runs only when a compose file exists.
- build-image: runs only when a Dockerfile exists.
- build-compose-images: runs when runtime metadata is compose-oriented.
- deploy-carrier: runs only when AutoDeploy is enabled and a Dockerfile exists.

## FRP operator expectations

deploy.sh owns Ocuspot-managed resources on the remote hosts:

- /etc/ocuspot/frp/*.toml and /etc/ocuspot/frp.env (shared token file)
- systemd units ocuspot-frps-* and ocuspot-frpc-*
- gateway nginx conf.d/ocuspot-*.conf and the carrier workload container

Each gateway uses OCUSPOT_FRP_<GATEWAY> in configs (never a literal token).
On first run, deploy.sh may create /etc/ocuspot/frp.env with an empty value;
fill the token before services restart, or export the env var in the shell
running deploy.sh so it is copied remotely.

If hosts lack frps/frpc, set FRP_SERVER_BIN and FRP_CLIENT_BIN locally before running deploy.sh.

IMAGE_TAR is required for a real workload rollout. Set OCUSPOT_ALLOW_CONFIG_ONLY=1
only for intentional FRP/nginx-only changes without loading a container image.

On failure after partial rollout, deploy.sh attempts best-effort cleanup (not a
guaranteed universal rollback).

## Authoritative deployments.json

Manifest updates happen only after deploy.sh succeeds fully:

- GitLab CI is the intended product deploy path. Local 'ocuspot deploy apply'
  remains an operator fallback/debug surface.
- GitLab CI runs publish-deployments.sh after successful deploy.runtime.sh so
  deployments.json publication stays binary-free.
- 'ocuspot deploy apply' updates local cached deployments.json and publishes the
  GitLab control-repo deployments.json.
- 'deploy bundle --apply' and 'deploy configure --apply' update local cache only.
- GitLab CI publishes deployments.json through the generated shell path and
  requires OCUSPOT_GITLAB_TOKEN in project CI variables.
