"""Node data model.

NodeData is now an alias for NodeSpec (msgspec Struct) to avoid conversion overhead.
All computed properties (signature, display_name, location_str, start_line) are defined
on NodeSpec directly.
"""

from ..graph.loader import NodeSpec

# NodeData is now an alias for NodeSpec — no conversion needed
NodeData = NodeSpec
