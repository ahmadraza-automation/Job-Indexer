import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, ClassVar, Literal
from urllib.parse import urlsplit

import httpx

from connectors.types import NormalizedJob

if TYPE_CHECKING:
    from jobs.models import Source


class ConnectorFetchError(Exception):
    """A connector request failed and should be recorded as a failed fetch."""

    def __init__(
        self,
        message: str,
        *,
        url: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class BaseConnector(ABC):
    platform_key: ClassVar[str]
    kind: ClassVar[Literal["ats", "board"]]
    timeout_seconds = 15.0
    retry_attempts = 3
    retry_backoff_seconds = 0.5
    rate_limit_interval = 1.0
    user_agent = "JobIndexer/1.0 (job indexing service)"

    _domain_lock = threading.Lock()
    _domain_locks: dict[str, threading.Lock] = {}
    _domain_next_request: dict[str, float] = {}

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(self.timeout_seconds),
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        )
        self._client.headers["User-Agent"] = self.user_agent
        self._owns_client = client is None

    @abstractmethod
    def discover_jobs(self, source: "Source") -> Iterator[NormalizedJob]:
        """Yield normalized jobs discovered for a configured source."""
        raise NotImplementedError

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Make a rate-limited request, retrying transient failures."""
        domain = urlsplit(url).hostname
        if not domain:
            raise ConnectorFetchError("Request URL has no valid domain", url=url)

        for attempt in range(self.retry_attempts):
            self._wait_for_domain(domain)
            try:
                response = self._client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                if attempt + 1 == self.retry_attempts:
                    raise ConnectorFetchError(
                        f"{method.upper()} {url} failed after {self.retry_attempts} attempts: {exc}",
                        url=url,
                    ) from exc
                self._backoff(attempt)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 < self.retry_attempts:
                    self._backoff(attempt)
                    continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ConnectorFetchError(
                    f"{method.upper()} {url} returned HTTP {response.status_code}",
                    url=url,
                    status_code=response.status_code,
                ) from exc
            return response

        raise ConnectorFetchError(
            f"{method.upper()} {url} failed after {self.retry_attempts} attempts",
            url=url,
        )

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "BaseConnector":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _wait_for_domain(self, domain: str) -> None:
        with self._domain_lock:
            domain_lock = self._domain_locks.setdefault(domain, threading.Lock())
        with domain_lock:
            now = time.monotonic()
            wait = self._domain_next_request.get(domain, 0.0) - now
            if wait > 0:
                time.sleep(wait)
            self._domain_next_request[domain] = (
                time.monotonic() + self.rate_limit_interval
            )

    def _backoff(self, attempt: int) -> None:
        time.sleep(self.retry_backoff_seconds * (2**attempt))
