import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

from .const import DISCOVERY_PORT, DISCOVERY_VERSION
from .errors import DeviceNotFound

_LOGGER = logging.getLogger(__name__)

_STATUS_CODE_STANDBY = "620"


class DeviceStatus(StrEnum):
    AWAKE = "AWAKE"
    STANDBY = "STANDBY"


@dataclass(frozen=True)
class DeviceInfo:
    host: str
    id: str
    name: str
    type: str
    status: DeviceStatus
    system_version: str
    host_request_port: int | None
    data: dict[str, str]


def format_message(message_type: str, data: dict[str, str] | None = None) -> bytes:
    fields = "".join(f"{k}:{v}\n" for k, v in (data or {}).items())
    return (
        f"{message_type} * HTTP/1.1\n{fields}device-discovery-protocol-version:{DISCOVERY_VERSION}\n"
    ).encode()


def parse_message(raw: bytes) -> dict[str, str] | None:
    """Parse a discovery datagram; returns None for anything that isn't a device reply."""
    lines = raw.decode("utf-8", errors="replace").replace("\r", "").split("\n")
    parts = lines[0].split(" ")
    if not lines[0].startswith("HTTP") or len(parts) < 2:
        return None

    result = {"status_code": parts[1]}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.lstrip(" ")
        if value:
            result[key.lower()] = value
    return result


def _device_from(host: str, parsed: dict[str, str]) -> DeviceInfo | None:
    device_id = parsed.get("host-id")
    if not device_id:
        return None
    port = parsed.get("host-request-port", "")
    return DeviceInfo(
        host=host,
        id=device_id,
        name=parsed.get("host-name", ""),
        type=parsed.get("host-type", ""),
        status=DeviceStatus.STANDBY if parsed["status_code"] == _STATUS_CODE_STANDBY else DeviceStatus.AWAKE,
        system_version=parsed.get("system-version", ""),
        host_request_port=int(port) if port.isdigit() else None,
        data=parsed,
    )


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.devices: asyncio.Queue[DeviceInfo] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        parsed = parse_message(data)
        device = parsed and _device_from(addr[0], parsed)
        if device:
            _LOGGER.debug("Discovered %s at %s: %s", device.id, device.host, device.status)
            self.devices.put_nowait(device)


async def _search(
    host: str | None, timeout: float, resend_interval: float, *, first_only: bool
) -> list[DeviceInfo]:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        _DiscoveryProtocol,
        local_addr=("0.0.0.0", 0),
        allow_broadcast=host is None,
    )
    target = (host or "255.255.255.255", DISCOVERY_PORT)
    message = format_message("SRCH")
    found: dict[str, DeviceInfo] = {}
    deadline = loop.time() + timeout
    try:
        while loop.time() < deadline:
            transport.sendto(message, target)
            resend_at = min(loop.time() + resend_interval, deadline)
            while (wait := resend_at - loop.time()) > 0:
                try:
                    device = await asyncio.wait_for(protocol.devices.get(), wait)
                except TimeoutError:
                    break
                if host and device.host != host:
                    continue
                found[device.id] = device
                if first_only:
                    return [device]
    finally:
        transport.close()
    return list(found.values())


async def discover(timeout: float = 5.0, resend_interval: float = 1.0) -> list[DeviceInfo]:
    """Broadcast a search and return every PS5 that answered."""
    return await _search(None, timeout, resend_interval, first_only=False)


async def get_device(host: str, timeout: float = 3.0, resend_interval: float = 1.0) -> DeviceInfo:
    """Query one console directly. Raises DeviceNotFound if it doesn't answer."""
    devices = await _search(host, timeout, resend_interval, first_only=True)
    if not devices:
        raise DeviceNotFound(f"No PS5 answered at {host}")
    return devices[0]


async def wait_for_status(
    host: str,
    status: DeviceStatus,
    timeout: float = 30.0,
    poll_interval: float = 2.0,
) -> DeviceInfo:
    async with asyncio.timeout(timeout):
        while True:
            try:
                device = await get_device(host, timeout=poll_interval)
                if device.status == status:
                    return device
            except DeviceNotFound:
                pass
            await asyncio.sleep(poll_interval)
