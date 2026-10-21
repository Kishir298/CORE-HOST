from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrganizationEntry:
    """Represents an organized piece of C.O.R.E. information."""

    entry_id: str
    category: str
    name: str
    resource_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
