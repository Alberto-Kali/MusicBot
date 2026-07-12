# MusicBot Runtime Branch

This `main` branch is the runtime and release branch for `MusicBot`.

It contains only:
- `docker-compose.yml` for deployment
- `.env.example` templates
- GitHub policy and CI files in `.github/`

Service source code lives in long-lived branches and sibling worktrees:
- `backend`
- `telegram-bot`
- `telegram-webapp`

## Release flow

1. Open a PR into a service branch.
2. After merge, CI builds and pushes the service image to GHCR.
3. Open a separate PR into `main` and bump the image tag in `docker-compose.yml` to the new immutable `sha-<commit>` tag.

## Secrets

Real secrets must never be committed.

Use local runtime files only:
- `.env`
- `.env.secrets.backend`
- `.env.secrets.bot`

The repository previously exposed real credentials and session material. Those values must be rotated outside git after this migration.
