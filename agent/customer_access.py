"""Does this customer belong to the signed-in advisor?

AgentCore Memory is keyed by the customer id (the memory "actor"), and the customer id
arrives from the browser. Before a runtime reads or writes memory for a customer it asks
the profile API, with the advisor's own access token, for that customer. The API answers
only with profiles whose advisor is the token's user, so an empty answer means the
customer is not this advisor's.

* A positive answer is remembered per (user pool, token subject, customer id) for at most
  OWNERSHIP_CACHE_SECONDS and never past the token's expiry. The cache is a TTLCache on the
  wall clock (see container_cache.py). Nothing is cached for a negative or failed lookup,
  so a customer created a moment ago, or a lookup that failed, is asked again on the next call.
* The check fails closed: a missing API address, a bad id, a network error, a non-2xx
  answer or a malformed body all return False, and the caller runs without memory.
* Nothing is created at import except the empty cache; the HTTP request is made only
  when `advisor_owns_customer` is called.

agent/ and voice-agent/ each carry a copy of this file; keep the two identical.
"""
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

from container_cache import TTLCache, seconds_until

logger = logging.getLogger(__name__)

OWNERSHIP_CACHE_SECONDS = 300
LOOKUP_TIMEOUT_SECONDS = 3.0
MAX_RESPONSE_BYTES = 1_000_000
# Same shape the application service accepts for a customer id.
CUSTOMER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

OWNERSHIP_CACHE = TTLCache(max_entries=256)


def _fetch_profiles(url: str, token: str, timeout: float) -> Any:
    """GET `url` with the advisor's token and return the decoded JSON body."""
    if not url.lower().startswith("https://"):
        raise ValueError("profile API address is not https")
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    # The scheme is checked above, so urlopen cannot be pointed at file: or ftp: addresses.
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310  # nosemgrep
        return json.loads(response.read(MAX_RESPONSE_BYTES))


def advisor_owns_customer(
    *,
    api_base_url: Optional[str],
    token: Optional[str],
    subject: Optional[str],
    token_exp: Optional[float],
    customer_id: Any,
    user_pool_id: str = "",
    fetch: Optional[Callable[[str, str, float], Any]] = None,
    cache: Optional[TTLCache] = None,
) -> bool:
    """True when the profile API lists `customer_id` among the token holder's customers."""
    if not isinstance(customer_id, str) or not CUSTOMER_ID_RE.match(customer_id):
        return False
    if not api_base_url or not token:
        logger.warning("Customer ownership cannot be checked: profile API address or token missing")
        return False
    cache = OWNERSHIP_CACHE if cache is None else cache
    key = ("owns", user_pool_id, subject, customer_id)
    if subject and cache.get(key):
        return True
    url = f"{api_base_url.rstrip('/')}/profile?customer_id={urllib.parse.quote(customer_id, safe='')}"
    try:
        body = (fetch or _fetch_profiles)(url, token, LOOKUP_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 - any failure means "not verified"
        logger.warning("Customer ownership lookup failed: %s", type(exc).__name__)
        return False
    owned = isinstance(body, list) and any(
        isinstance(item, dict) and str(item.get("customer_id")) == customer_id for item in body
    )
    if owned and subject:
        cache.put(key, True, min(OWNERSHIP_CACHE_SECONDS, seconds_until(token_exp)))
    return owned
