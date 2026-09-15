import asyncio

from .const import DISCOVERY_PORT
from .credentials import Credentials
from .discovery import format_message


def user_credential_from_regist_key(regist_key: str) -> str:
    # The key's bytes are ASCII hex digits; that hex number, in decimal, is the wake credential.
    return str(int(bytes.fromhex(regist_key).decode("ascii"), 16))


async def wake(host: str, credentials: Credentials) -> None:
    """Send WAKEUP. Does not wait for the console to come up; see wait_for_status."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("0.0.0.0", 0)
    )
    try:
        transport.sendto(format_message("WAKEUP", credentials.wake_fields()), (host, DISCOVERY_PORT))
    finally:
        transport.close()
