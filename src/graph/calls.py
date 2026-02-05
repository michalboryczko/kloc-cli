"""Calls data loader for calls.json files.

Provides indexed access to call and value records from the scip-php calls.json output.
Used to enrich context queries with access chain information and precise reference types.
"""

from pathlib import Path
from typing import Optional

import msgspec


# =============================================================================
# Msgspec Structs for calls.json schema
# =============================================================================

class LocationSpec(msgspec.Struct, omit_defaults=True):
    """Location in source code."""
    file: str
    line: int
    col: int


class ArgumentSpec(msgspec.Struct, omit_defaults=True):
    """Argument in a call."""
    position: int
    parameter: Optional[str] = None
    value_id: Optional[str] = None
    value_expr: Optional[str] = None


class ValueRecord(msgspec.Struct, omit_defaults=True):
    """Value record from calls.json.

    Values represent data holders: parameters, locals, literals, constants, and call results.
    """
    id: str
    kind: str  # "parameter", "local", "literal", "constant", "result"
    location: LocationSpec
    symbol: Optional[str] = None
    type: Optional[str] = None
    source_call_id: Optional[str] = None
    source_value_id: Optional[str] = None


class CallRecord(msgspec.Struct, omit_defaults=True):
    """Call record from calls.json.

    Calls represent operations: method calls, property access, operators.
    """
    id: str
    kind: str  # "method", "method_static", "constructor", "access", "access_static", etc.
    kind_type: str  # "invocation", "access", "operator"
    caller: str  # SCIP symbol of enclosing method/function
    location: LocationSpec
    callee: Optional[str] = None
    return_type: Optional[str] = None
    receiver_value_id: Optional[str] = None
    arguments: list[ArgumentSpec] = []


class CallsJsonSpec(msgspec.Struct, omit_defaults=True):
    """Full calls.json specification."""
    version: str
    project_root: str
    values: list[ValueRecord] = []
    calls: list[CallRecord] = []


# Create decoder for performance
_calls_decoder = msgspec.json.Decoder(CallsJsonSpec)


# =============================================================================
# Reference Type Mapping
# =============================================================================

# Maps call.kind from calls.json to reference types
CALL_KIND_TO_REFERENCE_TYPE = {
    "method": "method_call",
    "method_static": "static_call",
    "constructor": "instantiation",
    "access": "property_access",
    "access_static": "static_property",
    "function": "function_call",
    "access_array": "array_access",
}


# =============================================================================
# CallsData Class
# =============================================================================

class CallsData:
    """Loaded calls.json data with lookup indices.

    Provides O(1) lookups for:
    - Values by ID
    - Calls by ID
    - Calls by location (file:line)

    And methods to:
    - Find call at a specific location
    - Build access chain strings from receiver_value_id
    - Get reference type from call kind
    """

    def __init__(
        self,
        version: str,
        project_root: str,
        values_by_id: dict[str, ValueRecord],
        calls_by_id: dict[str, CallRecord],
        calls_by_location: dict[str, list[CallRecord]],
    ):
        self.version = version
        self.project_root = project_root
        self.values_by_id = values_by_id
        self.calls_by_id = calls_by_id
        self.calls_by_location = calls_by_location

    @classmethod
    def load(cls, path: str | Path) -> "CallsData":
        """Load calls.json from file and build indices.

        Args:
            path: Path to the calls.json file.

        Returns:
            CallsData instance with indexed lookups.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            msgspec.DecodeError: If the file is not valid JSON.
        """
        with open(path, "rb") as f:
            data = _calls_decoder.decode(f.read())

        # Build indices
        values_by_id: dict[str, ValueRecord] = {}
        for v in data.values:
            values_by_id[v.id] = v

        calls_by_id: dict[str, CallRecord] = {}
        calls_by_location: dict[str, list[CallRecord]] = {}

        for c in data.calls:
            calls_by_id[c.id] = c
            # Index by file:line for location-based lookup
            # Note: calls.json uses 1-based lines
            loc_key = f"{c.location.file}:{c.location.line}"
            if loc_key not in calls_by_location:
                calls_by_location[loc_key] = []
            calls_by_location[loc_key].append(c)

        return cls(
            version=data.version,
            project_root=data.project_root,
            values_by_id=values_by_id,
            calls_by_id=calls_by_id,
            calls_by_location=calls_by_location,
        )

    def get_call_at(
        self, file: str, line: int, col: Optional[int] = None, callee: Optional[str] = None
    ) -> Optional[CallRecord]:
        """Find call record at location.

        Args:
            file: Source file path (relative to project root).
            line: Line number (1-based, as in calls.json).
            col: Optional column for disambiguation.
            callee: Optional callee symbol to match.

        Returns:
            Matching CallRecord or None if not found.
        """
        loc_key = f"{file}:{line}"
        calls = self.calls_by_location.get(loc_key, [])

        if not calls:
            return None

        if len(calls) == 1:
            return calls[0]

        # Multiple calls on same line - try to disambiguate
        if col is not None:
            for c in calls:
                if c.location.col == col:
                    return c

        if callee is not None:
            for c in calls:
                if c.callee and callee in c.callee:
                    return c

        # Return first match if can't disambiguate
        return calls[0]

    def build_access_chain(self, call: CallRecord) -> Optional[str]:
        """Build access chain string from receiver_value_id.

        For a call like `$this->orderRepository->save()`, builds the chain
        "$this->orderRepository" by following receiver_value_id references.

        Args:
            call: CallRecord with optional receiver_value_id.

        Returns:
            Access chain string like "$this->orderRepository" or None if no receiver.
        """
        receiver_id = call.receiver_value_id
        if not receiver_id:
            return None  # Static call or constructor

        return self._build_chain_from_value(receiver_id)

    def build_chain_for_callee(self, call: CallRecord) -> Optional[str]:
        """Build access chain for the target of a call.

        This method builds the full chain leading to the call target.
        For `$this->repo->save()`, this returns "$this->repo".

        Args:
            call: CallRecord to build chain for.

        Returns:
            Access chain string or None for static/constructor calls.
        """
        if not call.receiver_value_id:
            return None

        # Get the value record for the receiver
        receiver_value = self.values_by_id.get(call.receiver_value_id)
        if not receiver_value:
            return "?"

        # If it's a result of another call (like property access),
        # we need to trace back
        if receiver_value.kind == "result" and receiver_value.source_call_id:
            source_call = self.calls_by_id.get(receiver_value.source_call_id)
            if source_call:
                # For property access on $this (no receiver), just return the property name
                if source_call.kind == "access" and not source_call.receiver_value_id:
                    prop_name = self._extract_member_name(source_call.callee)
                    # This is a $this->property access
                    return f"$this->{prop_name}"

                # For other accesses, recurse
                return self._build_chain_from_value(call.receiver_value_id)

        return self._build_chain_from_value(call.receiver_value_id)

    def _build_chain_from_value(self, value_id: str, max_depth: int = 10) -> str:
        """Build chain by following value references.

        Args:
            value_id: Starting value ID.
            max_depth: Maximum recursion depth to prevent infinite loops.

        Returns:
            Chain string like "$this->repo" or "$param" or "?".
        """
        if max_depth <= 0:
            return "?"

        value = self.values_by_id.get(value_id)
        if not value:
            return "?"

        kind = value.kind

        if kind == "parameter":
            # Extract parameter name from symbol
            # Symbol format: "...ClassName#methodName().($paramName)"
            return self._extract_var_name(value.symbol)

        if kind == "local":
            # Local variable
            return self._extract_var_name(value.symbol)

        if kind == "result":
            # Result of a call - follow to source call
            source_call_id = value.source_call_id
            if source_call_id:
                source_call = self.calls_by_id.get(source_call_id)
                if source_call:
                    # Get the method/property name being accessed
                    member_name = self._extract_member_name(source_call.callee)

                    # For property access, format as chain
                    if source_call.kind == "access":
                        # Recurse to get receiver chain
                        if source_call.receiver_value_id:
                            receiver_chain = self._build_chain_from_value(
                                source_call.receiver_value_id, max_depth - 1
                            )
                            return f"{receiver_chain}->{member_name}"
                        return member_name

                    # For method calls, show as method()
                    if source_call.kind in ("method", "method_static"):
                        if source_call.receiver_value_id:
                            receiver_chain = self._build_chain_from_value(
                                source_call.receiver_value_id, max_depth - 1
                            )
                            return f"{receiver_chain}->{member_name}()"
                        return f"{member_name}()"

            return "?"

        if kind == "literal":
            return "(literal)"

        if kind == "constant":
            return self._extract_const_name(value.symbol)

        return "?"

    def _extract_var_name(self, symbol: Optional[str]) -> str:
        """Extract variable name from SCIP symbol.

        Examples:
            "...OrderService#createOrder().($input)" -> "$input"
            "...OrderService#createOrder().local$order@31" -> "$order"
            "...OrderService#$orderRepository." -> "$orderRepository"
        """
        if not symbol:
            return "?"

        # Check for parameter: ends with ".($name)"
        if ".($" in symbol:
            # Extract the part after .($
            start = symbol.rfind(".($") + 3
            end = symbol.rfind(")")
            if start > 2 and end > start:
                return "$" + symbol[start:end]

        # Check for local variable: contains ".local$name@"
        if ".local$" in symbol:
            start = symbol.rfind(".local$") + 7
            # Find the @ that ends the variable name
            at_pos = symbol.find("@", start)
            if at_pos > start:
                return "$" + symbol[start:at_pos]
            # No @ found, take to end
            return "$" + symbol[start:]

        # Check for property: ends with "#$name."
        if "#$" in symbol:
            start = symbol.rfind("#$") + 2
            return "$" + symbol[start:].rstrip(".")

        # Check for $this pattern
        if ".$this@" in symbol or symbol.endswith(".$this"):
            return "$this"

        return "?"

    def _extract_member_name(self, callee: Optional[str]) -> str:
        """Extract member name from callee symbol.

        Examples:
            "...OrderRepository#save()." -> "save"
            "...OrderService#$orderRepository." -> "orderRepository"
        """
        if not callee:
            return "?"

        # Method: ends with "#methodName()."
        if "#" in callee:
            start = callee.rfind("#") + 1
            name = callee[start:].rstrip("().")
            # Remove $ prefix for properties
            return name.lstrip("$")

        return "?"

    def _extract_const_name(self, symbol: Optional[str]) -> str:
        """Extract constant name from symbol."""
        if not symbol:
            return "?"

        if "#" in symbol:
            start = symbol.rfind("#") + 1
            return symbol[start:].rstrip(".")

        return symbol

    def get_reference_type(self, call: CallRecord) -> str:
        """Get reference type from call kind.

        Args:
            call: CallRecord with kind field.

        Returns:
            Reference type string like "method_call", "static_call", etc.
        """
        return CALL_KIND_TO_REFERENCE_TYPE.get(call.kind, "unknown")

    def get_constructor_at(
        self, file: str, line: int, class_symbol: Optional[str] = None
    ) -> Optional[CallRecord]:
        """Find constructor call at location, optionally matching class symbol.

        This is used to detect `new ClassName()` instantiation at a given location.
        The class_symbol can be the full SCIP symbol or just a partial match.

        Args:
            file: Source file path (relative to project root).
            line: Line number (1-based, as in calls.json).
            class_symbol: Optional class symbol to match in return_type.

        Returns:
            Matching constructor CallRecord or None if not found.
        """
        loc_key = f"{file}:{line}"
        calls = self.calls_by_location.get(loc_key, [])

        for c in calls:
            if c.kind != "constructor":
                continue

            # If no class_symbol filter, return first constructor
            if class_symbol is None:
                return c

            # Match class symbol against return_type (the instantiated class)
            if c.return_type and class_symbol in c.return_type:
                return c

        return None
