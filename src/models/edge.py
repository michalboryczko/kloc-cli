"""Edge data model.

EdgeData is now an alias for EdgeSpec (msgspec Struct) to avoid conversion overhead.
"""

from ..graph.loader import EdgeSpec

# EdgeData is now an alias for EdgeSpec — no conversion needed
EdgeData = EdgeSpec
