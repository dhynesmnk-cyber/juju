"""Settings: the only place environment variables are read. Keys are SecretStr, never logged."""
import re
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# A tracker user's name: it is `logged_by` on their slips, and the site's cookie puts it before a
# dot, so it has none (web/lib/trackerUsers.ts has the same rule).
TRACKER_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")

# Display order of books (docs/GOALS.md, "book of record"): the first book in this chain that
# has a price on time wins. Hard Rock first, as the owner chose on 2026-09-30.
DEFAULT_BOOK_CHAIN = [
    "hardrockbet", "draftkings", "fanduel", "betmgm", "williamhill_us", "fanatics",
    "betrivers", "espnbet", "ballybet", "betparx",
]


def normalize_database_url(url: str) -> str:
    """Point Postgres URLs at the psycopg 3 driver; leave any other URL untouched."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


class Settings(BaseSettings):
    # Empty variables count as unset. Errors never echo the input, so a bad setting can't
    # print a key into the logs.
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore",
                                      hide_input_in_errors=True)

    database_url: str
    odds_api_key: SecretStr | None = None  # read with .get_secret_value()
    # Credits never spent below this, except by the T-45 captures themselves.
    odds_api_reserve: int = 1000
    # Books requested from The Odds API: at most 10 cost the same as one region.
    books: Annotated[list[str], NoDecode] = DEFAULT_BOOK_CHAIN
    # The LLM fallback for free text (OpenRouter's OpenAI-compatible API). Off unless both
    # the key and the model are set.
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str | None = None
    llm_daily_budget: int = 2000  # calls per UTC day, across the API process
    # How far back repair_gaps looks for games with no on-time price.
    repair_days: int = 14
    # ESPN event ids whose every response is kept in raw_samples, to replay a game in tests
    # (the tracker's `export-recording`). Comma-separated; empty records nothing.
    record_event_ids: Annotated[list[str], NoDecode] = []
    # The tracker's users (/me), from TRACKER_USERS: "Chris:<passcode>,Ash:<passcode>", the
    # variable the site reads. Only the names are kept: the site checks the passcodes and names
    # the signed-in user in `x-juju-user`. None configured: /api/me answers 404.
    tracker_users: Annotated[list[str], NoDecode] = []
    # The time zone the tracker shows times in. Its analytics' days and months stay US Eastern,
    # as ESPN files games.
    display_tz: str = "America/New_York"
    # A tracker analytics group with fewer decided legs than this is marked low sample.
    min_sample: int = 30

    @field_validator("database_url")
    @classmethod
    def _normalize_url(cls, v: str) -> str:
        return normalize_database_url(v)

    @field_validator("books", "record_event_ids", mode="before")
    @classmethod
    def _split_list(cls, v: object) -> object:
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @field_validator("tracker_users", mode="before")
    @classmethod
    def _tracker_names(cls, v: object) -> object:
        """Keep each entry's name, the part before its first colon; the passcode is dropped
        here and never stored, logged or echoed in an error."""
        if not isinstance(v, str):
            return v
        names = [entry.split(":", 1)[0].strip() for entry in v.split(",") if entry.strip()]
        # By position only: a missing colon would put the passcode in the name.
        bad = [str(n) for n, name in enumerate(names, start=1)
               if not TRACKER_NAME.fullmatch(name)]
        if bad:
            raise ValueError(f"TRACKER_USERS entry {', '.join(bad)}: a name is a letter, then up "
                             "to 31 letters, digits, - or _, then a colon and the passcode")
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("TRACKER_USERS names the same user twice")
        return names

    @field_validator("display_tz")
    @classmethod
    def _real_zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError(f"DISPLAY_TZ {v!r} is not an IANA time zone") from e
        return v

    @field_validator("min_sample")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("MIN_SAMPLE must be at least 1")
        return v

    @field_validator("books")
    @classmethod
    def _at_most_ten(cls, v: list[str]) -> list[str]:
        if not v or len(v) > 10:
            raise ValueError("BOOKS must list 1 to 10 bookmaker keys (10 cost one region)")
        return v

    @property
    def llm_enabled(self) -> bool:
        return self.llm_api_key is not None and bool(self.llm_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()
