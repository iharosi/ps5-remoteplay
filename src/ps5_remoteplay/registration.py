import asyncio
import logging
import os
import re

from .const import CLIENT_TYPE, REMOTEPLAY_PORT, RP_VERSION
from .credentials import Credentials
from .crypto import NONCE_LENGTH, RegistrationCrypto
from .errors import PS5Error
from .http import request
from .wake import user_credential_from_regist_key

_LOGGER = logging.getLogger(__name__)

_SEARCH_REQUEST = b"SRC3"
_SEARCH_RESPONSE = b"RES3"
_HEX_KEY = re.compile(r"^[0-9a-fA-F]{32}$")


def parse_body(body: bytes) -> dict[str, str]:
    result = {}
    for line in body.decode("utf-8", errors="replace").split("\r\n"):
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if value.strip():
            result[key] = value.strip()
    return result


class _SearchProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.found = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if data.startswith(_SEARCH_RESPONSE) and not self.found.done():
            self.found.set_result(addr)


async def _announce(host: str, timeout: float, resend_interval: float = 2.0) -> None:
    # The console rejects the registration POST unless this search happens first.
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        _SearchProtocol, local_addr=("0.0.0.0", REMOTEPLAY_PORT), allow_broadcast=True
    )
    try:
        async with asyncio.timeout(timeout):
            while not protocol.found.done():
                transport.sendto(_SEARCH_REQUEST, (host, REMOTEPLAY_PORT))
                await asyncio.wait({protocol.found}, timeout=resend_interval)
    except TimeoutError as e:
        raise PS5Error(
            "The PS5 did not answer the registration search. Open "
            "Settings > System > Remote Play > Link Device on the console and try again."
        ) from e
    finally:
        transport.close()
    await asyncio.sleep(0.1)


async def register(host: str, account_id: str, pin: str, *, timeout: float = 30.0) -> Credentials:
    """Pair with a console showing its Link Device PIN. The console must be awake."""
    pin = pin.strip()
    await _announce(host, timeout)

    crypto = RegistrationCrypto(pin, os.urandom(NONCE_LENGTH))
    body = crypto.encrypt_record({"Client-Type": CLIENT_TYPE, "Np-AccountId": account_id})
    response = await request(
        host, "POST", "/sie/ps5/rp/sess/rgst", {"RP-Version": RP_VERSION}, body, timeout=timeout
    )

    registration = parse_body(crypto.decrypt(response.body))
    _LOGGER.debug("Registration returned fields: %s", sorted(registration))

    if not _HEX_KEY.match(registration.get("RP-Key", "")):
        raise PS5Error("Registration returned an invalid RP-Key")
    regist_key = registration.get("PS5-RegistKey")
    if not regist_key:
        raise PS5Error("Registration returned no PS5-RegistKey")

    return Credentials(
        account_id=account_id,
        user_credential=user_credential_from_regist_key(regist_key),
        registration=registration,
    )
