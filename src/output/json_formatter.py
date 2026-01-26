"""JSON output formatter."""

import json
from typing import Any


def print_json(data: Any):
    """Print data as formatted JSON to stdout."""
    print(json.dumps(data, indent=2, ensure_ascii=False))
