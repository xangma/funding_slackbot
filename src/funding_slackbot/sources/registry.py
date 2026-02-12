from __future__ import annotations

from typing import Callable

from funding_slackbot.config import SourceSettings

from .base import Source

SourceFactory = Callable[[SourceSettings], Source]

_REGISTRY: dict[str, SourceFactory] = {}


class SourceRegistrationError(ValueError):
    """Raised when an unknown source type is used."""


def register_source(source_type: str) -> Callable[[SourceFactory], SourceFactory]:
    def decorator(factory: SourceFactory) -> SourceFactory:
        _REGISTRY[source_type] = factory
        return factory

    return decorator


def create_source(settings: SourceSettings) -> Source:
    factory = _REGISTRY.get(settings.type)
    if factory is None:
        available = ", ".join(sorted(_REGISTRY)) or "none"
        raise SourceRegistrationError(
            f"Unknown source type '{settings.type}'. Registered source types: {available}"
        )
    return factory(settings)


def registered_source_types() -> list[str]:
    return sorted(_REGISTRY)
