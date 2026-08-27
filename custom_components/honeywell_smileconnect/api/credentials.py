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
        """Return the current request counter, then increment it for the
        next call.

        Mirrors request.makeRequestData(): the CURRENT stored value is used
        to sign this request, and only afterwards bumped for the next one
        (b[counter] = current; ...; f[counter] = current + 1). Using a
        pre-incremented value here was the original bug that caused
        "session is finished" errors on the very first authenticated call.
        """
        current = self.reqcount
        self.reqcount += 1
        return current
