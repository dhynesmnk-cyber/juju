"""Settings: the only place environment variables are read. Keys are SecretStr, never logged."""
import re
from functools import lru_cache
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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


OWNER_NAME = re.compile(r"[a-z][a-z0-9_-]{0,31}")
OWNER_PASSCODE = re.compile(r"[A-Za-z0-9._-]{10,200}")  # the site passcode's rule (up.sh)


def parse_tracker_users(text: str) -> dict[str, str]:
    """"name:passcode,name:passcode" (web/lib/trackerPass.ts reads the same). The error never
    repeats the text: it holds the passcodes."""
    users: dict[str, str] = {}
    for pair in text.split(","):
        name, sep, code = pair.strip().partition(":")
        if not (sep and OWNER_NAME.fullmatch(name) and OWNER_PASSCODE.fullmatch(code)
                and name not in users):
            raise ValueError("TRACKER_USERS must be name:passcode pairs, separated by commas: "
                             "a lowercase name, and a passcode of 10 or more letters, digits, "
                             "- _ or .")
        users[name] = code
    return users


class Settings(BaseSettings):
    # Empty variables count as unset. Errors never echo the input, so a bad setting can't
    # print a key into the logs.
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore",
                                      hide_input_in_errors=True)

    database_url: str
    odds_api_key: SecretStr | None = None  # read with .get_secret_value()
    # Credits never spent below this, except by the T-45 captures themselves.
    odds_api_reserve: int = 1000
    # The day of the month the plan's credits reset (00:00 UTC), and how many games a week the
    # credits must last for (`core/budget.py`).
    odds_api_reset_day: int = 1
    games_per_week: int = 16
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
    # The owners' own sign-in (/me): "name:passcode,name:passcode". The site checks the
    # passcodes; the API only needs the names. Unset: /api/me answers 404.
    tracker_users: SecretStr | None = None

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

    @field_validator("odds_api_reset_day")
    @classmethod
    def _a_day_every_month_has(cls, v: int) -> int:
        if not 1 <= v <= 28:
            raise ValueError("ODDS_API_RESET_DAY must be 1 to 28")
        return v

    @field_validator("games_per_week")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("GAMES_PER_WEEK must be at least 1")
        return v

    @field_validator("books")
    @classmethod
    def _at_most_ten(cls, v: list[str]) -> list[str]:
        if not v or len(v) > 10:
            raise ValueError("BOOKS must list 1 to 10 bookmaker keys (10 cost one region)")
        return v

    @field_validator("tracker_users")
    @classmethod
    def _owner_pairs(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None:
            parse_tracker_users(v.get_secret_value())
        return v

    @property
    def llm_enabled(self) -> bool:
        return self.llm_api_key is not None and bool(self.llm_model)

    @property
    def tracker_names(self) -> frozenset[str]:
        if self.tracker_users is None:
            return frozenset()
        return frozenset(parse_tracker_users(self.tracker_users.get_secret_value()))


@lru_cache
def get_settings() -> Settings:
    return Settings()
