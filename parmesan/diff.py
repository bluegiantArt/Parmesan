"""Diff engine: reconcile the manifest against an observation of the graph.

Pure Python by design. ``observe()`` in sync.py does the hou-dependent
reading and hands this module plain dicts, so the decision logic is fully
testable without a Houdini install -- which is most of the logic worth
testing.

Three-way comparison per entry, against the ``last_synced`` watermark:

    source == watermark, gui == watermark  -> IN_SYNC      (do nothing)
    source != watermark, gui == watermark  -> PULL         (graph moved)
    source == watermark, gui != watermark  -> PUSH         (panel moved)
    source != watermark, gui != watermark  -> DIVERGED     (ask the user)

Under graph-as-master with working callbacks, PUSH should be rare: the
callback writes through on edit. It is detected anyway because a callback
can fail, be stripped, or be bypassed by a script setting the parm directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .manifest import Entry, Manifest

# Relative tolerance for float comparison. Houdini round-trips floats through
# string form in places, so exact equality produces phantom diffs.
FLOAT_REL_TOL = 1e-9
FLOAT_ABS_TOL = 1e-12


class ChangeKind(Enum):
    IN_SYNC = "in_sync"
    PULL = "pull"                  # graph -> panel
    PUSH = "push"                  # panel -> graph
    DIVERGED = "diverged"          # both moved; needs a decision
    ORPHANED = "orphaned"          # source node is gone
    DANGLING = "dangling"          # panel parm vanished from the interface
    TYPE_CHANGED = "type_changed"  # parm type no longer matches the manifest
    NEW_CANDIDATE = "new_candidate"


#: Kinds that refresh may apply without asking.
AUTO_APPLICABLE = frozenset({ChangeKind.PULL})

#: Kinds that represent a problem the artist should see.
NEEDS_ATTENTION = frozenset(
    {ChangeKind.DIVERGED, ChangeKind.ORPHANED, ChangeKind.DANGLING, ChangeKind.TYPE_CHANGED}
)


#: What each kind is called in the UI. The enum names are for code; an artist
#: reading a refresh list needs to know what happened and what to do, so the
#: wording is stated as cause plus consequence rather than as a status word.
KIND_LABELS = {
    ChangeKind.IN_SYNC: "Up to date",
    ChangeKind.PULL: "Changed in the graph",
    ChangeKind.PUSH: "Changed in the panel",
    ChangeKind.DIVERGED: "Changed in both places",
    ChangeKind.ORPHANED: "Source node is gone",
    ChangeKind.DANGLING: "Control was deleted",
    ChangeKind.TYPE_CHANGED: "Parameter type changed",
    ChangeKind.NEW_CANDIDATE: "Suggested",
}

#: The action offered for each kind, phrased as what will happen if applied.
KIND_ACTIONS = {
    ChangeKind.PULL: "Update the panel to match",
    ChangeKind.PUSH: "Write the panel value to the graph",
    ChangeKind.DIVERGED: "Choose which value wins",
    ChangeKind.ORPHANED: "Remove this control",
    ChangeKind.DANGLING: "Rebuild or remove this control",
    ChangeKind.TYPE_CHANGED: "Remove and re-add this control",
    ChangeKind.NEW_CANDIDATE: "Add as a control",
}


def human_kind(kind: "ChangeKind") -> str:
    return KIND_LABELS.get(kind, kind.value)


def human_action(kind: "ChangeKind") -> str:
    return KIND_ACTIONS.get(kind, "")


@dataclass
class Change:
    kind: ChangeKind
    entry_id: Optional[str] = None
    label: str = ""
    source_value: Any = None
    gui_value: Any = None
    watermark: Any = None
    detail: str = ""
    candidate: Optional[Dict[str, Any]] = None  # populated for NEW_CANDIDATE

    @property
    def is_actionable(self) -> bool:
        return self.kind is not ChangeKind.IN_SYNC


@dataclass
class DiffResult:
    changes: List[Change] = field(default_factory=list)

    def of(self, *kinds: ChangeKind) -> List[Change]:
        wanted = set(kinds)
        return [c for c in self.changes if c.kind in wanted]

    @property
    def actionable(self) -> List[Change]:
        return [c for c in self.changes if c.is_actionable]

    @property
    def badge_count(self) -> int:
        """What the Refresh button advertises. In-sync entries and merely
        suggested candidates do not nag; problems and real updates do."""
        return len(self.of(*(AUTO_APPLICABLE | NEEDS_ATTENTION)))

    def summary(self) -> str:
        if not self.actionable:
            return "Up to date"
        counts: Dict[str, int] = {}
        for c in self.actionable:
            counts[c.kind.value] = counts.get(c.kind.value, 0) + 1
        return ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in sorted(counts.items()))


def values_equal(a: Any, b: Any) -> bool:
    """Tolerant comparison. Floats get a relative epsilon; sequences compare
    element-wise; everything else falls back to ==."""
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        diff = abs(float(a) - float(b))
        if diff <= FLOAT_ABS_TOL:
            return True
        return diff <= FLOAT_REL_TOL * max(abs(float(a)), abs(float(b)))
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))
    return a == b


def compute(
    manifest: Manifest,
    observed: Dict[str, Dict[str, Any]],
    candidates: Optional[List[Dict[str, Any]]] = None,
) -> DiffResult:
    """Reconcile.

    ``observed`` maps entry id -> {
        "source_present": bool,   # source node resolved by UUID
        "gui_present": bool,      # spare parm still on the interface
        "source_value": Any,
        "gui_value": Any,
        "parm_type": str,
    }

    ``candidates`` are scored parms not currently in the manifest, already
    filtered against ``manifest.bound_keys()`` by the scoring pass.
    """
    result = DiffResult()

    for entry in manifest.entries:
        obs = observed.get(entry.id)
        if obs is None:
            result.changes.append(
                _change(ChangeKind.ORPHANED, entry, detail="Not found in scan")
            )
            continue

        if not obs.get("source_present", False):
            result.changes.append(
                _change(
                    ChangeKind.ORPHANED,
                    entry,
                    gui_value=obs.get("gui_value"),
                    detail=f"Source node missing (was {entry.source_path_hint or '?'})",
                )
            )
            continue

        if not obs.get("gui_present", False):
            result.changes.append(
                _change(
                    ChangeKind.DANGLING,
                    entry,
                    source_value=obs.get("source_value"),
                    detail="Panel parm was removed from the interface",
                )
            )
            continue

        obs_type = obs.get("parm_type", entry.parm_type)
        if entry.parm_type and obs_type and obs_type != entry.parm_type:
            result.changes.append(
                _change(
                    ChangeKind.TYPE_CHANGED,
                    entry,
                    source_value=obs.get("source_value"),
                    gui_value=obs.get("gui_value"),
                    detail=f"{entry.parm_type} -> {obs_type}",
                )
            )
            continue

        src = obs.get("source_value")
        gui = obs.get("gui_value")
        src_moved = not values_equal(src, entry.last_synced)
        gui_moved = not values_equal(gui, entry.last_synced)

        if src_moved and gui_moved:
            # Both sides moved. If they happen to agree, there is nothing to
            # reconcile -- just advance the watermark.
            kind = ChangeKind.IN_SYNC if values_equal(src, gui) else ChangeKind.DIVERGED
        elif src_moved:
            kind = ChangeKind.PULL
        elif gui_moved:
            kind = ChangeKind.PUSH
        else:
            kind = ChangeKind.IN_SYNC

        result.changes.append(
            _change(kind, entry, source_value=src, gui_value=gui)
        )

    for cand in candidates or []:
        result.changes.append(
            Change(
                kind=ChangeKind.NEW_CANDIDATE,
                label=cand.get("label") or cand.get("source_parm", ""),
                source_value=cand.get("value"),
                detail=cand.get("reason", ""),
                candidate=cand,
            )
        )

    return result


def _change(kind: ChangeKind, entry: Entry, **kw: Any) -> Change:
    return Change(
        kind=kind,
        entry_id=entry.id,
        label=entry.display_label(),
        watermark=entry.last_synced,
        **kw,
    )
