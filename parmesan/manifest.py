"""Manifest: the record of which graph parms have been surfaced to the panel.

The manifest stores *decisions*, never values-of-record. Under the
graph-as-master model the node graph owns every value; the manifest keeps
only ``last_synced`` as a watermark so the diff engine can tell which side
moved since the last reconciliation.

Persisted as JSON in the panel node's userData under MANIFEST_KEY. Source
nodes are stamped with a UUID in their own userData under STAMP_KEY so
promoted entries survive renames and reparenting, which silently break
path-based lookups.

This module is pure Python: it does not import hou and is testable off-host.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

# userData keys
MANIFEST_KEY = "parmesan_manifest"
STAMP_KEY = "parmesan_uuid"


def new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Entry:
    """One surfaced control: a panel parm bound to a parm inside the graph."""

    # identity
    id: str = field(default_factory=new_id)
    gui_parm: str = ""            # spare parm name on the panel node
    source_uuid: str = ""         # STAMP_KEY value on the source node
    source_parm: str = ""         # parm name on the source node
    index: int = 0                # component within a parm tuple

    # diagnostics / fast path (never trusted as identity)
    source_path_hint: str = ""

    # presentation (user-editable; regeneration must preserve these)
    label: str = ""
    user_label: Optional[str] = None
    folder: str = ""
    order: int = 0

    # binding state
    parm_type: str = ""           # "float" | "int" | "toggle" | "string" | "ramp"
    last_synced: Any = None       # watermark, NOT the value of record
    score: float = 0.0
    why: str = ""                 # why scoring surfaced this, in plain words
    pinned: bool = False          # user explicitly kept this; never auto-drop

    def display_label(self) -> str:
        return self.user_label or self.label or self.gui_parm


@dataclass
class Manifest:
    version: int = SCHEMA_VERSION
    entries: List[Entry] = field(default_factory=list)

    # ---- lookup -------------------------------------------------------

    def by_gui_parm(self, name: str) -> Optional[Entry]:
        for e in self.entries:
            if e.gui_parm == name:
                return e
        return None

    def by_id(self, entry_id: str) -> Optional[Entry]:
        for e in self.entries:
            if e.id == entry_id:
                return e
        return None

    def bound_keys(self) -> set:
        """(source_uuid, source_parm, index) already surfaced.

        The scoring pass excludes these so previously-promoted parms cannot
        re-enter the candidate pool and crowd out genuinely new edits.
        """
        return {(e.source_uuid, e.source_parm, e.index) for e in self.entries}

    def next_order(self) -> int:
        return max((e.order for e in self.entries), default=-1) + 1

    # ---- naming -------------------------------------------------------

    def unique_gui_name(self, base: str) -> str:
        """Disambiguate across nodes that expose identically-named parms."""
        taken = {e.gui_parm for e in self.entries}
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in base).strip("_")
        safe = safe or "parm"
        if safe[0].isdigit():
            safe = "p_" + safe
        if safe not in taken:
            return safe
        n = 2
        while f"{safe}{n}" in taken:
            n += 1
        return f"{safe}{n}"

    # ---- serialization ------------------------------------------------

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(
            {"version": self.version, "entries": [asdict(e) for e in self.entries]},
            indent=indent,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> "Manifest":
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return cls()
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Manifest":
        version = int(data.get("version", SCHEMA_VERSION))
        data = migrate(data, version)
        known = {f for f in Entry.__dataclass_fields__}  # tolerate newer files
        entries = [
            Entry(**{k: v for k, v in raw.items() if k in known})
            for raw in data.get("entries", [])
        ]
        return cls(version=SCHEMA_VERSION, entries=entries)


def migrate(data: Dict[str, Any], from_version: int) -> Dict[str, Any]:
    """Forward-migrate an older manifest. No-op at v1; the hook exists so
    shipped assets keep loading once the schema moves."""
    if from_version > SCHEMA_VERSION:
        # Written by a newer plugin. Unknown fields are dropped by from_dict
        # rather than raising, so the panel degrades instead of dying.
        return data
    return data
