"""Ports for the external systems. The pipeline only knows these; file
implementations keep tests hermetic, the real ones are picked via config."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TicketSystem(ABC):
    @abstractmethod
    def open_incidents(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_incident(self, sys_id: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def add_work_note(self, sys_id: str, note: str) -> None: ...

    @abstractmethod
    def set_fields(self, sys_id: str, fields: dict[str, Any]) -> None: ...


class LogSource(ABC):
    @abstractmethod
    def recent_errors(self, service: str, minutes: int = 60) -> str: ...


class MetricSource(ABC):
    @abstractmethod
    def recent_series(self, service: str, minutes: int = 60) -> str: ...


class ChangeSource(ABC):
    @abstractmethod
    def recent_changes(self, service: str, minutes: int = 240) -> str: ...


class HistoryStore(ABC):
    @abstractmethod
    def similar(self, text: str, service: str, k: int = 3) -> list[dict[str, Any]]: ...


class FixPublisher(ABC):
    @abstractmethod
    def publish(self, incident, fix_hint, narrative) -> str:
        """Returns something a human can follow: path, issue URL, PR URL."""
