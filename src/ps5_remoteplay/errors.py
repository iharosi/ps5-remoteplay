class PS5Error(Exception):
    pass


class DeviceNotFound(PS5Error):
    pass


class OAuthError(PS5Error):
    pass


_REASONS = {
    "80108b09": "Registration failed, probably invalid PIN",
    "80108b02": "Invalid PSN ID",
    "80108b10": "Remote is already in use",
    "80108b15": "Remote Play on Console crashed",
    "80108b11": "RP-Version mismatch",
}


class RemotePlayHttpError(PS5Error):
    def __init__(self, status: int, reason_code: str | None = None) -> None:
        self.status = status
        self.reason_code = reason_code
        self.reason = _REASONS.get((reason_code or "").lower())
        message = f"Remote Play request failed with HTTP {status}"
        if reason_code:
            message += f": {self.reason or 'Other error'} ({reason_code})"
        super().__init__(message)


class ProtocolError(PS5Error):
    pass


class PasscodeRequired(PS5Error):
    pass


class PasscodeMismatch(PS5Error):
    pass


class LoginFailed(PS5Error):
    def __init__(self, result: int) -> None:
        self.result = result
        super().__init__(f"Remote Play login failed with result {result}")
