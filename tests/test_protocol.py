from ps5_remoteplay.credentials import Credentials
from ps5_remoteplay.discovery import DeviceStatus, _device_from, format_message, parse_message
from ps5_remoteplay.oauth import account_id_from_user_id, code_from_redirect, login_url
from ps5_remoteplay.registration import parse_body
from ps5_remoteplay.wake import user_credential_from_regist_key

import pytest

from ps5_remoteplay.errors import OAuthError

PLAYACTOR_LOGIN_URL = (
    "https://auth.api.sonyentertainmentnetwork.com/2.0/oauth/authorize?service_entity=urn:service-entity:psn"
    "&response_type=code&client_id=ba495a24-818c-472b-b12d-ff231c1b5745"
    "&redirect_uri=https://remoteplay.dl.playstation.net/remoteplay/redirect&scope=psn:clientapp"
    "&request_locale=en_US&ui=pr&service_logo=ps&layout_type=popup&smcid=remoteplay&prompt=always"
    "&PlatformPrivacyWs1=minimal&"
)


def test_srch_and_wakeup_packets(vectors):
    fmt = vectors["discovery_format"]
    assert format_message("SRCH").decode() == fmt["srch"]
    creds = Credentials.from_dict(fmt["wakeup_creds"] | {"registration": {"PS5-RegistKey": "31", "RP-Key": "00" * 16}})
    assert format_message("WAKEUP", creds.wake_fields()).decode() == fmt["wakeup"]


def test_parse_device_replies(vectors):
    for case in vectors["discovery_parse"]:
        parsed = parse_message(case["raw"].encode())
        expected = case["parsed"]
        if expected["type"] != "DEVICE":
            assert parsed is None
            continue
        device = _device_from("10.0.0.5", parsed)
        assert device.id == expected["host-id"]
        assert device.name == expected["host-name"]
        assert device.system_version == expected["system-version"]
        assert device.host_request_port == int(expected["host-request-port"])
        assert device.status == DeviceStatus(expected["status"])


def test_parse_strips_cr_and_keeps_colons():
    parsed = parse_message(b"HTTP/1.1 200 Ok\r\nhost-id:ABC\r\nhost-name: My:PS5\r\n")
    assert parsed["host-id"] == "ABC"
    assert parsed["host-name"] == "My:PS5"


def test_user_credential(vectors):
    for case in vectors["user_credential"]:
        assert user_credential_from_regist_key(case["regist_key"]) == case["user_credential"]


def test_account_id(vectors):
    for case in vectors["account_id"]:
        assert account_id_from_user_id(case["user_id"]) == case["account_id"]


def test_login_url_matches_playactor():
    assert login_url() == PLAYACTOR_LOGIN_URL


def test_code_from_redirect():
    url = "https://remoteplay.dl.playstation.net/remoteplay/redirect?code=v5v0Nc&cid=5fea6e76"
    assert code_from_redirect(f"  {url}\n") == "v5v0Nc"
    with pytest.raises(OAuthError):
        code_from_redirect("https://remoteplay.dl.playstation.net/remoteplay/redirect")


def test_parse_body_keeps_mac_addresses(vectors):
    case = vectors["registration_payload"][0]
    parsed = parse_body(case["response_plain"].encode())
    assert parsed["AP-Bssid"] == "aa:bb:cc:dd:ee:ff"
    expected = dict(case["decrypted_parsed_by_playactor"])
    expected["AP-Bssid"] = "aa:bb:cc:dd:ee:ff"
    assert parsed == expected


def test_credentials_roundtrip_playactor_shape():
    entry = {
        "app-type": "r", "auth-type": "R", "client-type": "vr", "model": "w",
        "user-credential": "305419896", "accountId": "AAAAAAAAAEI=",
        "registration": {"PS5-RegistKey": "3132333435363738", "RP-Key": "00" * 16, "RP-KeyType": "2"},
    }
    creds = Credentials.from_dict(entry)
    assert creds.to_dict() == entry
    assert list(creds.to_dict()) == list(entry)


def test_credentials_reject_incomplete():
    with pytest.raises(ValueError):
        Credentials.from_dict({"accountId": "x", "user-credential": "1", "registration": {}})


def test_login_failed_messages():
    from ps5_remoteplay.errors import LoginFailed

    assert "another Remote Play session" in str(LoginFailed(2))
    assert str(LoginFailed(7)) == "Remote Play login failed (code 7)"
