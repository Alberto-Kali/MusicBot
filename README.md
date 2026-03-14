# MusicBot Telegram Bot

This branch contains only the Telegram bot service source and its Docker build context.

## Image

CI publishes:
- `ghcr.io/alberto-kali/musicbot-telegram-bot:latest`
- `ghcr.io/alberto-kali/musicbot-telegram-bot:sha-<commit>`

Release changes for production are made separately in the `main` branch by updating `docker-compose.yml`.
