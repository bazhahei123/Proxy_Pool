class ProxyPoolError(Exception):
    """Base exception for proxy-pool failures."""


class ConfigError(ProxyPoolError):
    """Configuration is missing or invalid."""


class ProxyTransportError(ProxyPoolError):
    """No proxy could establish a network connection."""


class RequestBodyNotReplayable(ProxyPoolError):
    """A retry was requested but the request body cannot be replayed."""


class ProvisioningError(ProxyPoolError):
    """A managed SSH/GOST node could not be provisioned."""
