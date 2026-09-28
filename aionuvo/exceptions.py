"""Exceptions raised by aionuvo."""


class NuvoError(Exception):
    """Base class for aionuvo errors."""


class DeniedActionError(NuvoError):
    """A denylisted (destructive) action was requested. See docs/safety.md."""


class NuvoConnectionError(NuvoError):
    """The zone could not be reached, even after rediscovery."""


class NuvoActionError(NuvoError):
    """The zone returned a SOAP fault."""

    def __init__(self, action: str, code: int | None, description: str | None) -> None:
        super().__init__(f"{action} failed: {code} {description}")
        self.action = action
        self.code = code
        self.description = description
