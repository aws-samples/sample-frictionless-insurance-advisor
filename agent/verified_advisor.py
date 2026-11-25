"""Which Cognito token and which Cognito user count as an advisor.

`is_app_access_token` takes the claims of a token whose signature and expiry are already verified
and accepts them only for an access token that this user pool issued to the web app's client.
An ID token, a token from another pool, and a token from another app client (the machine client
the runtimes use towards the gateway, for example) are refused.

The advisor id is the user's email address. An address Cognito has not verified is text the
user typed in, so it must never become an advisor id. `verified_email` takes an AdminGetUser
response and returns the address only for a user that is enabled, CONFIRMED and has
email_verified = "true"; for any other response it returns None, and the caller treats the
request as not signed in.

The address is returned exactly as stored: no case folding, no trimming.

agent/ and voice-agent/ each carry a copy of this file; keep the two identical.
"""
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def is_app_access_token(claims: Any, issuer: str, client_id: str) -> bool:
    """True for the claims of an access token issued by `issuer` to the app client `client_id`.

    An empty `client_id` or `issuer` (a runtime without that setting) accepts nothing.
    """
    if not isinstance(claims, dict) or not issuer or not client_id:
        return False
    return (
        claims.get("token_use") == "access"
        and claims.get("iss") == issuer
        and claims.get("client_id") == client_id
    )


def verified_email(user: Any) -> Optional[str]:
    """The email address stored for a Cognito user, exactly as stored, or None."""
    if not isinstance(user, dict) or user.get("Enabled") is not True or user.get("UserStatus") != "CONFIRMED":
        logger.warning("The user is not enabled and confirmed")
        return None
    attributes = {a.get("Name"): a.get("Value") for a in user.get("UserAttributes") or []}
    email = attributes.get("email")
    if attributes.get("email_verified") != "true" or not isinstance(email, str) or not email:
        logger.warning("The user has no verified email address")
        return None
    return email
