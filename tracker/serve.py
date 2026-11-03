"""Runs the webhook server. Settings come from the environment, and a bad one stops it here."""

import logging
import os
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from typing import Final

import uvicorn

from tracker.channel.settings import Settings
from tracker.channel.webhook import create_app

DEFAULT_BIND_HOST: Final = "127.0.0.1"
# Inside the container the app must listen beyond loopback, but compose publishes the
# port to the server's loopback only. These two are the only choices.
ALLOWED_BIND_HOSTS: Final = frozenset({DEFAULT_BIND_HOST, "0.0.0.0"})  # noqa: S104 - see above.
BIND_PORT: Final = 8099
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s %(message)s"
LOG_FILE_BYTES: Final = 5 * 1024 * 1024
LOG_FILE_BACKUPS: Final = 3


def bind_host(environment: Mapping[str, str]) -> str:
    host = environment.get("TRACKER_BIND_HOST", DEFAULT_BIND_HOST)
    if host not in ALLOWED_BIND_HOSTS:
        raise ValueError(f"TRACKER_BIND_HOST must be one of {sorted(ALLOWED_BIND_HOSTS)}, not {host!r}")
    return host


def server_config(settings: Settings, host: str = DEFAULT_BIND_HOST) -> uvicorn.Config:
    """Access logs stay off: a URL can carry what shouldn't be kept (T6)."""
    return uvicorn.Config(
        create_app(settings), host=host, port=BIND_PORT, access_log=False, server_header=False, date_header=False
    )


def configure_logging(environment: Mapping[str, str]) -> None:
    """Stdout always, and a rotating file when TRACKER_LOG_FILE is set, for the monitoring
    stack to read without the Docker socket. The lines hold no content (T14)."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = environment.get("TRACKER_LOG_FILE")
    if log_file:
        handlers.append(RotatingFileHandler(log_file, maxBytes=LOG_FILE_BYTES, backupCount=LOG_FILE_BACKUPS))
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=handlers, force=True)


def main() -> None:
    configure_logging(os.environ)
    uvicorn.Server(server_config(Settings.from_environment(os.environ), bind_host(os.environ))).run()


if __name__ == "__main__":
    main()
