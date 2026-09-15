import base64

import pytest

from ps5_remoteplay.crypto import (
    RegistrationCrypto,
    SessionCipher,
    generate_auth_seed,
    generate_iv,
    generate_seed,
    transform_server_nonce,
)


def test_generate_iv_playactor_case():
    nonce = bytes.fromhex("3e7e7a825973adab2f694346bd44dab5")
    assert generate_iv(nonce, 0) == bytes.fromhex("9044408273f8044dca767b5a16394d64")


def test_generate_iv(vectors):
    for case in vectors["generate_iv"]:
        iv = generate_iv(bytes.fromhex(case["nonce"]), int(case["counter"]))
        assert iv.hex() == case["iv"]


def test_generate_seed(vectors):
    for case in vectors["generate_seed"]:
        assert generate_seed(case["pin"], case["offset"]).hex() == case["seed"]


def test_auth_functions(vectors):
    for case in vectors["auth"]:
        nonce = bytes.fromhex(case["server_nonce"])
        key = bytes.fromhex(case["auth_key"])
        assert transform_server_nonce(nonce).hex() == case["transformed_nonce"]
        assert generate_auth_seed(key, nonce).hex() == case["auth_seed"]


def test_registration_payload(vectors):
    for case in vectors["registration_payload"]:
        crypto = RegistrationCrypto(case["pin"], bytes.fromhex(case["nonce"]))
        payload = crypto.encrypt_record({
            "Client-Type": "dabfa2ec873de5839bee8d3f4c0239c4282c07c25c6077a2931afcf0adc0d34f",
            "Np-AccountId": case["accountId"],
        })
        assert payload.hex() == case["payload"]
        decrypted = crypto.decrypt(bytes.fromhex(case["response_cipher"]))
        assert decrypted.decode() == case["response_plain"]


def test_registration_rejects_non_numeric_pin():
    with pytest.raises(ValueError):
        RegistrationCrypto("12ab", bytes(16))


def test_session_ctrl_headers_and_frames(vectors):
    for case in vectors["session"]:
        rp_key = bytes.fromhex(case["rp_key"])
        nonce = bytes.fromhex(case["server_nonce"])
        regist_key = bytes.fromhex(case["regist_key"]).ljust(16, b"\0")
        did = bytes.fromhex("00180000000700400080") + bytes(22)

        enc = SessionCipher(rp_key, nonce)
        headers = case["ctrl_headers"]
        b64 = lambda data: base64.b64encode(enc.encrypt(data)).decode()
        assert b64(regist_key) == headers["RP-Auth"]
        assert b64(did) == headers["RP-Did"]
        assert b64(b"Win10.0.0") == headers["RP-OSType"]
        assert b64(bytes(4)) == headers["RP-StartBitrate"]
        assert b64((1).to_bytes(4, "little")) == headers["RP-StreamingType"]

        for frame in case["outgoing_frames"]:
            assert enc.encode_frame(bytes.fromhex(frame["plain"])).hex() == frame["wire"]

        dec = SessionCipher(rp_key, nonce)
        server_type = dec.decrypt(base64.b64decode(case["server_type_cipher_b64"]))
        assert server_type.hex() == case["server_type_plain"]
        for frame in case["incoming_frames"]:
            wire = bytes.fromhex(frame["wire"])
            assert (wire[:8] + dec.decode_payload(wire[8:])).hex() == frame["plain"]
