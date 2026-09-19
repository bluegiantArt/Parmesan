"""Building the panel interface and the write-through callbacks.

Under graph-as-master no expression is ever written onto a source parm.
Binding is done with a parameter callback script instead: the panel parm
carries a Python callback that writes its value down into the graph on
change. The source parm stays unlocked and fully editable in the node, and
``isAtDefault()`` keeps its original meaning for the scoring pass -- which
it would not if we channel-referenced.

The generated callback is a three-line bootstrap, deliberately. Authoring
real logic into a parm-template string is unmaintainable and undebuggable;
everything of substance lives in sync.py, which can be edited and reloaded
without regenerating a single interface.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .manifest import Entry, Manifest

try:  # absent off-host; the module still imports so tests can run
    import hou
except ImportError:  # pragma: no cover
    hou = None


#: Written into every generated panel parm. Keep this stable -- changing it
#: means regenerating existing interfaces.
CALLBACK_SCRIPT = "\n".join(
    [
        "try:",
        "    from parmesan import sync",
        "except ImportError:",
        "    raise hou.NodeError(",
        "        'parmesan is not on the Houdini PYTHONPATH; '",
        "        'panel edits will not reach the graph.')",
        "sync.on_gui_parm_changed(kwargs)",
    ]
)


def _require_hou() -> None:
    if hou is None:
        raise RuntimeError("This function requires a running Houdini session.")


# --------------------------------------------------------------------------
# parm template construction
# --------------------------------------------------------------------------

def build_parm_template(entry: Entry, spec: Optional[Dict[str, Any]] = None):
    """Create the panel-side ParmTemplate for one entry.

    ``spec`` carries presentation hints lifted from the source parm at scan
    time (range, menu items, help). Ranges are copied but never made strict:
    a source parm's UI range is a suggestion, and clamping the panel control
    tighter than the graph allows is a trap.
    """
    _require_hou()
    spec = spec or {}
    name = entry.gui_parm
    label = entry.display_label()
    help_text = spec.get("help") or _default_help(entry)

    kind = entry.parm_type
    if kind == "float":
        tmpl = hou.FloatParmTemplate(
            name, label, 1,
            default_value=(spec.get("default", 0.0),),
            min=spec.get("min", 0.0),
            max=spec.get("max", 10.0),
            min_is_strict=False,
            max_is_strict=False,
        )
    elif kind == "int":
        tmpl = hou.IntParmTemplate(
            name, label, 1,
            default_value=(int(spec.get("default", 0)),),
            min=int(spec.get("min", 0)),
            max=int(spec.get("max", 10)),
            min_is_strict=False,
            max_is_strict=False,
        )
    elif kind == "toggle":
        tmpl = hou.ToggleParmTemplate(
            name, label, default_value=bool(spec.get("default", False))
        )
    elif kind == "menu":
        items = spec.get("menu_items") or ()
        tmpl = hou.MenuParmTemplate(
            name, label, items,
            menu_labels=spec.get("menu_labels") or (),
            default_value=int(spec.get("default_index", 0)),
        )
    elif kind == "string":
        tmpl = hou.StringParmTemplate(
            name, label, 1,
            default_value=(str(spec.get("default", "")),),
            string_type=spec.get("string_type", hou.stringParmType.Regular),
        )
    else:
        # Ramps and multiparms are not value-syncable through a scalar
        # callback; the scoring pass should exclude them upstream. Failing
        # loudly here beats generating a control that silently does nothing.
        raise ValueError(f"Unsupported parm type for surfacing: {kind!r}")

    tmpl.setHelp(help_text)
    attach_callback(tmpl)
    return tmpl


def attach_callback(tmpl) -> None:
    """Attach the write-through callback to a parm template."""
    _require_hou()
    tmpl.setScriptCallback(CALLBACK_SCRIPT)
    tmpl.setScriptCallbackLanguage(hou.scriptLanguage.Python)


def _default_help(entry: Entry) -> str:
    where = entry.source_path_hint or "(moved)"
    return f"Drives {entry.source_parm} on {where}. Edit the node directly for full control."


# --------------------------------------------------------------------------
# interface assembly
# --------------------------------------------------------------------------

def build_interface(node, manifest: Manifest, specs: Optional[Dict[str, Dict]] = None):
    """Rebuild the panel node's spare-parm interface from the manifest.

    Safe to call repeatedly: values live in the graph, so a rebuild loses
    nothing that a subsequent pull will not restore. Ordering, folders and
    user labels come from the manifest, which is why regeneration preserves
    curation rather than flattening it.
    """
    _require_hou()
    specs = specs or {}
    ptg = node.parmTemplateGroup()

    # Drop our previously-generated controls, leaving anything the artist
    # added by hand untouched.
    for existing in list(ptg.entries()):
        if _is_ours(existing, manifest):
            ptg.remove(existing.name())

    by_folder: Dict[str, List[Entry]] = {}
    for entry in sorted(manifest.entries, key=lambda e: e.order):
        by_folder.setdefault(entry.folder or "", []).append(entry)

    # Unfoldered controls first, then named folders alphabetically.
    for folder_name in sorted(by_folder, key=lambda f: (f != "", f)):
        entries = by_folder[folder_name]
        templates = []
        for entry in entries:
            try:
                templates.append(build_parm_template(entry, specs.get(entry.id)))
            except ValueError:
                continue  # unsupported type; skip rather than abort the rebuild
        if not templates:
            continue
        if folder_name:
            ptg.append(
                hou.FolderParmTemplate(
                    _folder_name(folder_name), folder_name,
                    parm_templates=templates,
                    folder_type=hou.folderType.Collapsible,
                )
            )
        else:
            for tmpl in templates:
                ptg.append(tmpl)

    node.setParmTemplateGroup(ptg)
    return ptg


def _is_ours(entry_template, manifest: Manifest) -> bool:
    name = entry_template.name()
    if manifest.by_gui_parm(name) is not None:
        return True
    return name.startswith("parmesan_folder_")


def _folder_name(label: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in label).strip("_").lower()
    return f"parmesan_folder_{safe or 'group'}"
