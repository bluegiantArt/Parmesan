"""Runtime: reading the graph, writing through, and applying a refresh.

This is the only module that touches hou during normal operation, and the
only one the generated callbacks import. Keeping it separate from diff.py
means the reconciliation logic stays testable off-host, and means this file
can be edited and reloaded without regenerating any interface.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from . import diff as diffmod
from .manifest import MANIFEST_KEY, STAMP_KEY, Entry, Manifest, new_id

try:
    import hou
except ImportError:  # pragma: no cover
    hou = None


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

def load_manifest(node) -> Manifest:
    return Manifest.from_json(node.userData(MANIFEST_KEY) or "")


def save_manifest(node, manifest: Manifest) -> None:
    node.setUserData(MANIFEST_KEY, manifest.to_json())


def stamp(node) -> str:
    """Return the node's stable id, assigning one if absent.

    Paths are not identity: a rename or reparent breaks them silently. The
    stamp is what lets a promoted entry survive both.
    """
    existing = node.userData(STAMP_KEY)
    if existing:
        return existing
    token = new_id()
    node.setUserData(STAMP_KEY, token)
    return token


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def build_stamp_index(root) -> Dict[str, Any]:
    """Map stamp -> node for one scan. Built once per refresh; a per-entry
    subtree walk would be quadratic on a large graph."""
    index = {}
    for node in root.allSubChildren(recurse_in_locked_nodes=False):
        token = node.userData(STAMP_KEY)
        if token:
            index[token] = node
    return index


def resolve_source(entry: Entry, root, index: Optional[Dict[str, Any]] = None):
    """Find an entry's source node. Path hint first (cheap, usually right),
    verified against the stamp; falls back to the stamp index."""
    if entry.source_path_hint:
        node = hou.node(entry.source_path_hint)
        if node is not None and node.userData(STAMP_KEY) == entry.source_uuid:
            return node
    if index is None:
        index = build_stamp_index(root)
    return index.get(entry.source_uuid)


def _parm_of(node, entry: Entry):
    if node is None:
        return None
    parm = node.parm(entry.source_parm)
    if parm is None:
        tuple_parm = node.parmTuple(entry.source_parm)
        if tuple_parm is not None and entry.index < len(tuple_parm):
            parm = tuple_parm[entry.index]
    return parm


def parm_kind(parm) -> str:
    """Normalize a Houdini parm template to our manifest type vocabulary."""
    tmpl = parm.parmTemplate()
    t = tmpl.type()
    if t == hou.parmTemplateType.Float:
        return "float"
    if t == hou.parmTemplateType.Int:
        return "menu" if tmpl.menuItems() else "int"
    if t == hou.parmTemplateType.Toggle:
        return "toggle"
    if t == hou.parmTemplateType.String:
        return "string"
    if t == hou.parmTemplateType.Ramp:
        return "ramp"
    return "unsupported"


# --------------------------------------------------------------------------
# observation  (hou -> plain dicts, for the pure diff engine)
# --------------------------------------------------------------------------

def observe(panel_node, root, manifest: Manifest) -> Dict[str, Dict[str, Any]]:
    """Snapshot both sides of every binding. Deliberately returns plain data
    so diff.compute() never needs hou."""
    index = build_stamp_index(root)
    observed: Dict[str, Dict[str, Any]] = {}

    for entry in manifest.entries:
        source_node = resolve_source(entry, root, index)
        source_parm = _parm_of(source_node, entry)
        gui_parm = panel_node.parm(entry.gui_parm)

        record: Dict[str, Any] = {
            "source_present": source_parm is not None,
            "gui_present": gui_parm is not None,
            "source_value": None,
            "gui_value": None,
            "parm_type": entry.parm_type,
        }
        if source_parm is not None:
            record["source_value"] = source_parm.eval()
            record["parm_type"] = parm_kind(source_parm)
            # Keep the hint fresh so the cheap path keeps working.
            entry.source_path_hint = source_node.path()
        if gui_parm is not None:
            record["gui_value"] = gui_parm.eval()

        observed[entry.id] = record

    return observed


def refresh(panel_node, root, candidates: Optional[List[Dict]] = None):
    """Re-read the graph and report what moved. Read-only: applies nothing."""
    manifest = load_manifest(panel_node)
    observed = observe(panel_node, root, manifest)
    result = diffmod.compute(manifest, observed, candidates)
    save_manifest(panel_node, manifest)  # persists refreshed path hints
    return manifest, result


# --------------------------------------------------------------------------
# write-through  (panel -> graph), invoked by the generated callback
# --------------------------------------------------------------------------

def on_gui_parm_changed(kwargs: Dict[str, Any]) -> None:
    """Parameter callback entry point.

    Wrapped in an undo group so one Ctrl+Z reverts both the panel parm and
    the graph write it caused. Without the group they unwind separately and
    undo appears to half-work, which is worse than not supporting it.
    """
    node = kwargs.get("node")
    parm = kwargs.get("parm")
    if node is None or parm is None:
        return

    manifest = load_manifest(node)
    entry = manifest.by_gui_parm(parm.name())
    if entry is None:
        return  # a hand-added parm, not ours

    root = _search_root(node)
    source_node = resolve_source(entry, root)
    source_parm = _parm_of(source_node, entry)
    if source_parm is None:
        hou.ui.setStatusMessage(
            f"'{entry.display_label()}' is orphaned - its node is gone. Refresh to review.",
            severity=hou.severityType.Warning,
        )
        return

    if source_parm.isLocked():
        hou.ui.setStatusMessage(
            f"'{entry.display_label()}' is locked on {source_node.path()}; not written.",
            severity=hou.severityType.Warning,
        )
        return

    value = parm.eval()
    with hou.undos.group(f"Set {entry.display_label()}"):
        source_parm.set(value)

    entry.last_synced = value
    entry.source_path_hint = source_node.path()
    save_manifest(node, manifest)


def write_through(panel_node, manifest: Manifest, entry: Entry, value) -> Optional[str]:
    """Write a panel-side edit down to the source parm. Returns a problem, or
    None on success.

    Shared by the generated parm callback and the panel's own widgets, so
    there is exactly one path that writes to the graph and exactly one place
    where the watermark is advanced.
    """
    root = _search_root(panel_node)
    source_node = resolve_source(entry, root)
    source_parm = _parm_of(source_node, entry)
    if source_parm is None:
        return f"{entry.display_label()}: source node is gone"
    if source_parm.isLocked():
        return f"{entry.display_label()}: locked on {source_node.path()}"

    with hou.undos.group(f"Set {entry.display_label()}"):
        # An expression on the target is replaced by a direct value, which is
        # what dragging its slider in Houdini does too. It is inside the undo
        # group, so one Ctrl+Z puts the expression back.
        try:
            source_parm.deleteAllKeyframes()
        except (AttributeError, hou.OperationFailed):
            pass
        source_parm.set(value)

    entry.last_synced = value
    entry.source_path_hint = source_node.path()

    # Keep the control node's spare parm in step, so the native parameter
    # view and the panel never disagree about what the value is.
    gui_parm = panel_node.parm(entry.gui_parm)
    if gui_parm is not None:
        try:
            gui_parm.set(value)
        except hou.OperationFailed:
            pass

    save_manifest(panel_node, manifest)
    return None


def _search_root(panel_node):
    """Where to look for source nodes. The panel's parent covers the common
    case of a panel living alongside the graph it drives; falling back to
    the scene root keeps deeply-relocated panels working."""
    return panel_node.parent() or hou.node("/")


# --------------------------------------------------------------------------
# applying a reviewed diff
# --------------------------------------------------------------------------

def apply_changes(panel_node, root, manifest: Manifest, changes) -> Tuple[int, List[str]]:
    """Apply selected changes. Returns (applied_count, problems).

    Only PULL and PUSH are applied here. DIVERGED, ORPHANED, DANGLING and
    TYPE_CHANGED are surfaced to the artist instead -- silently picking a
    winner on a conflict is how a sync tool loses trust.
    """
    index = build_stamp_index(root)
    applied = 0
    problems: List[str] = []

    with hou.undos.group("Parmesan refresh"):
        for change in changes:
            entry = manifest.by_id(change.entry_id) if change.entry_id else None
            if entry is None:
                continue

            source_parm = _parm_of(resolve_source(entry, root, index), entry)
            gui_parm = panel_node.parm(entry.gui_parm)
            if source_parm is None or gui_parm is None:
                problems.append(f"{entry.display_label()}: binding broken")
                continue

            try:
                if change.kind is diffmod.ChangeKind.PULL:
                    gui_parm.set(source_parm.eval())
                    entry.last_synced = source_parm.eval()
                elif change.kind is diffmod.ChangeKind.PUSH:
                    source_parm.set(gui_parm.eval())
                    entry.last_synced = gui_parm.eval()
                else:
                    continue
                applied += 1
            except hou.OperationFailed as exc:
                problems.append(f"{entry.display_label()}: {exc}")

    save_manifest(panel_node, manifest)
    return applied, problems
