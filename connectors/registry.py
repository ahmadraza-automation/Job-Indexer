from typing import TypeVar

from connectors.base import BaseConnector

ConnectorType = TypeVar("ConnectorType", bound=type[BaseConnector])

_CONNECTORS: dict[str, type[BaseConnector]] = {}


def register_connector(connector_class: ConnectorType) -> ConnectorType:
    """Register a connector class using its platform key and kind."""
    if not issubclass(connector_class, BaseConnector):
        raise TypeError("Registered connectors must inherit from BaseConnector")

    platform_key = getattr(connector_class, "platform_key", "")
    kind = getattr(connector_class, "kind", "")
    if not platform_key:
        raise ValueError("A connector must define a non-empty platform_key")
    if kind not in {"ats", "board"}:
        raise ValueError("A connector kind must be 'ats' or 'board'")

    registered = _CONNECTORS.get(platform_key)
    if registered is not None and registered is not connector_class:
        raise ValueError(f"A connector is already registered for {platform_key!r}")
    _CONNECTORS[platform_key] = connector_class
    return connector_class


def get_connector(platform_key: str) -> BaseConnector:
    """Create a connector instance for a registered platform key."""
    try:
        connector_class = _CONNECTORS[platform_key]
    except KeyError as exc:
        raise KeyError(f"No connector registered for {platform_key!r}") from exc
    return connector_class()


def list_connectors() -> list[str]:
    """Return registered platform keys in a stable order."""
    return sorted(_CONNECTORS)
