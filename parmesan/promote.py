"""Promoting graph parms onto the panel node, and unpromoting them again.

This is the write side of curation: turning "the artist pointed at this
parameter" into a manifest entry plus a generated control. It needs hou (it
reads real parm templates to lift ranges and menus), so the pure decision
logic stays in manifest.py and diff.py.

Presentation hints are lifted from the source parm once, at promote time,
rather than re-derived on every rebuild: a source parm's UI range is a
starting suggestion, and an artist who widens the panel slider afterwards
should not have it silently reset by the next refresh.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import callbacks, sync
from .manifest import Entry, Manifest

try:
    import hou
except ImportError:  # pragma: no cover
    hou = None


#: Parm kinds we can bind through a scalar write-through callback.
SUPPORTED_KINDS = frozenset({"float", "int", "toggle", "string", "menu"})


def compose_label(node_name: str, parm_label: str) -> str:
    """Human label for a surfaced control.

    Qualified by node name because the same parm label appears on dozens of
    nodes in a real graph, and "Scale" three times in one panel is useless.
    The node name is dropped when the parm label already contains it, which
    is common on HDAs whose parms are named after the asset.
    """
    node_name = (node_name or "").strip()
    parm_label = (parm_label or "").strip()
    if not parm_label:
        return node_name
    if not node_name:
        return parm_label
    if node_name.lower() in parm_label.lower():
        return parm_label
    return f"{node_name} / {parm_label}"


def _require_hou() -> None:
    if hou is None:
        raise RuntimeError("This function requires a running Houdini session.")


# --------------------------------------------------------------------------
# reading presentation hints off a source parm
# --------------------------------------------------------------------------

def spec_for(parm) -> Dict[str, Any]:
    """Lift default / range / menu / help from a source parm.

    Wrapped broadly: parm templates vary across Houdini versions and asset
    authors do strange things. A missing hint costs a less-tailored slider,
    which is survivable; an exception here would abort the whole promote.
    """
    _require_hou()
    spec: Dict[str, Any] = {}
    try:
        tmpl = parm.parmTemplate()
        index = parm.componentIndex()
        spec["help"] = tmpl.help() or ""
        kind = tmpl.type()

        if kind in (hou.parmTemplateType.Float, hou.parmTemplateType.Int):
            defaults = tmpl.defaultValue()
            if index < len(defaults):
                spec["default"] = defaults[index]
            spec["min"] = tmpl.minValue()
            spec["max"] = tmpl.maxValue()
            items = tmpl.menuItems()
            if kind == hou.parmTemplateType.Int and items:
                spec["menu_items"] = tuple(items)
                spec["menu_labels"] = tuple(tmpl.menuLabels())
                spec["default_index"] = int(spec.get("default", 0) or 0)
        elif kind == hou.parmTemplateType.Toggle:
            spec["default"] = bool(tmpl.defaultValue())
        elif kind == hou.parmTemplateType.String:
            defaults = tmpl.defaultValue()
            if index < len(defaults):
                spec["default"] = defaults[index]
            spec["string_type"] = tmpl.stringType()
    except Exception:  # noqa: BLE001 - hint extraction is never load-bearing
        spec.setdefault("help", "")
    return spec


def specs_for(panel_node, root, manifest: Manifest) -> Dict[str, Dict[str, Any]]:
    """Gather specs for every entry, keyed by entry id for build_interface."""
    _require_hou()
    index = sync.build_stamp_index(root)
    specs: Dict[str, Dict[str, Any]] = {}
    for entry in manifest.entries:
        source_parm = sync._parm_of(sync.resolve_source(entry, root, index), entry)
        if source_parm is not None:
            specs[entry.id] = spec_for(source_parm)
    return specs


# --------------------------------------------------------------------------
# candidates
# --------------------------------------------------------------------------

def is_promotable(parm) -> bool:
    """Can this parm be surfaced at all?

    Hidden parms are excluded because surfacing something the asset author
    deliberately buried is usually a mistake, and ramps/multiparms because
    build_parm_template() cannot generate a control for them.
    """
    _require_hou()
    try:
        if parm.isHidden():
            return False
        if parm.isMultiParmInstance():
            return False
    except AttributeError:  # older builds; fall through to the type check
        pass
    return sync.parm_kind(parm) in SUPPORTED_KINDS


def describe_candidate(parm) -> Dict[str, Any]:
    """Flatten a parm into the plain dict the panel and scoring pass share.

    ``edited`` is the isAtDefault() signal -- the strongest evidence that a
    parameter matters to this scene, and the reason nothing in this project
    writes channel references onto source parms.
    """
    _require_hou()
    node = parm.node()
    try:
        edited = not parm.isAtDefault()
    except Exception:  # noqa: BLE001
        edited = False
    try:
        animated = bool(parm.keyframes())
    except Exception:  # noqa: BLE001
        animated = False
    return {
        "node_path": node.path(),
        "node_name": node.name(),
        "source_parm": parm.name(),
        "index": parm.componentIndex(),
        "parm_label": parm.description(),
        "label": compose_label(node.name(), parm.description()),
        "parm_type": sync.parm_kind(parm),
        "value": parm.eval(),
        "edited": edited,
        "animated": animated,
        "locked": parm.isLocked(),
    }


def candidates_on(node, manifest: Optional[Manifest] = None) -> List[Dict[str, Any]]:
    """Every promotable parm on one node, minus anything already surfaced."""
    _require_hou()
    bound = manifest.bound_keys() if manifest is not None else set()
    stamp = node.userData(sync.STAMP_KEY) or ""
    out = []
    for parm in node.parms():
        if not is_promotable(parm):
            continue
        if (stamp, parm.name(), parm.componentIndex()) in bound:
            continue
        out.append(describe_candidate(parm))
    # Edited parms first: the ones the artist touched are the ones they are
    # most likely looking for in a long list.
    out.sort(key=lambda c: (not c["edited"], c["label"].lower()))
    return out


# --------------------------------------------------------------------------
# promote / unpromote
# --------------------------------------------------------------------------

def promote(panel_node, parms: Iterable, folder: str = "") -> Tuple[List[Entry], List[str]]:
    """Surface ``parms`` on ``panel_node``. Returns (added, problems).

    Idempotent per (node, parm, component): promoting something twice is a
    no-op rather than a duplicate control.
    """
    _require_hou()
    manifest = sync.load_manifest(panel_node)
    root = sync._search_root(panel_node)
    added: List[Entry] = []
    problems: List[str] = []

    for parm in parms:
        node = parm.node()
        if node.path() == panel_node.path():
            problems.append(f"{parm.name()}: cannot surface a control onto itself")
            continue
        if not is_promotable(parm):
            problems.append(f"{parm.name()}: unsupported parameter type")
            continue

        token = sync.stamp(node)
        key = (token, parm.name(), parm.componentIndex())
        if key in manifest.bound_keys():
            continue

        entry = Entry(
            gui_parm=manifest.unique_gui_name(f"{node.name()}_{parm.name()}"),
            source_uuid=token,
            source_parm=parm.name(),
            index=parm.componentIndex(),
            source_path_hint=node.path(),
            label=compose_label(node.name(), parm.description()),
            folder=folder,
            order=manifest.next_order(),
            parm_type=sync.parm_kind(parm),
            last_synced=parm.eval(),
        )
        manifest.entries.append(entry)
        added.append(entry)

    if added:
        sync.save_manifest(panel_node, manifest)
        rebuild(panel_node, manifest)
    return added, problems


def unpromote(panel_node, entry_ids: Iterable[str]) -> int:
    """Drop entries and regenerate. The graph is untouched: values stay where
    they always were, which is the point of the whole design."""
    _require_hou()
    manifest = sync.load_manifest(panel_node)
    doomed = set(entry_ids)
    before = len(manifest.entries)
    manifest.entries = [e for e in manifest.entries if e.id not in doomed]
    removed = before - len(manifest.entries)
    if removed:
        sync.save_manifest(panel_node, manifest)
        rebuild(panel_node, manifest)
    return removed


def rename(panel_node, entry_id: str, label: str) -> bool:
    """Set a user label. Stored separately from the derived label so a later
    regeneration does not overwrite the artist's wording."""
    _require_hou()
    manifest = sync.load_manifest(panel_node)
    entry = manifest.by_id(entry_id)
    if entry is None:
        return False
    entry.user_label = label.strip() or None
    sync.save_manifest(panel_node, manifest)
    rebuild(panel_node, manifest)
    return True


def rebuild(panel_node, manifest: Optional[Manifest] = None):
    """Regenerate the panel node's controls from the manifest."""
    _require_hou()
    if manifest is None:
        manifest = sync.load_manifest(panel_node)
    root = sync._search_root(panel_node)
    specs = specs_for(panel_node, root, manifest)
    ptg = callbacks.build_interface(panel_node, manifest, specs)
    # Seed each control with the value the graph currently holds, so a fresh
    # interface reads correctly instead of showing template defaults.
    for entry in manifest.entries:
        gui_parm = panel_node.parm(entry.gui_parm)
        if gui_parm is None:
            continue
        source_parm = sync._parm_of(sync.resolve_source(entry, root), entry)
        if source_parm is None:
            continue
        try:
            gui_parm.set(source_parm.eval())
            entry.last_synced = source_parm.eval()
        except hou.OperationFailed:
            continue
    sync.save_manifest(panel_node, manifest)
    return ptg


def create_control_node(parent, name: str = "CONTROLS"):
    """Make a node to hang controls on.

    A null: it is inert, cheap, survives in any context that accepts one, and
    carries spare parms without affecting the cook.
    """
    _require_hou()
    node = parent.createNode("null", name)
    try:
        node.moveToGoodPosition()
    except Exception:  # noqa: BLE001 - layout is cosmetic
        pass
    return node
