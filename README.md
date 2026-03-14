# MusicBot Backend

This branch contains only the backend service source and its Docker build context.

## Image

CI publishes:
- `ghcr.io/alberto-kali/musicbot-backend:latest`
- `ghcr.io/alberto-kali/musicbot-backend:sha-<commit>`

Release changes for production are made separately in the `main` branch by updating `docker-compose.yml`.
