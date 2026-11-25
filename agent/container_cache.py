"""Small caches that live as long as the runtime container.

One container serves one session id, so state kept here is shared
by the messages of one chat only. The caches exist to avoid repeating work that
gives the same answer on every message (fetching the Cognito signing keys, asking
Cognito who the advisor is, connecting to the gateway and listing its tools).

Rules every cache here follows:
  * Keys always contain the token subject, so two advisors never share an entry.
  * An entry never outlives the token it was made with (`seconds_until`).
  * Reads and writes are safe under concurrent requests (one lock per cache, and
    one lock per key so two requests missing the same key build the value once).
  * A factory that raises leaves nothing in the cache.

This module imports nothing outside the standard library, so it can be tested
without the AgentCore, Strands or MCP packages.
"""
import base64
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Seconds kept between "now" and a token's expiry before an entry stops being served.
EXPIRY_MARGIN_SECONDS = 60


def jwt_expiry(token: str) -> Optional[float]:
    """The `exp` claim of a JWT as epoch seconds, read WITHOUT checking the signature.

    Only used to decide how long a cache entry may live, never to trust a token:
    callers verify the signature separately (inbound token) or got the token from
    a trusted issuer over TLS (gateway token).
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return float(exp) if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None
    except (IndexError, ValueError, TypeError, AttributeError):
        return None


def seconds_until(epoch: Optional[float], margin: float = EXPIRY_MARGIN_SECONDS, now: Optional[float] = None) -> float:
    """Seconds from now until `epoch` minus `margin`; 0 or less means already unusable."""
    if epoch is None:
        return 0.0
    return epoch - margin - (time.time() if now is None else now)


def is_fresh(stamp: Optional[float], max_age: float, now: Optional[float] = None) -> bool:
    """True when `stamp` (wall-clock seconds) is at most `max_age` seconds old.

    A stamp in the future (the clock went backwards, as it can after a snapshot
    restore) or a missing stamp counts as not fresh, so the value is fetched again.
    """
    if stamp is None:
        return False
    age = (time.time() if now is None else now) - stamp
    return 0 <= age < max_age


class TTLCache:
    """Thread-safe dictionary whose entries expire.

    Time comes from `clock`. The default is the wall clock, not `time.monotonic()`: a
    process restored from a snapshot (AgentCore Runtime V2) can find the monotonic clock
    where it was when the snapshot was taken, so a monotonic expiry would never fire for
    an entry made before the snapshot. An entry is also dropped when the clock reads
    earlier than the moment the entry was stored (the clock went backwards), because its
    remaining lifetime can no longer be trusted. A forward jump simply expires it.
    """

    def __init__(self, max_entries: int = 128, clock: Callable[[], float] = time.time):
        self._max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        # key -> (value, expires_at, stored_at)
        self._items: dict[Any, tuple[Any, float, float]] = {}
        self._key_locks: dict[Any, threading.Lock] = {}

    def get(self, key: Any) -> Any:
        now = self._clock()
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return None
            if hit[1] <= now or now < hit[2]:
                del self._items[key]
                return None
            return hit[0]

    def put(self, key: Any, value: Any, ttl_seconds: float) -> bool:
        """Store `value` for `ttl_seconds`. Returns False (and stores nothing) when the ttl is not positive."""
        if ttl_seconds is None or ttl_seconds <= 0:
            return False
        now = self._clock()
        with self._lock:
            if key not in self._items and len(self._items) >= self._max_entries:
                for stale in [k for k, (_, until, stored) in self._items.items() if until <= now or now < stored]:
                    del self._items[stale]
                if len(self._items) >= self._max_entries:
                    del self._items[min(self._items, key=lambda k: self._items[k][1])]
            self._items[key] = (value, now + ttl_seconds, now)
            # Forget per-key locks of keys that are gone and not in use.
            if len(self._key_locks) > 4 * self._max_entries:
                for k in [k for k, lk in self._key_locks.items() if k not in self._items and not lk.locked()]:
                    del self._key_locks[k]
        return True

    def discard(self, key: Any) -> None:
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def get_or_create(
        self,
        key: Any,
        factory: Callable[[], Any],
        ttl_for: Callable[[Any], float],
        is_usable: Optional[Callable[[Any], bool]] = None,
    ) -> tuple[Any, bool]:
        """Return `(value, from_cache)`.

        On a miss, `factory()` runs once even when several threads ask for the same
        key at the same time; the others wait and then receive its value. A cached
        value that `is_usable` rejects is dropped and rebuilt. `ttl_for(value)` gives
        the lifetime of a new value; a ttl of 0 or less returns the value without
        caching it. Exceptions from `factory` propagate and nothing is cached.
        """
        value = self.get(key)
        if value is not None and (is_usable is None or is_usable(value)):
            return value, True
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            value = self.get(key)
            if value is not None and (is_usable is None or is_usable(value)):
                return value, True
            self.discard(key)
            value = factory()
            self.put(key, value, ttl_for(value))
            return value, False


@dataclass
class GatewayHandle:
    """A started gateway (MCP) client and the tools listed through it.

    `valid_until` is the earliest expiry (epoch seconds) of the tokens the client
    was connected with: the advisor's token travels with every tool call and the
    gateway token was used to open the connection. The handle is not served after
    that point (minus a margin), so a cached tool never calls with a dead token.
    """

    client: Any
    tools: list
    valid_until: Optional[float]
    created_at: float = field(default_factory=time.time)

    def is_usable(self, now: Optional[float] = None) -> bool:
        if seconds_until(self.valid_until, now=now) <= 0:
            return False
        # A client whose background thread has stopped cannot call tools any more.
        active = getattr(self.client, "_is_session_active", None)
        if callable(active):
            try:
                return bool(active())
            except Exception:  # noqa: BLE001 - treat any doubt as "not usable"
                return False
        return True

    def cache_seconds(self, max_ttl: float, now: Optional[float] = None) -> float:
        return min(max_ttl, seconds_until(self.valid_until, now=now))


# PyJWT downloads the key set with urllib and waits 30 s by default (connect and read). 10 s is far more
# than the download takes (a few KB); a stalled connection then fails the request sooner instead of at 30 s.
JWKS_FETCH_TIMEOUT_SECONDS = 10

_jwks_clients: dict[str, Any] = {}
_jwks_made_at: dict[str, float] = {}   # wall-clock time each client above was made
_jwks_lock = threading.Lock()

# A signing-key client older than this (or made "in the future" by a clock jump) is replaced by a new
# one, which downloads the key set again. PyJWKClient already refreshes its own copy every 5 minutes
# and on an unknown key id, but it counts with the monotonic clock; this bound uses the wall clock.
JWKS_CLIENT_MAX_AGE_SECONDS = 900


def get_jwks_client(url: str, factory: Optional[Callable[[str], Any]] = None) -> Any:
    """One signing-key client per JWKS url, replaced after JWKS_CLIENT_MAX_AGE_SECONDS.

    PyJWKClient keeps the downloaded key set for its own lifespan (5 minutes by
    default) and downloads again on an unknown key id, so key rotation is handled.
    """
    with _jwks_lock:
        client = _jwks_clients.get(url)
        # A client put in `_jwks_clients` without a timestamp (tests) is kept as it is.
        if client is not None and url in _jwks_made_at and not is_fresh(_jwks_made_at[url], JWKS_CLIENT_MAX_AGE_SECONDS):
            client = None
        if client is None:
            if factory is None:
                from jwt import PyJWKClient

                def factory(jwks_url: str):
                    return PyJWKClient(jwks_url, timeout=JWKS_FETCH_TIMEOUT_SECONDS)
            client = _jwks_clients[url] = factory(url)
            _jwks_made_at[url] = time.time()
        return client
