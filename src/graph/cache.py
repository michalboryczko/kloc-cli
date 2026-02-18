"""Binary cache for pre-indexed SoT data.

Saves the fully-built SoTIndex to a .sot.cache file for near-instant
subsequent loads (~1.4s vs ~12s from JSON on large datasets).

Uses msgspec.msgpack for safe, fast serialization (no pickle).
"""

import os
import logging
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import msgspec

from .loader import NodeSpec, EdgeSpec

if TYPE_CHECKING:
    from .index import SoTIndex
    from .precompute import PrecomputedGraph

logger = logging.getLogger(__name__)

CACHE_VERSION = 2  # Bumped from 1: switched from pickle to msgspec.msgpack


class PrecomputedCache(msgspec.Struct):
    """Serializable representation of PrecomputedGraph data."""
    extends: dict[str, str]
    implements: dict[str, list[str]]
    overrides: dict[str, str]
    contains: dict[str, str]
    ancestors: dict[str, list[str]]
    descendants: dict[str, list[str]]
    all_interfaces: dict[str, list[str]]
    override_root: dict[str, str]
    override_chain_up: dict[str, list[str]]
    override_chain_down: dict[str, list[str]]
    containment_path: dict[str, list[str]]


class CacheData(msgspec.Struct):
    """Full cache structure for msgspec.msgpack serialization."""
    source_mtime: float
    source_size: int
    cache_version: int
    version: str
    metadata: dict
    nodes: dict[str, NodeSpec]
    edges: list[EdgeSpec]
    symbol_to_id: dict[str, str]
    fqn_to_ids: dict[str, list[str]]
    name_to_ids: dict[str, list[str]]
    outgoing: dict[str, dict[str, list[EdgeSpec]]]
    incoming: dict[str, dict[str, list[EdgeSpec]]]
    edges_by_parameter: dict[str, list[EdgeSpec]]
    precomputed: Optional[PrecomputedCache] = None


def get_cache_path(sot_path: Path) -> Path:
    """Return .sot.cache path for a given sot.json."""
    return sot_path.parent / ".sot.cache"


def _precomputed_to_cache(pg: "PrecomputedGraph") -> PrecomputedCache:
    """Convert PrecomputedGraph to serializable cache struct."""
    return PrecomputedCache(
        extends=dict(pg.extends),
        implements={k: sorted(v) for k, v in pg.implements.items()},
        overrides=dict(pg.overrides),
        contains=dict(pg.contains),
        ancestors=dict(pg.ancestors),
        descendants=dict(pg.descendants),
        all_interfaces={k: sorted(v) for k, v in pg.all_interfaces.items()},
        override_root=dict(pg.override_root),
        override_chain_up=dict(pg.override_chain_up),
        override_chain_down=dict(pg.override_chain_down),
        containment_path=dict(pg.containment_path),
    )


def _cache_to_precomputed(pc: PrecomputedCache) -> "PrecomputedGraph":
    """Reconstruct PrecomputedGraph from cached data."""
    from .precompute import PrecomputedGraph
    from collections import defaultdict

    pg = PrecomputedGraph()
    pg.extends = dict(pc.extends)
    pg.implements = defaultdict(set, {k: set(v) for k, v in pc.implements.items()})
    pg.overrides = dict(pc.overrides)
    pg.contains = dict(pc.contains)
    pg.ancestors = dict(pc.ancestors)
    pg.descendants = dict(pc.descendants)
    pg.all_interfaces = {k: set(v) for k, v in pc.all_interfaces.items()}
    pg.override_root = dict(pc.override_root)
    pg.override_chain_up = dict(pc.override_chain_up)
    pg.override_chain_down = dict(pc.override_chain_down)
    pg.containment_path = dict(pc.containment_path)
    return pg


_encoder = msgspec.msgpack.Encoder()
_decoder = msgspec.msgpack.Decoder(CacheData)


def write_cache(sot_path: Path, index: "SoTIndex") -> Optional[Path]:
    """Serialize the built index to .sot.cache using msgspec.msgpack.

    Args:
        sot_path: Path to the source sot.json file.
        index: The fully-built SoTIndex to cache.

    Returns:
        Path to the cache file, or None if write failed.
    """
    cache_path = get_cache_path(sot_path)
    try:
        precomputed_cache = None
        if index._precomputed is not None:
            precomputed_cache = _precomputed_to_cache(index._precomputed)

        cache_data = CacheData(
            source_mtime=os.path.getmtime(sot_path),
            source_size=os.path.getsize(sot_path),
            cache_version=CACHE_VERSION,
            version=index.version,
            metadata=index.metadata,
            nodes=dict(index.nodes),
            edges=list(index.edges),
            symbol_to_id=dict(index.symbol_to_id),
            fqn_to_ids=dict(index.fqn_to_ids),
            name_to_ids=dict(index.name_to_ids),
            outgoing=dict(index.outgoing),
            incoming=dict(index.incoming),
            edges_by_parameter=dict(index.edges_by_parameter),
            precomputed=precomputed_cache,
        )
        encoded = _encoder.encode(cache_data)
        with open(cache_path, "wb") as f:
            f.write(encoded)
        return cache_path
    except (OSError, msgspec.EncodeError) as e:
        logger.debug(f"Failed to write cache: {e}")
        return None


def read_cache(cache_path: Path, sot_path: Path) -> Optional[dict]:
    """Load index data from cache if valid.

    Args:
        cache_path: Path to the .sot.cache file.
        sot_path: Path to the source sot.json file.

    Returns:
        Dict with cached index data, or None if cache is stale/missing/corrupt.
    """
    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "rb") as f:
            raw = f.read()
        cache_data = _decoder.decode(raw)
    except (OSError, msgspec.DecodeError, ValueError) as e:
        logger.debug(f"Failed to read cache: {e}")
        return None

    # Validate cache version
    if cache_data.cache_version != CACHE_VERSION:
        logger.debug("Cache version mismatch")
        return None

    # Check source file hasn't changed
    try:
        current_mtime = os.path.getmtime(sot_path)
        current_size = os.path.getsize(sot_path)
    except OSError:
        return None

    if (cache_data.source_mtime != current_mtime or
            cache_data.source_size != current_size):
        logger.debug("Source file changed, cache invalidated")
        return None

    # Reconstruct PrecomputedGraph if cached
    precomputed = None
    if cache_data.precomputed is not None:
        precomputed = _cache_to_precomputed(cache_data.precomputed)

    return {
        "version": cache_data.version,
        "metadata": cache_data.metadata,
        "nodes": cache_data.nodes,
        "edges": cache_data.edges,
        "symbol_to_id": cache_data.symbol_to_id,
        "fqn_to_ids": cache_data.fqn_to_ids,
        "name_to_ids": cache_data.name_to_ids,
        "outgoing": cache_data.outgoing,
        "incoming": cache_data.incoming,
        "edges_by_parameter": cache_data.edges_by_parameter,
        "precomputed": precomputed,
    }
