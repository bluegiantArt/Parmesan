"""The Parmesan Python Panel.

A first working surface, deliberately plain: pick a control node, add
parameters to it, refresh, review what moved, apply. Layout and styling are
expected to change -- the useful thing here is that every operation the
engine supports is reachable and legible.

The sliders themselves are not in this panel. They are spare parms on the
control node, so they live in Houdini's own parameter editor where artists
already work, and they keep working when this panel is closed. This panel is
the machinery around them: what is bound, what changed, what to do about it.

Qt comes from PySide6 on Houdini 20.5+ and PySide2 before that. Enums are
written fully qualified (``Qt.CheckState.Checked`` rather than
``Qt.Checked``) because PySide6 dropped the short forms.
"""

from __future__ import annotations

from typing import Dict, List, Optional

try:  # Houdini 20.5+
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - older Houdini
    from PySide2 import QtCore, QtGui, QtWidgets

from . import diff as diffmod
from . import promote, sync
from .manifest import Manifest

try:
    import hou
except ImportError:  # pragma: no cover
    hou = None


CHECKED = QtCore.Qt.CheckState.Checked
UNCHECKED = QtCore.Qt.CheckState.Unchecked

#: Kinds a plain Apply can act on without asking a question first.
DIRECTLY_APPLICABLE = frozenset({diffmod.ChangeKind.PULL, diffmod.ChangeKind.PUSH})


def create():
    """Factory used by the .pypanel file.

    Reloads the package first so editing a source file and reopening the
    panel picks up the change -- without this, iterating means restarting
    Houdini, which no one will do twenty times in an afternoon.
    """
    import importlib

    from . import callbacks, manifest
    for module in (manifest, diffmod, callbacks, sync, promote):
        importlib.reload(module)
    return ParmesanPanel()


# --------------------------------------------------------------------------
# add-parameters dialog
# --------------------------------------------------------------------------

class AddParmsDialog(QtWidgets.QDialog):
    """Tick the parameters to surface, from the currently selected nodes.

    Edited parms sort first and are marked, because "I changed this one" is
    the signal that usually means "this is the one I want on the panel".
    """

    def __init__(self, candidates: List[Dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Parameters")
        self.resize(560, 460)
        self._candidates = candidates

        layout = QtWidgets.QVBoxLayout(self)

        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by name...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Parameter", "Node", "Value", ""])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree, 1)

        for cand in candidates:
            item = QtWidgets.QTreeWidgetItem(
                [
                    cand["parm_label"] or cand["source_parm"],
                    cand["node_name"],
                    _short(cand["value"]),
                    "edited" if cand["edited"] else "",
                ]
            )
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, UNCHECKED)
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, cand)
            if cand["edited"]:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            self.tree.addTopLevelItem(item)
        for col in range(4):
            self.tree.resizeColumnToContents(col)

        row = QtWidgets.QHBoxLayout()
        edited_btn = QtWidgets.QPushButton("Check All Edited")
        edited_btn.setToolTip("Tick every parameter whose value differs from its default")
        edited_btn.clicked.connect(self._check_edited)
        row.addWidget(edited_btn)
        none_btn = QtWidgets.QPushButton("Check None")
        none_btn.clicked.connect(self._check_none)
        row.addWidget(none_btn)
        row.addStretch(1)
        layout.addLayout(row)

        folder_row = QtWidgets.QHBoxLayout()
        folder_row.addWidget(QtWidgets.QLabel("Put in folder:"))
        self.folder_edit = QtWidgets.QLineEdit()
        self.folder_edit.setPlaceholderText("(none)")
        folder_row.addWidget(self.folder_edit, 1)
        layout.addLayout(folder_row)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            haystack = f"{item.text(0)} {item.text(1)}".lower()
            item.setHidden(bool(needle) and needle not in haystack)

    def _check_edited(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            cand = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if cand and cand["edited"]:
                item.setCheckState(0, CHECKED)

    def _check_none(self) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, UNCHECKED)

    def chosen(self) -> List[Dict]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == CHECKED:
                out.append(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
        return out

    def folder(self) -> str:
        return self.folder_edit.text().strip()


# --------------------------------------------------------------------------
# main panel
# --------------------------------------------------------------------------

class ParmesanPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._node_path: str = ""
        self._manifest = Manifest()
        self._result: Optional[diffmod.DiffResult] = None

        self._build_ui()
        self._adopt_selection()
        self.reload()

    # ---- construction -------------------------------------------------

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        outer.addLayout(self._build_node_row())
        outer.addLayout(self._build_toolbar())
        outer.addWidget(self._build_changes_group(), 1)
        outer.addWidget(self._build_controls_group(), 1)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    def _build_node_row(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Control node:"))

        self.node_edit = QtWidgets.QLineEdit()
        self.node_edit.setReadOnly(True)
        self.node_edit.setPlaceholderText("none chosen")
        row.addWidget(self.node_edit, 1)

        use_btn = QtWidgets.QPushButton("Use Selected")
        use_btn.setToolTip("Use the node currently selected in the network editor")
        use_btn.clicked.connect(self.on_use_selected)
        row.addWidget(use_btn)

        new_btn = QtWidgets.QPushButton("Create New")
        new_btn.setToolTip("Make a new null named CONTROLS beside the selected node")
        new_btn.clicked.connect(self.on_create_node)
        row.addWidget(new_btn)
        return row

    def _build_toolbar(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()

        self.add_btn = QtWidgets.QPushButton("Add Parameters...")
        self.add_btn.setToolTip("Surface parameters from the selected node(s)")
        self.add_btn.clicked.connect(self.on_add)
        row.addWidget(self.add_btn)

        self.parms_btn = QtWidgets.QPushButton("Show Controls")
        self.parms_btn.setToolTip("Open the control node's parameters, where the sliders live")
        self.parms_btn.clicked.connect(self.on_show_parms)
        row.addWidget(self.parms_btn)

        row.addStretch(1)

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.setToolTip("Re-read the graph and list what changed")
        self.refresh_btn.clicked.connect(self.on_refresh)
        row.addWidget(self.refresh_btn)
        return row

    def _build_changes_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Needs review")
        layout = QtWidgets.QVBoxLayout(group)

        self.changes_tree = QtWidgets.QTreeWidget()
        self.changes_tree.setHeaderLabels(["Control", "What happened", "Graph", "Panel"])
        self.changes_tree.setRootIsDecorated(False)
        self.changes_tree.setAlternatingRowColors(True)
        layout.addWidget(self.changes_tree, 1)

        row = QtWidgets.QHBoxLayout()
        all_btn = QtWidgets.QPushButton("Check All")
        all_btn.clicked.connect(lambda: self._set_all_checks(self.changes_tree, CHECKED))
        row.addWidget(all_btn)
        none_btn = QtWidgets.QPushButton("Check None")
        none_btn.clicked.connect(lambda: self._set_all_checks(self.changes_tree, UNCHECKED))
        row.addWidget(none_btn)
        row.addStretch(1)
        self.apply_btn = QtWidgets.QPushButton("Apply Checked")
        self.apply_btn.clicked.connect(self.on_apply)
        row.addWidget(self.apply_btn)
        layout.addLayout(row)
        return group

    def _build_controls_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Surfaced controls")
        layout = QtWidgets.QVBoxLayout(group)

        self.controls_tree = QtWidgets.QTreeWidget()
        self.controls_tree.setHeaderLabels(["Control", "Drives", "Folder", "Type"])
        self.controls_tree.setRootIsDecorated(False)
        self.controls_tree.setAlternatingRowColors(True)
        self.controls_tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.controls_tree.itemDoubleClicked.connect(lambda *_: self.on_rename())
        layout.addWidget(self.controls_tree, 1)

        row = QtWidgets.QHBoxLayout()
        rename_btn = QtWidgets.QPushButton("Rename")
        rename_btn.clicked.connect(self.on_rename)
        row.addWidget(rename_btn)
        jump_btn = QtWidgets.QPushButton("Select Source Node")
        jump_btn.clicked.connect(self.on_jump)
        row.addWidget(jump_btn)
        row.addStretch(1)
        remove_btn = QtWidgets.QPushButton("Remove")
        remove_btn.setToolTip("Remove the control. The graph value is left untouched.")
        remove_btn.clicked.connect(self.on_remove)
        row.addWidget(remove_btn)
        layout.addLayout(row)
        return group

    # ---- node binding -------------------------------------------------

    def _adopt_selection(self) -> None:
        """Adopt a selected node that already carries a manifest, so reopening
        the panel lands back where the artist was."""
        if hou is None:
            return
        for node in hou.selectedNodes():
            if node.userData(sync.MANIFEST_KEY):
                self._node_path = node.path()
                return

    def node(self):
        """Resolve the bound control node, or None if it is gone."""
        if hou is None or not self._node_path:
            return None
        return hou.node(self._node_path)

    def on_use_selected(self) -> None:
        nodes = hou.selectedNodes()
        if not nodes:
            self._say("Select a node in the network editor first.")
            return
        self._node_path = nodes[0].path()
        self.reload()
        self._say(f"Using {self._node_path}")

    def on_create_node(self) -> None:
        nodes = hou.selectedNodes()
        if not nodes:
            self._say("Select a node first -- the new control node is made beside it.")
            return
        parent = nodes[0].parent()
        try:
            node = promote.create_control_node(parent)
        except hou.OperationFailed as exc:
            self._say(f"Could not create the node: {exc}")
            return
        self._node_path = node.path()
        self.reload()
        self._say(f"Created {self._node_path}")

    # ---- actions ------------------------------------------------------

    def on_add(self) -> None:
        node = self._need_node()
        if node is None:
            return
        sources = [n for n in hou.selectedNodes() if n.path() != node.path()]
        if not sources:
            self._say("Select the node whose parameters you want, then Add Parameters.")
            return

        manifest = sync.load_manifest(node)
        candidates: List[Dict] = []
        for source in sources:
            candidates.extend(promote.candidates_on(source, manifest))
        if not candidates:
            self._say("Nothing on those nodes can be surfaced, or it all already is.")
            return

        dialog = AddParmsDialog(candidates, self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        chosen = dialog.chosen()
        if not chosen:
            self._say("Nothing was ticked.")
            return

        parms = []
        for cand in chosen:
            source_node = hou.node(cand["node_path"])
            if source_node is None:
                continue
            parm = source_node.parm(cand["source_parm"])
            if parm is not None:
                parms.append(parm)

        with hou.undos.group("Parmesan add parameters"):
            added, problems = promote.promote(node, parms, folder=dialog.folder())
        self.reload()
        self._say(_join(f"Added {len(added)}.", problems))

    def on_refresh(self) -> None:
        node = self._need_node()
        if node is None:
            return
        root = sync._search_root(node)
        self._manifest, self._result = sync.refresh(node, root)
        self._fill_changes()
        self._fill_controls()
        self._update_badge()
        self._say(self._result.summary())

    def on_apply(self) -> None:
        node = self._need_node()
        if node is None or self._result is None:
            self._say("Refresh first.")
            return

        checked = self._checked_changes()
        if not checked:
            self._say("Nothing is checked.")
            return

        direct = [c for c in checked if c.kind in DIRECTLY_APPLICABLE]
        diverged = [c for c in checked if c.kind is diffmod.ChangeKind.DIVERGED]
        skipped = [c for c in checked if c not in direct and c not in diverged]

        # A conflict is the one case the engine refuses to decide. Ask once for
        # the whole batch rather than per row: an artist resolving fifteen
        # conflicts one dialog at a time will stop trusting the tool.
        if diverged:
            choice = hou.ui.displayMessage(
                f"{len(diverged)} control(s) changed in both the graph and the panel.\n\n"
                "Which value should win?",
                buttons=("Keep graph values", "Keep panel values", "Cancel"),
                default_choice=0,
                close_choice=2,
                title="Resolve conflicts",
            )
            if choice == 2:
                self._say("Cancelled.")
                return
            winner = (
                diffmod.ChangeKind.PULL if choice == 0 else diffmod.ChangeKind.PUSH
            )
            direct.extend(
                diffmod.Change(kind=winner, entry_id=c.entry_id, label=c.label)
                for c in diverged
            )

        root = sync._search_root(node)
        applied, problems = sync.apply_changes(node, root, self._manifest, direct)
        if skipped:
            problems.append(
                f"{len(skipped)} item(s) need Remove or re-adding rather than Apply."
            )
        self.on_refresh()
        self._say(_join(f"Applied {applied}.", problems))

    def on_remove(self) -> None:
        node = self._need_node()
        if node is None:
            return
        ids = self._selected_entry_ids()
        if not ids:
            self._say("Select one or more controls to remove.")
            return
        with hou.undos.group("Parmesan remove controls"):
            removed = promote.unpromote(node, ids)
        self.reload()
        self._say(f"Removed {removed}. Graph values were left alone.")

    def on_rename(self) -> None:
        node = self._need_node()
        if node is None:
            return
        ids = self._selected_entry_ids()
        if len(ids) != 1:
            self._say("Select exactly one control to rename.")
            return
        entry = self._manifest.by_id(ids[0])
        if entry is None:
            return
        text, ok = QtWidgets.QInputDialog.getText(
            self, "Rename Control", "Label:", text=entry.display_label()
        )
        if not ok:
            return
        with hou.undos.group("Parmesan rename control"):
            promote.rename(node, entry.id, text)
        self.reload()
        self._say(f"Renamed to {text.strip() or entry.label!r}.")

    def on_jump(self) -> None:
        node = self._need_node()
        if node is None:
            return
        ids = self._selected_entry_ids()
        if not ids:
            self._say("Select a control first.")
            return
        entry = self._manifest.by_id(ids[0])
        if entry is None:
            return
        source = sync.resolve_source(entry, sync._search_root(node))
        if source is None:
            self._say("That control's source node no longer exists.")
            return
        source.setCurrent(True, clear_all_selected=True)
        self._say(f"Selected {source.path()}")

    def on_show_parms(self) -> None:
        """Open the control node's parameters -- that is where the sliders are."""
        node = self._need_node()
        if node is None:
            return
        node.setCurrent(True, clear_all_selected=True)
        try:
            node.setSelected(True, show_asset_if_selected=True)
        except (AttributeError, TypeError):
            pass
        self._say(
            f"{node.path()} is now current -- its sliders are in the Parameters pane."
        )

    # ---- population ---------------------------------------------------

    def reload(self) -> None:
        """Re-read the manifest and repopulate, without diffing the graph."""
        node = self.node()
        self.node_edit.setText(self._node_path or "")
        if node is None:
            self._manifest = Manifest()
            self._result = None
            self.changes_tree.clear()
            self.controls_tree.clear()
            self._update_badge()
            if self._node_path:
                self._say(f"{self._node_path} no longer exists.")
            return
        self._manifest = sync.load_manifest(node)
        self._result = None
        self.changes_tree.clear()
        self._fill_controls()
        self._update_badge()

    def _fill_changes(self) -> None:
        self.changes_tree.clear()
        if self._result is None:
            return
        for change in self._result.actionable:
            item = QtWidgets.QTreeWidgetItem(
                [
                    change.label or "(unnamed)",
                    diffmod.human_kind(change.kind),
                    _short(change.source_value),
                    _short(change.gui_value),
                ]
            )
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            # Pre-check only what Apply can act on unaided, so the default
            # click is always safe.
            item.setCheckState(
                0, CHECKED if change.kind in DIRECTLY_APPLICABLE else UNCHECKED
            )
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, change)
            tip = diffmod.human_action(change.kind)
            if change.detail:
                tip = f"{tip}\n{change.detail}" if tip else change.detail
            for col in range(4):
                item.setToolTip(col, tip)
            if change.kind in diffmod.NEEDS_ATTENTION:
                item.setForeground(1, QtGui.QBrush(QtGui.QColor("#d08770")))
            self.changes_tree.addTopLevelItem(item)
        for col in range(4):
            self.changes_tree.resizeColumnToContents(col)

    def _fill_controls(self) -> None:
        self.controls_tree.clear()
        for entry in sorted(self._manifest.entries, key=lambda e: e.order):
            drives = entry.source_path_hint or "(moved)"
            item = QtWidgets.QTreeWidgetItem(
                [
                    entry.display_label(),
                    f"{drives} - {entry.source_parm}",
                    entry.folder or "",
                    entry.parm_type,
                ]
            )
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, entry.id)
            self.controls_tree.addTopLevelItem(item)
        for col in range(4):
            self.controls_tree.resizeColumnToContents(col)

    def _update_badge(self) -> None:
        count = self._result.badge_count if self._result else 0
        self.refresh_btn.setText(f"Refresh ({count})" if count else "Refresh")

    # ---- small helpers ------------------------------------------------

    def _need_node(self):
        node = self.node()
        if node is None:
            self._say("Choose a control node first (Use Selected, or Create New).")
        return node

    def _say(self, message: str) -> None:
        self.status.setText(message)

    def _set_all_checks(self, tree: QtWidgets.QTreeWidget, state) -> None:
        for i in range(tree.topLevelItemCount()):
            tree.topLevelItem(i).setCheckState(0, state)

    def _checked_changes(self) -> List[diffmod.Change]:
        out = []
        for i in range(self.changes_tree.topLevelItemCount()):
            item = self.changes_tree.topLevelItem(i)
            if item.checkState(0) == CHECKED:
                out.append(item.data(0, QtCore.Qt.ItemDataRole.UserRole))
        return out

    def _selected_entry_ids(self) -> List[str]:
        return [
            item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            for item in self.controls_tree.selectedItems()
        ]


def _short(value, limit: int = 28) -> str:
    """Compact a parm value for a table cell."""
    if value is None:
        return ""
    if isinstance(value, float):
        text = f"{value:.4g}"
    elif isinstance(value, (list, tuple)):
        text = ", ".join(_short(v, 8) for v in value)
    else:
        text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _join(lead: str, problems: List[str]) -> str:
    if not problems:
        return lead
    return f"{lead} {len(problems)} problem(s): " + "; ".join(problems[:3])
