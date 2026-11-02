"""The app's settings, which refuse to load rather than run unsafely.

A missing, blank or placeholder secret stops startup, so signature checks can never be
quietly off (R1, T1). A WAHA address that isn't private stops it too: an exposed WAHA API
is someone else's control of the account, and bots have done exactly that (T36).
"""

import ipaddress
from collections.abc import Mapping
from typing import Final, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr

MIN_SECRET_CHARS: Final = 32  # WAHA's alert asks for 64 for its API key. 32 random characters is a floor.
PLACEHOLDER_PREFIX: Final = "change-me"
REQUIRED_SECRETS: Final = ("WAHA_API_KEY", "WAHA_WEBHOOK_HMAC_KEY")
REQUIRED_VALUES: Final = ("TRACKER_DATABASE_URL", "WAHA_URL", "WAHA_SESSION", "BANTUAN_CONTACT")


class SettingsError(Exception):
    """A setting is missing or unsafe. The message says which one and what to do."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_url: SecretStr
    waha_url: str
    waha_session: str
    waha_api_key: SecretStr
    webhook_key: SecretStr
    human_contact: str  # Who `bantuan` points to: automated replies need a path to a human. Never in the repo.

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> Self:
        for name in (*REQUIRED_SECRETS, *REQUIRED_VALUES):
            if not environment.get(name, "").strip():
                raise SettingsError(f"{name} is not set: add it to .env. The app won't start without it.")
        for name in REQUIRED_SECRETS:
            value = environment[name]
            if value.startswith(PLACEHOLDER_PREFIX) or len(value) < MIN_SECRET_CHARS:
                raise SettingsError(f"{name} looks like a placeholder: set a random value in .env.")
        if not _is_private_address(environment["WAHA_URL"]):
            raise SettingsError("WAHA_URL isn't private: WAHA must only be reachable from this machine.")
        return cls(
            database_url=SecretStr(environment["TRACKER_DATABASE_URL"]),
            waha_url=environment["WAHA_URL"].rstrip("/"),
            waha_session=environment["WAHA_SESSION"],
            waha_api_key=SecretStr(environment["WAHA_API_KEY"]),
            webhook_key=SecretStr(environment["WAHA_WEBHOOK_HMAC_KEY"]),
            human_contact=environment["BANTUAN_CONTACT"],
        )


def _is_private_address(url: str) -> bool:
    """True for loopback, a private IP, or a one-word container name such as `waha`."""
    host = urlsplit(url).hostname
    if not host:
        return False
    if "." not in host and ":" not in host:
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False
