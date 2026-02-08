"""Node data model."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NodeData:
    """Node from SoT JSON."""

    id: str
    kind: str
    name: str
    fqn: str
    symbol: str
    file: Optional[str]
    range: Optional[dict]
    enclosing_range: Optional[dict] = None
    documentation: list[str] = field(default_factory=list)

    # Value node fields (only set when kind == "Value")
    value_kind: Optional[str] = None    # "parameter", "local", "result", "literal", "constant"
    type_symbol: Optional[str] = None   # SCIP symbol of the value's type

    # Call node fields (only set when kind == "Call")
    call_kind: Optional[str] = None     # "method", "method_static", "constructor", "access", "access_static", "function"

    @property
    def start_line(self) -> Optional[int]:
        if self.range:
            return self.range.get("start_line")
        return None

    @property
    def location_str(self) -> str:
        """Return file:line string."""
        if self.file and self.start_line is not None:
            return f"{self.file}:{self.start_line + 1}"  # 1-based
        elif self.file:
            return self.file
        return "<unknown>"

    @property
    def signature(self) -> Optional[str]:
        """Extract method/function signature from documentation.

        SCIP PHP includes the signature in documentation, typically in the first line.
        Examples:
        - ```php\npublic function getName(): string\n```
        - public function setName(string $name): void
        - public function create(#[\\Symfony\\...\\MapRequestPayload] CreateOrderRequest $request): JsonResponse

        Returns the clean signature without visibility modifiers, e.g.:
        - setName(string $name): void
        - getName(): string
        - create(CreateOrderRequest $request): JsonResponse

        R5: Handles PHP attributes (#[...]) by stripping them from parameters.
        If the signature appears truncated (missing closing paren), falls back to
        method_name(...) shorthand.
        """
        import re

        if not self.documentation or self.kind not in ("Method", "Function"):
            return None

        for doc in self.documentation:
            # Clean up markdown code blocks
            clean = doc.replace("```php", "").replace("```", "").strip()
            # Look for function signature
            if "function " in clean:
                # Extract just the signature line(s) -- may span multiple lines
                # when attributes push content to the next line
                sig_lines = []
                capturing = False
                for line in clean.split("\n"):
                    line = line.strip()
                    if "function " in line:
                        capturing = True
                    if capturing:
                        sig_lines.append(line)
                        # Stop capturing once we have a complete signature
                        # (has closing paren and optional return type)
                        if ")" in line:
                            break

                if not sig_lines:
                    continue

                full_sig = " ".join(sig_lines)

                # Remove visibility modifiers (public, protected, private, static, final, abstract)
                full_sig = re.sub(
                    r'^(?:public\s+|protected\s+|private\s+|static\s+|final\s+|abstract\s+)*function\s+',
                    '',
                    full_sig
                )

                # R5: Strip PHP attributes (#[...]) from parameters
                # Attributes can be nested: #[Attr(args)] or #[Attr]
                full_sig = re.sub(r'#\[[^\]]*\]\s*', '', full_sig)

                # Clean up any double spaces left after attribute removal
                full_sig = re.sub(r'\s+', ' ', full_sig).strip()

                # Validate the signature is well-formed (has matching parens)
                if "(" in full_sig and ")" in full_sig:
                    return full_sig

                # If signature is malformed (missing closing paren), use shorthand
                if "(" in full_sig:
                    method_name = full_sig.split("(")[0]
                    return f"{method_name}(...)"

                return full_sig
        return None

    @property
    def display_name(self) -> str:
        """Return display name - signature for methods, FQN otherwise."""
        if self.kind in ("Method", "Function") and self.signature:
            # For methods, show class::method_signature
            if "::" in self.fqn:
                class_part = self.fqn.rsplit("::", 1)[0]
                return f"{class_part}::{self.signature}"
            return self.signature
        return self.fqn
