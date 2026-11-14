"""Built-in providers plus entry points in the group ``mediatracker.providers``."""

from importlib.metadata import entry_points
from typing import Any

from pydantic import BaseModel, ValidationError


class UnknownProvider(Exception):
    def __init__(self, kind: str, type_: str) -> None:
        self.kind = kind
        self.type = type_
        super().__init__(f"Fournisseur inconnu : {kind}/{type_}.")


class ProviderRegistry:
    def __init__(self) -> None:
        self._classes: dict[tuple[str, str], type] = {}

    def register(self, cls: type) -> None:
        key = (cls.kind, cls.type)
        self._classes[key] = cls

    def load_entry_points(self) -> None:
        try:
            found = entry_points(group="mediatracker.providers")
        except TypeError:  # pragma: no cover - older importlib
            found = entry_points().get("mediatracker.providers", [])
        for item in found:
            item.load()(self)

    def get(self, kind: str, type_: str) -> type:
        try:
            return self._classes[(kind, type_)]
        except KeyError as exc:
            raise UnknownProvider(kind, type_) from exc

    def known(self, kind: str | None = None) -> list[type]:
        classes = list(self._classes.values())
        if kind is not None:
            classes = [cls for cls in classes if cls.kind == kind]
        return sorted(classes, key=lambda cls: (cls.kind, cls.type))

    def instantiate(self, kind: str, type_: str, config: dict[str, Any]) -> Any:
        cls = self.get(kind, type_)
        model = cls.config_model().model_validate(config)
        return cls(model)

    def validate_config(self, kind: str, type_: str, config: dict[str, Any]) -> BaseModel:
        cls = self.get(kind, type_)
        try:
            return cls.config_model().model_validate(config)
        except ValidationError:
            raise


def secret_fields(model: type[BaseModel]) -> set[str]:
    schema = model.model_json_schema()
    found: set[str] = set()
    for name, prop in schema.get("properties", {}).items():
        if prop.get("secret") is True:
            found.add(name)
    return found
