"""Thin Alpha Vantage HTTP client with on-disk caching and rate-limit handling.

The free Alpha Vantage tier is limited (currently ~25 requests/day), so every
response is cached to disk and re-used until it goes stale. This keeps repeated
notebook runs from burning through the daily quota.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

import requests

BASE_URL = "https://www.alphavantage.co/query"
DEFAULT_VAR_NAME = "AV_API_KEY"

# Project root is the parent of this package directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "alphavantage"


class AlphaVantageError(RuntimeError):
    """Raised when the API returns an explicit error message."""


class RateLimitError(AlphaVantageError):
    """Raised when the API signals that a rate/quota limit was hit."""


def _parse_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser so we don't need python-dotenv as a dependency."""
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_api_key(var_name: str = DEFAULT_VAR_NAME, env_path: Optional[str | Path] = None) -> str:
    """Resolve the Alpha Vantage API key.

    Looks first at the process environment, then at a .env file (an explicit
    ``env_path`` if given, otherwise the project root and the current directory).
    """
    import os

    if os.environ.get(var_name):
        return os.environ[var_name]

    candidates: list[Path] = []
    if env_path is not None:
        candidates.append(Path(env_path))
    else:
        candidates.append(PROJECT_ROOT / ".env")
        candidates.append(Path.cwd() / ".env")

    for candidate in candidates:
        if candidate.is_file():
            env = _parse_env_file(candidate)
            if env.get(var_name):
                return env[var_name]

    raise RuntimeError(
        f"Could not find API key '{var_name}'. Set it as an environment variable "
        f"or add a line like '{var_name}=your_key' to a .env file in {PROJECT_ROOT}."
    )


class AlphaVantageClient:
    """Small wrapper around the Alpha Vantage REST API.

    Parameters
    ----------
    api_key:
        Explicit key; if omitted it is loaded via :func:`load_api_key`.
    cache_dir:
        Directory used to store cached JSON responses.
    cache_ttl:
        Default time-to-live for cached responses, in seconds.
    min_interval:
        Minimum spacing between live HTTP requests, in seconds (polite throttling).
    timeout:
        Per-request HTTP timeout, in seconds.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        cache_ttl: float = 24 * 60 * 60,
        min_interval: float = 1.0,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or load_api_key()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = cache_ttl
        self.min_interval = min_interval
        self.timeout = timeout
        self.session = requests.Session()
        self._last_request_ts = 0.0

    def _cache_key(self, params: dict[str, Any]) -> str:
        # The api key is intentionally excluded so the cache is portable.
        payload = json.dumps(params, sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()[:24]
        function = params.get("function", "query")
        symbol = params.get("symbol", "")
        stem = "_".join(part for part in (function, symbol, digest) if part)
        return stem

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read_cache(self, key: str, ttl: float) -> Optional[dict[str, Any]]:
        path = self._cache_path(key)
        if not path.is_file():
            return None
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None

    def _write_cache(self, key: str, data: dict[str, Any]) -> None:
        self._cache_path(key).write_text(json.dumps(data))

    def _respect_rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_ts
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    @staticmethod
    def _check_for_errors(data: dict[str, Any]) -> None:
        if not isinstance(data, dict):
            raise AlphaVantageError(f"Unexpected response type: {type(data)!r}")
        if "Error Message" in data:
            raise AlphaVantageError(data["Error Message"])
        # The free-tier daily cap is reported under "Note" or "Information".
        for key in ("Note", "Information"):
            if key in data:
                raise RateLimitError(str(data[key]))

    def request(
        self,
        params: dict[str, Any],
        cache_ttl: Optional[float] = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """Perform a (cached) GET against the Alpha Vantage ``query`` endpoint."""
        ttl = self.cache_ttl if cache_ttl is None else cache_ttl
        key = self._cache_key(params)

        if not force_refresh:
            cached = self._read_cache(key, ttl)
            if cached is not None:
                return cached

        self._respect_rate_limit()
        full_params = {**params, "apikey": self.api_key}
        response = self.session.get(BASE_URL, params=full_params, timeout=self.timeout)
        self._last_request_ts = time.time()
        response.raise_for_status()

        data = response.json()
        self._check_for_errors(data)
        self._write_cache(key, data)
        return data
