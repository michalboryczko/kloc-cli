"""Symbol resolution query."""

from ..models import NodeData, ResolveResult
from .base import Query


class ResolveQuery(Query[ResolveResult]):
    """Resolve a symbol to its definition(s)."""

    def execute(self, symbol: str) -> ResolveResult:
        """Execute symbol resolution.

        Args:
            symbol: Symbol to resolve (FQN, partial, or short name).

        Returns:
            ResolveResult with list of matching candidates.
        """
        candidates = self.index.resolve_symbol(symbol)
        return ResolveResult(query=symbol, candidates=candidates)
