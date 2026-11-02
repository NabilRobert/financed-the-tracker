"""Runs the webhook server. Settings come from the environment, and a bad one stops it here."""

import os
from typing import Final

import uvicorn

from tracker.channel.settings import Settings
from tracker.channel.webhook import create_app

BIND_HOST: Final = "127.0.0.1"  # Caddy sits in front. The app is never exposed directly.
BIND_PORT: Final = 8099


def server_config(settings: Settings) -> uvicorn.Config:
    """Access logs stay off: Meta's verification request carries the token in its URL (T6)."""
    return uvicorn.Config(
        create_app(settings), host=BIND_HOST, port=BIND_PORT, access_log=False, server_header=False, date_header=False
    )


def main() -> None:
    uvicorn.Server(server_config(Settings.from_environment(os.environ))).run()


if __name__ == "__main__":
    main()
