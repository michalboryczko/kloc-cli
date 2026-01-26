"""Base query interface."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from ..graph import SoTIndex

T = TypeVar("T")


class Query(ABC, Generic[T]):
    """Base query interface.

    All queries take an index and execute against it.
    """

    def __init__(self, index: SoTIndex):
        self.index = index

    @abstractmethod
    def execute(self, **params) -> T:
        """Execute the query and return typed result."""
        pass
