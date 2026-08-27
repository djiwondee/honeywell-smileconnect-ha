"""Credential / session state contract for the Smile Connect API."""


class Credentials:
    """Holds everything required to sign an authenticated request."""

    def __init__(
        self,
        username: str,
        password: str,
        udid: str,
        device_token: str = "",
        authorization_token: str = "",
        user_id: str = "",
    ) -> None:
        self.username = username
        self.password = password
        self.udid = udid
        self.device_token = device_token
        self.authorization_token = authorization_token
        self.user_id = user_id
        self.reqcount = 0

    def next_reqcount(self) -> int:
        """Increment and return the request counter used in signing."""
        self.reqcount += 1
        return self.reqcount
