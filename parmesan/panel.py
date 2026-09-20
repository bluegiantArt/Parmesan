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
from . import promote, scan, scoring, sync, widgets
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
    for module in (
        manifest, diffmod, scoring, callbacks, sync, promote, scan, widgets
    ):
        importlib.reload(module)
    return ParmesanPanel()


# --------------------------------------------------------------------------
# add-parameters dialog
# --------------------------------------------------------------------------

class CandidateDialog(QtWidgets.QDialog):
    """Review the parameters about to be surfaced, grouped by their node.

    Used for both a graph scan and a manual add. Rows sit under a per-node
    heading, because a flat list gives no sense of which knobs belong together
    and "Height" is meaningless without knowing whose height it is.
    """

    def __init__(
        self,
        candidates: List[Dict],
        parent=None,
        title: str = "Add Parameters",
        precheck: bool = False,
        show_scores: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 540)
        self._candidates = candidates
        self._show_scores = show_scores

        layout = QtWidgets.QVBoxLayout(self)

        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by parameter or node name...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Parameter", "Value", "Score", "Why"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setColumnHidden(2, not show_scores)
        layout.addWidget(self.tree, 1)

        self._populate(precheck)

        row = QtWidgets.QHBoxLayout()
        all_btn = QtWidgets.QPushButton("Check All")
        all_btn.clicked.connect(lambda: self._set_all(CHECKED))
        row.addWidget(all_btn)
        none_btn = QtWidgets.QPushButton("Check None")
        none_btn.clicked.connect(lambda: self._set_all(UNCHECKED))
        row.addWidget(none_btn)
        if not precheck:
            edited_btn = QtWidgets.QPushButton("Check All Edited")
            edited_btn.setToolTip("Tick every parameter whose value differs from its default")
            edited_btn.clicked.connect(self._check_edited)
            row.addWidget(edited_btn)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ---- rows ---------------------------------------------------------

    def _populate(self, precheck: bool) -> None:
        self.tree.clear()
        for node_label, group in _group_by_node(self._candidates):
            parent = QtWidgets.QTreeWidgetItem([node_label, "", "", ""])
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            parent.setFirstColumnSpanned(False)
            parent.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(parent)

            for cand in group:
                item = QtWidgets.QTreeWidgetItem(
                    [
                        cand.get("parm_label") or cand.get("source_parm", ""),
                        _short(cand.get("value")),
                        f"{cand.get('score', 0):g}" if self._show_scores else "",
                        cand.get("why") or ("edited" if cand.get("edited") else ""),
                    ]
                )
                item.setFlags(
                    QtCore.Qt.ItemFlag.ItemIsEnabled
                    | QtCore.Qt.ItemFlag.ItemIsSelectable
                    | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(0, CHECKED if precheck else UNCHECKED)
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, cand)
                if cand.get("edited"):
                    item_font = item.font(0)
                    item_font.setBold(True)
                    item.setFont(0, item_font)
                parent.addChild(item)
            parent.setExpanded(True)
        for col in range(4):
            self.tree.resizeColumnToContents(col)

    def _rows(self):
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                yield parent, parent.child(j)

    # ---- actions ------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for parent, item in self._rows():
            haystack = f"{item.text(0)} {parent.text(0)}".lower()
            item.setHidden(bool(needle) and needle not in haystack)
        # Hide a node heading whose every parm was filtered away.
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            visible = any(
                not parent.child(j).isHidden() for j in range(parent.childCount())
            )
            parent.setHidden(not visible)

    def _set_all(self, state) -> None:
        for _, item in self._rows():
            item.setCheckState(0, state)

    def _check_edited(self) -> None:
        for _, item in self._rows():
            cand = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            if cand and cand.get("edited"):
                item.setCheckState(0, CHECKED)

    def chosen(self) -> List[Dict]:
        return [
            item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            for _, item in self._rows()
            if item.checkState(0) == CHECKED
        ]


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

        # The controls are the point of the panel, so they get the space and
        # the top position. Review only appears when there is something to
        # review -- an empty table above the actual controls was the first
        # thing anyone asked about.
        outer.addWidget(self._build_controls_group(), 3)

        self.changes_group = self._build_changes_group()
        self.changes_group.setVisible(False)
        outer.addWidget(self.changes_group, 1)

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
        new_btn.setToolTip(
            "Make a new null named CONTROLS to hang controls on.\n"
            "Goes in the selected node's network, or the network you are\n"
            "looking at if nothing is selected."
        )
        new_btn.clicked.connect(self.on_create_node)
        row.addWidget(new_btn)
        return row

    def _build_toolbar(self) -> QtWidgets.QVBoxLayout:
        outer = QtWidgets.QVBoxLayout()

        # The headline action. One press reads the whole graph, ranks every
        # parameter in it, and builds the panel -- no node-by-node hunting,
        # which is the entire reason this tool exists.
        scan_row = QtWidgets.QHBoxLayout()
        self.scan_btn = QtWidgets.QPushButton("Scan Graph and Build Panel")
        self.scan_btn.setToolTip(
            "Read every node under the scan root, rank the parameters, and\n"
            "surface the most important ones grouped by node."
        )
        font = self.scan_btn.font()
        font.setBold(True)
        self.scan_btn.setFont(font)
        self.scan_btn.clicked.connect(self.on_scan)
        scan_row.addWidget(self.scan_btn, 1)

        scan_row.addWidget(QtWidgets.QLabel("How much:"))
        # A quality bar, not a count. "Top 12" drops parameters with exactly
        # as much evidence as the ones that made the cut, and no one can
        # justify the 12.
        self.level_combo = QtWidgets.QComboBox()
        for key, settings in scoring.LEVELS.items():
            self.level_combo.addItem(settings["label"], key)
            self.level_combo.setItemData(
                self.level_combo.count() - 1,
                settings["hint"],
                QtCore.Qt.ItemDataRole.ToolTipRole,
            )
        self.level_combo.setCurrentIndex(
            list(scoring.LEVELS).index(scoring.DEFAULT_LEVEL)
        )
        self.level_combo.setToolTip(
            "How strong the evidence has to be before a parameter is surfaced.\n"
            "Everything above the bar is surfaced -- there is no fixed count."
        )
        scan_row.addWidget(self.level_combo)

        self.review_check = QtWidgets.QCheckBox("Review first")
        self.review_check.setToolTip(
            "Show what the scan picked before adding it.\n"
            "Off by default: one press should give you a working panel."
        )
        scan_row.addWidget(self.review_check)
        outer.addLayout(scan_row)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Scan from:"))
        self.root_edit = QtWidgets.QLineEdit()
        self.root_edit.setPlaceholderText("(the control node's network)")
        self.root_edit.setToolTip(
            "Which part of the scene to scan. Blank means the network the\n"
            "control node lives in."
        )
        row.addWidget(self.root_edit, 1)
        root_btn = QtWidgets.QPushButton("Set from Selection")
        root_btn.clicked.connect(self.on_set_root)
        row.addWidget(root_btn)
        outer.addLayout(row)

        row2 = QtWidgets.QHBoxLayout()
        self.add_btn = QtWidgets.QPushButton("Add Manually...")
        self.add_btn.setToolTip(
            "Fallback: pick parameters yourself from the selected node(s),\n"
            "for when the scan misses something."
        )
        self.add_btn.clicked.connect(self.on_add)
        row2.addWidget(self.add_btn)

        self.parms_btn = QtWidgets.QPushButton("Show Controls")
        self.parms_btn.setToolTip(
            "Make the control node current so its sliders appear in the\n"
            "Parameters pane -- that is where the controls actually live."
        )
        self.parms_btn.clicked.connect(self.on_show_parms)
        row2.addWidget(self.parms_btn)

        row2.addStretch(1)

        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_btn.setToolTip("Re-read the graph and list what changed")
        self.refresh_btn.clicked.connect(self.on_refresh)
        row2.addWidget(self.refresh_btn)
        outer.addLayout(row2)
        return outer

    def _build_changes_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox(
            "Changed in the graph since you last looked - needs review"
        )
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
        group = QtWidgets.QGroupBox("Controls")
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(4, 4, 4, 4)

        self.controls = widgets.ControlsView()
        self.controls.edited.connect(self.on_control_edited)
        self.controls.contextRequested.connect(self.on_control_menu)
        layout.addWidget(self.controls, 1)

        hint = QtWidgets.QLabel(
            "Right-click a control's name to rename it, jump to its node, or remove it."
        )
        hint.setStyleSheet("color: palette(mid);")
        layout.addWidget(hint)
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

    def _current_network(self):
        """The network the artist is looking at.

        Used when nothing is selected. Requiring a selection purely to decide
        where to put a null is busywork, and it invites the misreading that
        the selected node is what gets scanned -- it is not.
        """
        try:
            editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if editor is not None:
                return editor.pwd()
        except (AttributeError, hou.OperationFailed):
            pass
        return hou.node("/obj")

    def on_create_node(self) -> None:
        nodes = hou.selectedNodes()
        parent = nodes[0].parent() if nodes else self._current_network()
        if parent is None:
            self._say("Could not work out which network to build the control node in.")
            return
        try:
            node = promote.create_control_node(parent)
        except hou.OperationFailed as exc:
            self._say(f"Could not create a control node in {parent.path()}: {exc}")
            return
        self._node_path = node.path()
        self.reload()
        self._say(f"Created {self._node_path}. Scan will read all of {parent.path()}.")

    # ---- actions ------------------------------------------------------

    def on_set_root(self) -> None:
        nodes = hou.selectedNodes()
        if not nodes:
            self._say("Select the network or node to scan from, then press this.")
            return
        self.root_edit.setText(nodes[0].path())
        self._say(f"Scanning from {nodes[0].path()}")

    def _scan_root(self, node):
        """Resolve the scan root: whatever was typed, else the control node's
        own network."""
        typed = self.root_edit.text().strip()
        if not typed:
            return scan.default_root(node)
        root = hou.node(typed)
        if root is None:
            self._say(f"No such network: {typed}")
        return root

    def on_scan(self) -> None:
        """The headline action: read the graph, rank it, build the panel."""
        node = self._need_node()
        if node is None:
            return
        root = self._scan_root(node)
        if root is None:
            return

        level = self.level_combo.currentData() or scoring.DEFAULT_LEVEL
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            ranked, report = scan.propose(node, root=root, level=level)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        if report.interrupted:
            # Escaped deliberately: nothing is surfaced, because a half-read
            # graph would rank against evidence that was never gathered.
            self._say(f"Scan stopped. {report.summary()}. Nothing was added.")
            return

        if not ranked:
            self._say(
                f"{report.summary()}, but nothing cleared the evidence bar. "
                "Try a lower setting in 'How much', or use Add Manually."
            )
            return

        chosen = ranked
        if self.review_check.isChecked():
            dialog = CandidateDialog(
                ranked,
                self,
                title="Scan Results",
                precheck=True,
                show_scores=True,
            )
            if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                self._say("Scan cancelled; nothing was added.")
                return
            chosen = dialog.chosen()
            if not chosen:
                self._say("Nothing was ticked.")
                return

        with hou.undos.group("Parmesan scan graph"):
            added, problems = promote.promote_candidates(node, chosen)

        self.reload()
        note = f"Surfaced {len(added)} of {scoring.summarize(ranked)}. {report.summary()}."
        if len(added) > scoring.LARGE_PANEL:
            note += " That is a big panel -- try 'Just the essentials' for fewer."
        self._say(_join(note, problems + report.errors))

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

        dialog = CandidateDialog(candidates, self, title="Add Parameters")
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        chosen = dialog.chosen()
        if not chosen:
            self._say("Nothing was ticked.")
            return

        with hou.undos.group("Parmesan add parameters"):
            added, problems = promote.promote_candidates(node, chosen)
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

    def on_control_edited(self, entry_id: str, value) -> None:
        """A control in the panel was moved: write it down into the graph."""
        node = self.node()
        if node is None:
            return
        entry = self._manifest.by_id(entry_id)
        if entry is None:
            return
        problem = sync.write_through(node, self._manifest, entry, value)
        if problem:
            self._say(problem)
            return
        self._say(f"{entry.display_label()} = {_short(value)}")

    def on_control_menu(self, entry_id: str, global_pos) -> None:
        """Per-control actions, on the control's own label."""
        entry = self._manifest.by_id(entry_id)
        if entry is None:
            return
        menu = QtWidgets.QMenu(self)
        rename_action = menu.addAction("Rename...")
        jump_action = menu.addAction("Select Source Node")
        menu.addSeparator()
        remove_action = menu.addAction("Remove Control")
        remove_group_action = None
        if entry.folder:
            remove_group_action = menu.addAction(f"Remove All From {entry.folder}")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is rename_action:
            self.on_rename(entry_id)
        elif chosen is jump_action:
            self.on_jump(entry_id)
        elif chosen is remove_action:
            self.on_remove([entry_id])
        elif remove_group_action is not None and chosen is remove_group_action:
            self.on_remove(
                [e.id for e in self._manifest.entries if e.folder == entry.folder]
            )

    def on_remove(self, entry_ids: List[str]) -> None:
        node = self._need_node()
        if node is None or not entry_ids:
            return
        with hou.undos.group("Parmesan remove controls"):
            removed = promote.unpromote(node, entry_ids)
        self.reload()
        self._say(f"Removed {removed}. Graph values were left alone.")

    def on_rename(self, entry_id: str) -> None:
        node = self._need_node()
        if node is None:
            return
        entry = self._manifest.by_id(entry_id)
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

    def on_jump(self, entry_id: str) -> None:
        node = self._need_node()
        if node is None:
            return
        entry = self._manifest.by_id(entry_id)
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
            self._fill_controls()
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
        """Draw the actual controls, grouped by source node."""
        node = self.node()
        if node is None:
            self.controls.show_message(
                "No control node yet.\n\n"
                "Press Create New to make one, then Scan Graph to fill it."
            )
            return
        if not self._manifest.entries:
            self.controls.show_message(
                "Nothing surfaced yet.\n\n"
                "Press Scan Graph and Build Panel."
            )
            return

        rows = promote.live_rows(node, self._manifest)

        groups: List[Dict] = []
        by_heading: Dict[str, Dict] = {}
        for row in rows:
            heading = row["folder"] or row["node_name"] or "(ungrouped)"
            group = by_heading.get(heading)
            if group is None:
                group = {
                    "title": heading,
                    "subtitle": row["node_path"],
                    "rows": [],
                }
                by_heading[heading] = group
                groups.append(group)
            group["rows"].append(row)

        drawn = self.controls.build(groups)
        if drawn == 0:
            self.controls.show_message(
                "Nothing here can be drawn as a control. "
                "Try Scan Graph again, or Add Manually."
            )

    def _update_badge(self) -> None:
        count = self._result.badge_count if self._result else 0
        self.refresh_btn.setText(f"Refresh ({count})" if count else "Refresh")
        # The review table only earns screen space when it has something on it.
        self.changes_group.setVisible(
            bool(self._result and self._result.actionable)
        )

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



def _group_by_node(candidates: List[Dict]):
    """Group candidates under a node heading, keeping the order they arrive in.

    Rank order is meaningful after a scan -- the best-scoring node's group
    should sit at the top -- so this does not sort, it only clusters.
    """
    headings: List[str] = []
    groups: Dict[str, List[Dict]] = {}
    for cand in candidates:
        name = cand.get("node_name") or "?"
        path = cand.get("node_path") or ""
        heading = f"{name}   {path}" if path else name
        if heading not in groups:
            groups[heading] = []
            headings.append(heading)
        groups[heading].append(cand)
    return [(heading, groups[heading]) for heading in headings]


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
