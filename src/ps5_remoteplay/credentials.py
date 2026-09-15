from dataclasses import dataclass, field
from typing import Any


@dataclass
class Credentials:
    """Pairing result for one console.

    `to_dict`/`from_dict` use playactor's credentials.json entry shape, so
    entries can be moved between the two tools.
    """

    account_id: str
    user_credential: str
    registration: dict[str, str] = field(default_factory=dict)

    @property
    def regist_key(self) -> str:
        return self.registration["PS5-RegistKey"]

    @property
    def rp_key(self) -> bytes:
        return bytes.fromhex(self.registration["RP-Key"])

    def wake_fields(self) -> dict[str, str]:
        return {
            "app-type": "r",
            "auth-type": "R",
            "client-type": "vr",
            "model": "w",
            "user-credential": self.user_credential,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.wake_fields(),
            "accountId": self.account_id,
            "registration": dict(self.registration),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Credentials":
        registration = data.get("registration") or {}
        if not registration.get("PS5-RegistKey") or not registration.get("RP-Key"):
            raise ValueError("Credentials are missing PS5-RegistKey or RP-Key; pair again")
        return cls(
            account_id=data["accountId"],
            user_credential=data["user-credential"],
            registration=dict(registration),
        )
