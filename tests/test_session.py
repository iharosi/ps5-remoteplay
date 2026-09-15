import asyncio
import base64
import struct
from unittest.mock import AsyncMock

import pytest

from types import SimpleNamespace

from ps5_remoteplay import DeviceStatus
from ps5_remoteplay.credentials import Credentials
from ps5_remoteplay.crypto import SessionCipher
from ps5_remoteplay.errors import PasscodeMismatch, PasscodeRequired, RemotePlayHttpError
from ps5_remoteplay.session import RemotePlaySession, encode_frame

RP_KEY = bytes(range(16))
SERVER_NONCE = bytes(range(0x80, 0x90))
REGIST_KEY = "3132333435363738"
CREDS = Credentials(
    account_id="AAAAAAAAAEI=",
    user_credential="305419896",
    registration={"PS5-RegistKey": REGIST_KEY, "RP-Key": RP_KEY.hex()},
)


class FakeConsole:
    def __init__(
        self, *, passcode: str | None = None, reject_init: bool = False, ignore_first_ctrl: bool = False
    ) -> None:
        self.passcode = passcode
        self.reject_init = reject_init
        self.ignore_first_ctrl = ignore_first_ctrl
        self.ctrl_requests = 0
        self.received: list[int] = []
        self.events: list[str] = []
        self.ctrl_headers: dict[str, bytes] = {}

    async def __aenter__(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def handle(self, reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        request_line, *lines = head.split("\r\n")
        headers = {k.lower(): v.strip() for k, v in (l.split(":", 1) for l in lines if ":" in l)}
        assert headers["content-length"] == "0"
        assert headers["user-agent"] == "remoteplay Windows"

        self.events.append("ctrl opened" if "/ctrl" in request_line else "init opened")
        if "/init" in request_line:
            if self.reject_init:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nRP-Application-Reason: 80108b10\r\nContent-Length: 0\r\n\r\n")
            else:
                assert headers["rp-registkey"] == REGIST_KEY
                nonce = base64.b64encode(SERVER_NONCE).decode()
                writer.write(f"HTTP/1.1 200 OK\r\nRP-Nonce: {nonce}\r\nContent-Length: 0\r\n\r\n".encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            self.events.append("init closed")
            return

        self.ctrl_requests += 1
        if self.ignore_first_ctrl and self.ctrl_requests == 1:
            # the console acknowledges the request but never answers
            try:
                await reader.read()
            except ConnectionResetError:
                pass
            return

        cipher = SessionCipher(RP_KEY, SERVER_NONCE)
        for name in ("rp-auth", "rp-did", "rp-ostype", "rp-startbitrate", "rp-streamingtype"):
            self.ctrl_headers[name] = cipher.decrypt(base64.b64decode(headers[name]))

        server_type = base64.b64encode(cipher.encrypt(b"\x00\x00\x00\x01")).decode()
        # Headers and a heartbeat arrive in one write, so the client must not over-read the HTTP response.
        writer.write(
            f"HTTP/1.1 200 OK\r\nRP-Server-Type: {server_type}\r\nContent-Length: 0\r\n\r\n".encode()
            + encode_frame(0xFE)
        )
        await writer.drain()

        async def send(frame_type, payload=b""):
            frame = cipher.encode_frame(encode_frame(frame_type, payload))
            # split frames across writes to exercise reassembly
            writer.write(frame[:5])
            await writer.drain()
            await asyncio.sleep(0.01)
            writer.write(frame[5:])
            await writer.drain()

        while True:
            try:
                length, frame_type, _ = struct.unpack(">IHH", await reader.readexactly(8))
                payload = cipher.decode_payload(await reader.readexactly(length))
            except asyncio.IncompleteReadError:
                break
            self.received.append(frame_type)
            if frame_type == 0x05:
                if self.passcode:
                    await send(0x04)
                else:
                    await send(0x05, b"\x00")
            elif frame_type == 0x8004:
                await send(0x05, b"\x00" if payload.decode() == self.passcode else b"\x01")
            elif frame_type == 0x50:
                break
        writer.close()


async def test_login_and_standby():
    async with FakeConsole() as console:
        session = await RemotePlaySession.open("127.0.0.1", CREDS, port=console.port, timeout=5)
        await session.standby(timeout=5)
        await session.close()

    assert console.received == [0x05, 0x1FE, 0x50]
    # the console rejects a second connection opened while the first is still closing
    assert console.events == ["init opened", "init closed", "ctrl opened"]
    assert console.ctrl_headers["rp-auth"] == bytes.fromhex(REGIST_KEY).ljust(16, b"\0")
    did = console.ctrl_headers["rp-did"]
    assert len(did) == 32
    assert did.startswith(bytes.fromhex("00180000000700400080")) and did.endswith(bytes(6))
    assert did[10:26] != bytes(16), "device id must be random, not fixed"
    assert console.ctrl_headers["rp-ostype"] == b"Win10.0.0"
    assert console.ctrl_headers["rp-startbitrate"] == bytes(4)
    assert console.ctrl_headers["rp-streamingtype"] == b"\x01\x00\x00\x00"


async def test_passcode_accepted():
    async with FakeConsole(passcode="1234") as console:
        session = await RemotePlaySession.open("127.0.0.1", CREDS, passcode="1234", port=console.port, timeout=5)
        await session.standby(timeout=5)
        await session.close()
    assert 0x8004 in console.received and console.received[-1] == 0x50


async def test_passcode_required():
    async with FakeConsole(passcode="1234") as console:
        with pytest.raises(PasscodeRequired):
            await RemotePlaySession.open("127.0.0.1", CREDS, port=console.port, timeout=5)


async def test_passcode_mismatch():
    async with FakeConsole(passcode="1234") as console:
        with pytest.raises(PasscodeMismatch):
            await RemotePlaySession.open("127.0.0.1", CREDS, passcode="9999", port=console.port, timeout=5)


async def test_http_error_reason():
    async with FakeConsole(reject_init=True) as console:
        with pytest.raises(RemotePlayHttpError, match="already in use"):
            await RemotePlaySession.open("127.0.0.1", CREDS, port=console.port, timeout=5)


async def test_standby_retries_after_a_stuck_session(monkeypatch) -> None:
    from ps5_remoteplay import session as session_module

    async with FakeConsole(ignore_first_ctrl=True) as console:
        closes: list[bool] = []
        original_close = session_module.RemotePlaySession.close

        async def record_close(self, *, abort: bool = False):
            closes.append(abort)
            await original_close(self, abort=abort)

        monkeypatch.setattr(session_module.RemotePlaySession, "close", record_close)
        monkeypatch.setattr(session_module, "RETRY_DELAY", 0.05)
        monkeypatch.setattr(
            session_module,
            "get_device",
            AsyncMock(return_value=SimpleNamespace(status=DeviceStatus.AWAKE)),
        )
        assert await session_module.standby("127.0.0.1", CREDS, port=console.port, timeout=1) is True

    assert console.ctrl_requests == 2
    assert closes[0] is True, "the stuck session must be reset, not left half-open"
    assert console.received[-1] == 0x50
