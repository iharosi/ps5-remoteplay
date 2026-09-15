import hashlib
import hmac
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

try:
    from cryptography.hazmat.decrepit.ciphers.modes import CFB
except ImportError:
    from cryptography.hazmat.primitives.ciphers.modes import CFB

from .keys import AERO_KEYS, AUTH_NONCE_KEYS, AUTH_SEED_KEYS, INIT_KEYS

NONCE_LENGTH = 16
PREFACE_LENGTH = 480
HEADER_LENGTH = 8

_HMAC_KEY = bytes.fromhex("464687b349ca8ce859c5270f5d7a69d6")


def generate_iv(nonce: bytes, counter: int) -> bytes:
    message = nonce + struct.pack(">Q", counter)
    return hmac.new(_HMAC_KEY, message, hashlib.sha256).digest()[:NONCE_LENGTH]


def generate_seed(pin: int, offset: int) -> bytes:
    seed = bytearray(INIT_KEYS[(i * 0x20 + offset) % len(INIT_KEYS)] for i in range(16))
    for i, b in enumerate(struct.pack(">I", pin & 0xFFFFFFFF)):
        seed[12 + i] ^= b
    return bytes(seed)


def generate_aeropause(nonce: bytes, preface: bytes) -> bytes:
    offset = preface[0] >> 3
    return bytes(
        ((nonce[i] ^ AERO_KEYS[i * 0x20 + offset]) + 0xD3 + i) & 0xFF
        for i in range(16)
    )


def transform_server_nonce(server_nonce: bytes) -> bytes:
    offset = (server_nonce[0] >> 3) * 0x70
    return bytes(
        ((server_nonce[i] - 0x2D - i) ^ AUTH_NONCE_KEYS[offset + i]) & 0xFF
        for i in range(16)
    )


def generate_auth_seed(auth_key: bytes, server_nonce: bytes) -> bytes:
    offset = (server_nonce[7] >> 3) * 0x70
    return bytes(
        (((auth_key[i] + 0x18 + i) ^ server_nonce[i]) ^ AUTH_SEED_KEYS[offset + i]) & 0xFF
        for i in range(16)
    )


def aes_cfb(key: bytes, iv: bytes, data: bytes, *, decrypt: bool = False) -> bytes:
    cipher = Cipher(algorithms.AES(key), CFB(iv))
    ctx = cipher.decryptor() if decrypt else cipher.encryptor()
    return ctx.update(data) + ctx.finalize()


class RegistrationCrypto:
    def __init__(self, pin: str, nonce: bytes) -> None:
        if not pin.isdigit():
            raise ValueError("PIN must be numeric")
        if len(nonce) != NONCE_LENGTH:
            raise ValueError("nonce must be 16 bytes")

        preface = bytearray(b"A" * PREFACE_LENGTH)
        self._iv = generate_iv(nonce, 0)
        self._key = generate_seed(int(pin), preface[0x18D] & 0x1F)

        aeropause = generate_aeropause(nonce, preface)
        preface[0xC7:0xCF] = aeropause[8:16]
        preface[0x191:0x199] = aeropause[0:8]
        self.preface = bytes(preface)

    def encrypt_record(self, record: dict[str, str]) -> bytes:
        text = "".join(f"{k}: {v}\r\n" for k, v in record.items())
        return self.preface + aes_cfb(self._key, self._iv, text.encode())

    def decrypt(self, data: bytes) -> bytes:
        return aes_cfb(self._key, self._iv, data, decrypt=True)


class SessionCipher:
    """Per-message AES-CFB with independent encrypt/decrypt counters.

    Header-only frames are sent in the clear and do not consume a counter.
    """

    def __init__(self, rp_key: bytes, server_nonce: bytes) -> None:
        if len(rp_key) != NONCE_LENGTH or len(server_nonce) != NONCE_LENGTH:
            raise ValueError("RP-Key and server nonce must be 16 bytes")
        self._key = generate_auth_seed(rp_key, server_nonce)
        self._nonce = transform_server_nonce(server_nonce)
        self._encrypt_counter = 0
        self._decrypt_counter = 0

    def encrypt(self, data: bytes) -> bytes:
        iv = generate_iv(self._nonce, self._encrypt_counter)
        self._encrypt_counter += 1
        return aes_cfb(self._key, iv, data)

    def decrypt(self, data: bytes) -> bytes:
        iv = generate_iv(self._nonce, self._decrypt_counter)
        self._decrypt_counter += 1
        return aes_cfb(self._key, iv, data, decrypt=True)

    def encode_frame(self, frame: bytes) -> bytes:
        if len(frame) == HEADER_LENGTH:
            return frame
        return frame[:HEADER_LENGTH] + self.encrypt(frame[HEADER_LENGTH:])

    def decode_payload(self, payload: bytes) -> bytes:
        if not payload:
            return payload
        return self.decrypt(payload)
