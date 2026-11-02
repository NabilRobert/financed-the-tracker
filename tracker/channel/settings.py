"""The app's settings, which refuse to load rather than run unsafely.

pywa turns signature checks off, with only a warning, when it has no app secret.
So a missing, blank or placeholder secret stops startup here (R1, T1).
"""

from collections.abc import Mapping
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, SecretStr

MIN_SECRET_CHARS: Final = 16  # Meta's app secret is 32 hex characters; the verify token is ours to choose.
PLACEHOLDER_PREFIX: Final = "change-me"
REQUIRED_SECRETS: Final = ("WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN")
REQUIRED_VALUES: Final = (
    "TRACKER_DATABASE_URL",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "BANTUAN_CONTACT",
)


class SettingsError(Exception):
    """A setting is missing or unsafe. The message says which one and what to do."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_url: SecretStr
    app_secret: SecretStr
    verify_token: SecretStr
    access_token: SecretStr
    phone_number_id: str
    human_contact: str  # Who `bantuan` points to (Meta requires a path to a human). Never in the repo.

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> Self:
        for name in (*REQUIRED_SECRETS, *REQUIRED_VALUES):
            if not environment.get(name, "").strip():
                raise SettingsError(f"{name} is not set: add it to .env. The app won't start without it.")
        for name in REQUIRED_SECRETS:
            value = environment[name]
            if value.startswith(PLACEHOLDER_PREFIX) or len(value) < MIN_SECRET_CHARS:
                raise SettingsError(f"{name} looks like a placeholder: set the real value in .env.")
        return cls(
            database_url=SecretStr(environment["TRACKER_DATABASE_URL"]),
            app_secret=SecretStr(environment["WHATSAPP_APP_SECRET"]),
            verify_token=SecretStr(environment["WHATSAPP_VERIFY_TOKEN"]),
            access_token=SecretStr(environment["WHATSAPP_ACCESS_TOKEN"]),
            phone_number_id=environment["WHATSAPP_PHONE_NUMBER_ID"],
            human_contact=environment["BANTUAN_CONTACT"],
        )
