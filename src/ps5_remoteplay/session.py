import asyncio
import base64
import logging
import socket
import struct
from typing import Self

from .const import REMOTEPLAY_PORT, RP_VERSION
from .credentials import Credentials
from .discovery import DeviceStatus, get_device
from .crypto import HEADER_LENGTH, NONCE_LENGTH, SessionCipher
from .errors import LoginFailed, PasscodeMismatch, PasscodeRequired, ProtocolError
from .http import build_request, raise_for_status, read_response, request

_LOGGER = logging.getLogger(__name__)

CMD_LOGIN = 0x05
CMD_STANDBY = 0x50
CMD_HEARTBEAT = 0x1FE
CMD_PASSCODE = 0x8004

RESP_PASSCODE = 0x04
RESP_LOGIN = 0x05
RESP_HEARTBEAT = 0xFE

LOGIN_OK = 0
LOGIN_PASSCODE_UNMATCHED = 1

_DID = bytes.fromhex("00180000000700400080") + bytes(16) + bytes(6)
_OS_TYPE = b"Win10.0.0"
# The console ignores or resets the control connection when it arrives within
# microseconds of the init connection closing; ~1 ms was enough in captures.
CTRL_CONNECT_DELAY = 0.2


def encode_frame(frame_type: int, payload: bytes = b"") -> bytes:
    return struct.pack(">IHH", len(payload), frame_type, 0) + payload


async def _server_nonce(host: str, credentials: Credentials, port: int, timeout: float) -> bytes:
    response = await request(
        host,
        "GET",
        "/sie/ps5/rp/sess/init",
        {"RP-RegistKey": credentials.regist_key, "RP-Version": RP_VERSION},
        port=port,
        timeout=timeout,
    )
    nonce = base64.b64decode(response.headers.get("rp-nonce", ""))
    if len(nonce) != NONCE_LENGTH:
        raise ProtocolError("Session init returned an invalid RP-Nonce")
    return nonce


class RemotePlaySession:
    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, cipher: SessionCipher
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._cipher = cipher

    @classmethod
    async def open(
        cls,
        host: str,
        credentials: Credentials,
        *,
        passcode: str | None = None,
        port: int = REMOTEPLAY_PORT,
        timeout: float = 15.0,
    ) -> Self:
        step = "session init"
        try:
            async with asyncio.timeout(timeout):
                nonce = await _server_nonce(host, credentials, port, timeout)
                cipher = SessionCipher(credentials.rp_key, nonce)
                await asyncio.sleep(CTRL_CONNECT_DELAY)

                def encrypted(data: bytes) -> str:
                    return base64.b64encode(cipher.encrypt(data)).decode()

                # Encryption order sets the counters (0..4); keep it in this sequence.
                headers = {
                    "RP-Auth": encrypted(bytes.fromhex(credentials.regist_key).ljust(NONCE_LENGTH, b"\0")),
                    "RP-Version": RP_VERSION,
                    "RP-Did": encrypted(_DID),
                    "RP-ControllerType": "3",
                    "RP-ClientType": "11",
                    "RP-OSType": encrypted(_OS_TYPE),
                    "RP-ConPath": "1",
                    "RP-StartBitrate": encrypted(bytes(4)),
                    "RP-StreamingType": encrypted(struct.pack("<i", 1)),
                }

                step = "session control request"
                reader, writer = await asyncio.open_connection(host, port)
                session = cls(reader, writer, cipher)
                try:
                    writer.write(build_request("GET", host, port, "/sie/ps5/rp/sess/ctrl", headers))
                    await writer.drain()
                    _LOGGER.debug("GET /sie/ps5/rp/sess/ctrl: request sent")
                    response = await read_response(reader, read_body=False)
                    _LOGGER.debug(
                        "GET /sie/ps5/rp/sess/ctrl: HTTP %d (reason %s), headers %s",
                        response.status, response.headers.get("rp-application-reason", "-"),
                        sorted(response.headers),
                    )
                    raise_for_status(response)
                    if response.headers.get("content-length", "0") != "0":
                        raise ProtocolError("Unexpected body on session ctrl response")

                    server_type = response.headers.get("rp-server-type")
                    if server_type is None:
                        raise ProtocolError("Session ctrl response has no RP-Server-Type")
                    cipher.decrypt(base64.b64decode(server_type))

                    step = "login"
                    await session._login(passcode)
                except BaseException:
                    await session.close(abort=True)
                    raise
        except TimeoutError as err:
            raise ProtocolError(
                f"Timed out after {timeout:g}s waiting for the PS5 during {step}; "
                "another Remote Play session may still be open on the console"
            ) from err
        return session

    async def _send(self, frame_type: int, payload: bytes = b"") -> None:
        _LOGGER.debug("Sending frame type 0x%x (%d bytes)", frame_type, len(payload))
        self._writer.write(self._cipher.encode_frame(encode_frame(frame_type, payload)))
        await self._writer.drain()

    async def _receive(self) -> tuple[int, bytes] | None:
        """Next non-heartbeat frame, or None when the console closes the connection."""
        while True:
            try:
                header = await self._reader.readexactly(HEADER_LENGTH)
                length, frame_type, _ = struct.unpack(">IHH", header)
                payload = await self._reader.readexactly(length)
            except asyncio.IncompleteReadError:
                return None
            except ConnectionError:
                return None

            payload = self._cipher.decode_payload(payload)
            _LOGGER.debug("Received frame type 0x%x (%d bytes)", frame_type, length)
            if frame_type == RESP_HEARTBEAT:
                await self._send(CMD_HEARTBEAT)
                continue
            return frame_type, payload

    async def _expect(self, *types: int) -> tuple[int, bytes]:
        while True:
            frame = await self._receive()
            if frame is None:
                raise ProtocolError("Console closed the connection during login")
            if frame[0] in types:
                return frame

    async def _login(self, passcode: str | None) -> None:
        await self._send(CMD_LOGIN)
        frame_type, payload = await self._expect(RESP_LOGIN, RESP_PASSCODE)

        if frame_type == RESP_PASSCODE:
            if not passcode:
                raise PasscodeRequired("This PS5 user requires a passcode")
            await self._send(CMD_PASSCODE, passcode.encode("ascii"))
            frame_type, payload = await self._expect(RESP_LOGIN)

        if not payload:
            raise ProtocolError("Login result frame has no payload")
        result = payload[0]
        if result == LOGIN_PASSCODE_UNMATCHED:
            raise PasscodeMismatch("The passcode was rejected")
        if result != LOGIN_OK:
            raise LoginFailed(result)

    async def standby(self, timeout: float = 10.0) -> None:
        await self._send(CMD_STANDBY)
        try:
            async with asyncio.timeout(timeout):
                while await self._receive() is not None:
                    pass
        except TimeoutError:
            _LOGGER.debug("Console did not close the connection after standby")

    async def close(self, *, abort: bool = False) -> None:
        """Close the session.

        `abort` resets the connection instead of closing it politely: a console
        that never answered keeps the session reserved for this pairing after a
        normal close, and then refuses every later session as "already in use".
        """
        if abort:
            sock = self._writer.transport.get_extra_info("socket")
            if sock is not None:
                try:
                    # SO_LINGER with a zero timeout makes close() send RST
                    sock.setsockopt(
                        socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                    )
                except OSError:
                    _LOGGER.debug("Could not force a TCP reset on the abandoned session")
            self._writer.transport.abort()
            return
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def standby(
    host: str,
    credentials: Credentials,
    *,
    passcode: str | None = None,
    port: int = REMOTEPLAY_PORT,
    timeout: float = 15.0,
) -> bool:
    """Put an awake console into standby. Returns False if it was already in standby."""
    device = await get_device(host)
    if device.status == DeviceStatus.STANDBY:
        return False

    session = await RemotePlaySession.open(
        host, credentials, passcode=passcode, port=port, timeout=timeout
    )
    try:
        await session.standby()
    finally:
        await session.close()
    return True
