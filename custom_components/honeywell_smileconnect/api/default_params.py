"""Default parameter container shared across API calls."""


class DefaultApiParams:
    """Base set of fields present on (almost) every request.

    Additional per-endpoint fields are set as extra attributes on the
    instance before passing it to ApiRequest.request().
    """

    def __init__(self, skin: str = "flat_white") -> None:
        self.skin = skin
