import base64
from urllib.parse import parse_qs, urlparse

import aiohttp

from .const import (
    OAUTH_AUTHORIZE_URL,
    OAUTH_CLIENT_ID,
    OAUTH_CLIENT_SECRET,
    OAUTH_REDIRECT_URI,
    OAUTH_TOKEN_URL,
)
from .errors import OAuthError


def login_url() -> str:
    return (
        f"{OAUTH_AUTHORIZE_URL}?service_entity=urn:service-entity:psn&response_type=code"
        f"&client_id={OAUTH_CLIENT_ID}&redirect_uri={OAUTH_REDIRECT_URI}"
        "&scope=psn:clientapp&request_locale=en_US&ui=pr&service_logo=ps&layout_type=popup"
        "&smcid=remoteplay&prompt=always&PlatformPrivacyWs1=minimal&"
    )


def code_from_redirect(redirect_url: str) -> str:
    codes = parse_qs(urlparse(redirect_url.strip()).query).get("code")
    if not codes or not codes[0]:
        raise OAuthError("The pasted URL has no 'code' parameter; copy the full redirect URL")
    return codes[0]


def account_id_from_user_id(user_id: str | int) -> str:
    return base64.b64encode(int(user_id).to_bytes(8, "little")).decode()


async def _json(response: aiohttp.ClientResponse, what: str) -> dict:
    if response.status >= 400:
        raise OAuthError(f"PSN {what} failed with HTTP {response.status}: {await response.text()}")
    return await response.json(content_type=None)


async def account_id_from_redirect(
    redirect_url: str,
    session: aiohttp.ClientSession | None = None,
) -> str:
    """Exchange the pasted redirect URL for the base64 Remote Play account id."""
    code = code_from_redirect(redirect_url)
    auth = aiohttp.BasicAuth(OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET)
    owned = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.post(
            OAUTH_TOKEN_URL,
            auth=auth,
            data={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": OAUTH_REDIRECT_URI,
            },
        ) as response:
            access_token = (await _json(response, "token exchange")).get("access_token")
        if not access_token:
            raise OAuthError("PSN did not return an access token; the code may have expired")

        async with session.get(f"{OAUTH_TOKEN_URL}/{access_token}", auth=auth) as response:
            user_id = (await _json(response, "account lookup")).get("user_id")
        if not user_id:
            raise OAuthError("PSN account lookup returned no user_id")
        return account_id_from_user_id(user_id)
    finally:
        if owned:
            await session.close()
