"""The actual controls: Qt widgets bound to source parms, inside the panel.

Houdini does not let you embed its native parameter editor into a Python
Panel, so a panel with working sliders has to draw them. SideFX's own Python
Panel examples do the same thing -- link PySide widgets to node parms and back.

The layout follows Houdini's: right-aligned label, value field, slider, one
collapsible group per source node. Familiarity is the whole point; an artist
should not have to learn a new parameter widget to use this.

What these widgets do NOT get, because they are not native parms: ladder
drag, expression entry, right-click keyframing, channel colouring. The
control node's spare parms still exist and still provide all of that in the
Parameters pane for anyone who wants it.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

try:  # Houdini 20.5+
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:  # pragma: no cover - older Houdini
    from PySide2 import QtCore, QtGui, QtWidgets

#: Slider resolution. Sliders are integer devices; this is how many steps a
#: float range is divided into before the value is mapped back.
SLIDER_STEPS = 1000


# --------------------------------------------------------------------------
# editors
# --------------------------------------------------------------------------

class NumberField(QtWidgets.QWidget):
    """A value field with a slider beside it, like Houdini's own.

    The field is authoritative and the slider is a convenience: typing a value
    outside the slider's range is allowed and simply pins the handle. Source
    parm ranges are suggestions, and clamping the panel tighter than the graph
    allows is a trap -- a sim parm whose UI range stops at 10 may still want 40.
    """

    valueChanged = QtCore.Signal(object)

    def __init__(self, value, spec: Dict[str, Any], integer: bool = False, parent=None):
        super().__init__(parent)
        self._integer = integer
        self._emitting = False

        self._minimum = float(spec.get("min", 0.0) or 0.0)
        self._maximum = float(spec.get("max", 10.0) or 10.0)
        if self._maximum <= self._minimum:
            self._maximum = self._minimum + 1.0

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.field = QtWidgets.QLineEdit()
        self.field.setFixedWidth(80)
        validator = (
            QtGui.QIntValidator() if integer else QtGui.QDoubleValidator()
        )
        self.field.setValidator(validator)
        self.field.editingFinished.connect(self._from_field)
        layout.addWidget(self.field)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setRange(0, SLIDER_STEPS)
        self.slider.valueChanged.connect(self._from_slider)
        layout.addWidget(self.slider, 1)

        self.set_value(value)

    # ---- conversion ---------------------------------------------------

    def _to_slider(self, value: float) -> int:
        span = self._maximum - self._minimum
        ratio = (float(value) - self._minimum) / span if span else 0.0
        return max(0, min(SLIDER_STEPS, int(round(ratio * SLIDER_STEPS))))

    def _from_slider_value(self, position: int) -> float:
        span = self._maximum - self._minimum
        value = self._minimum + (position / float(SLIDER_STEPS)) * span
        return int(round(value)) if self._integer else value

    def _format(self, value) -> str:
        if self._integer:
            return str(int(value))
        # Enough precision to round-trip a typed value, without turning 1.0
        # into 1.0000000000000002 in the field.
        return f"{float(value):.6g}"

    # ---- state --------------------------------------------------------

    def value(self):
        text = self.field.text().strip()
        if not text:
            return 0 if self._integer else 0.0
        try:
            return int(float(text)) if self._integer else float(text)
        except ValueError:
            return 0 if self._integer else 0.0

    def set_value(self, value) -> None:
        """Set without emitting -- used when refreshing from the graph."""
        self._emitting = True
        try:
            self.field.setText(self._format(value))
            self.slider.setValue(self._to_slider(value))
        finally:
            self._emitting = False

    # ---- signals ------------------------------------------------------

    def _from_field(self) -> None:
        if self._emitting:
            return
        value = self.value()
        self._emitting = True
        try:
            self.slider.setValue(self._to_slider(value))
        finally:
            self._emitting = False
        self.valueChanged.emit(value)

    def _from_slider(self, position: int) -> None:
        if self._emitting:
            return
        value = self._from_slider_value(position)
        self._emitting = True
        try:
            self.field.setText(self._format(value))
        finally:
            self._emitting = False
        self.valueChanged.emit(value)


class ToggleField(QtWidgets.QCheckBox):
    valueChanged = QtCore.Signal(object)

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self._emitting = False
        self.set_value(value)
        self.toggled.connect(self._emit)

    def value(self):
        return bool(self.isChecked())

    def set_value(self, value) -> None:
        self._emitting = True
        try:
            self.setChecked(bool(value))
        finally:
            self._emitting = False

    def _emit(self, state) -> None:
        if not self._emitting:
            self.valueChanged.emit(bool(state))


class TextField(QtWidgets.QLineEdit):
    valueChanged = QtCore.Signal(object)

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self._emitting = False
        self.set_value(value)
        self.editingFinished.connect(self._emit)

    def value(self):
        return self.text()

    def set_value(self, value) -> None:
        self._emitting = True
        try:
            self.setText("" if value is None else str(value))
        finally:
            self._emitting = False

    def _emit(self) -> None:
        if not self._emitting:
            self.valueChanged.emit(self.text())


class MenuField(QtWidgets.QComboBox):
    valueChanged = QtCore.Signal(object)

    def __init__(self, value, spec: Dict[str, Any], parent=None):
        super().__init__(parent)
        self._emitting = False
        labels = spec.get("menu_labels") or spec.get("menu_items") or ()
        self.addItems([str(label) for label in labels])
        self.set_value(value)
        self.currentIndexChanged.connect(self._emit)

    def value(self):
        return self.currentIndex()

    def set_value(self, value) -> None:
        self._emitting = True
        try:
            try:
                self.setCurrentIndex(int(value))
            except (TypeError, ValueError):
                self.setCurrentIndex(0)
        finally:
            self._emitting = False

    def _emit(self, index) -> None:
        if not self._emitting:
            self.valueChanged.emit(int(index))


def build_editor(parm_type: str, value, spec: Optional[Dict[str, Any]] = None):
    """The right editor for a parm type, or None if we cannot draw one."""
    spec = spec or {}
    if parm_type == "float":
        return NumberField(value if value is not None else 0.0, spec)
    if parm_type == "int":
        return NumberField(value if value is not None else 0, spec, integer=True)
    if parm_type == "toggle":
        return ToggleField(value)
    if parm_type == "menu":
        return MenuField(value, spec)
    if parm_type == "string":
        return TextField(value)
    return None


# --------------------------------------------------------------------------
# grouping
# --------------------------------------------------------------------------

class CollapsibleGroup(QtWidgets.QWidget):
    """A node's controls under a clickable header.

    One group per source node, which is what makes provenance obvious without
    repeating the node name on every single row.
    """

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.header = QtWidgets.QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setToolButtonStyle(
            QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.header.setArrowType(QtCore.Qt.ArrowType.DownArrow)
        self.header.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        font = self.header.font()
        font.setBold(True)
        self.header.setFont(font)
        if subtitle:
            self.header.setToolTip(subtitle)
        self.header.toggled.connect(self._on_toggled)
        outer.addWidget(self.header)

        self.body = QtWidgets.QWidget()
        self.form = QtWidgets.QFormLayout(self.body)
        self.form.setContentsMargins(16, 4, 4, 8)
        self.form.setSpacing(4)
        # Right-aligned labels, matching Houdini's parameter layout.
        self.form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        outer.addWidget(self.body)

    def _on_toggled(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.header.setArrowType(
            QtCore.Qt.ArrowType.DownArrow if expanded else QtCore.Qt.ArrowType.RightArrow
        )

    def add_row(self, label_widget, editor) -> None:
        self.form.addRow(label_widget, editor)


class ControlsView(QtWidgets.QScrollArea):
    """The panel's control surface: every surfaced parm, grouped by node.

    Emits ``edited(entry_id, value)`` when the artist changes something. It
    does not write to the graph itself -- the panel owns that, so all writing
    stays in one place and inside one undo group.
    """

    edited = QtCore.Signal(str, object)
    contextRequested = QtCore.Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._editors: Dict[str, Any] = {}
        self._inner = QtWidgets.QWidget()
        self._layout = QtWidgets.QVBoxLayout(self._inner)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(6)
        self._layout.addStretch(1)
        self.setWidget(self._inner)
        self._placeholder: Optional[QtWidgets.QLabel] = None

    # ---- building -----------------------------------------------------

    def clear(self) -> None:
        self._editors.clear()
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._layout.addStretch(1)

    def show_message(self, message: str) -> None:
        self.clear()
        label = QtWidgets.QLabel(message)
        label.setWordWrap(True)
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: palette(mid); padding: 24px;")
        self._layout.insertWidget(0, label)
        self._placeholder = label

    def build(self, groups: List[Dict[str, Any]]) -> int:
        """Build from grouped control descriptions.

        Each group is {"title", "subtitle", "rows": [row, ...]} and each row is
        {"entry_id", "label", "parm_type", "value", "spec", "tooltip",
         "expression"}.
        """
        self.clear()
        drawn = 0
        for index, group in enumerate(groups):
            box = CollapsibleGroup(group.get("title", ""), group.get("subtitle", ""))
            for row in group.get("rows", []):
                editor = build_editor(
                    row.get("parm_type", ""), row.get("value"), row.get("spec")
                )
                if editor is None:
                    continue
                label = QtWidgets.QLabel(row.get("label", ""))
                tooltip = row.get("tooltip", "")
                expression = row.get("expression")
                if expression:
                    # Editing an expression-driven parm replaces the
                    # expression, exactly as dragging its slider in Houdini
                    # would. Say so up front rather than after the fact.
                    label_font = label.font()
                    label_font.setItalic(True)
                    label.setFont(label_font)
                    tooltip = (
                        f"{tooltip}\n\nDriven by an expression:\n  {expression}\n"
                        "Editing this control replaces it (undoable)."
                    ).strip()
                if tooltip:
                    label.setToolTip(tooltip)
                    editor.setToolTip(tooltip)

                entry_id = row.get("entry_id", "")
                editor.valueChanged.connect(
                    lambda value, key=entry_id: self.edited.emit(key, value)
                )
                label.setContextMenuPolicy(
                    QtCore.Qt.ContextMenuPolicy.CustomContextMenu
                )
                label.customContextMenuRequested.connect(
                    lambda point, key=entry_id, widget=label: self.contextRequested.emit(
                        key, widget.mapToGlobal(point)
                    )
                )
                box.add_row(label, editor)
                self._editors[entry_id] = editor
                drawn += 1
            self._layout.insertWidget(index, box)
        self._layout.addStretch(1)
        return drawn

    # ---- updating -----------------------------------------------------

    def set_value(self, entry_id: str, value) -> bool:
        """Update one control without emitting, after a graph-side change."""
        editor = self._editors.get(entry_id)
        if editor is None:
            return False
        editor.set_value(value)
        return True

    def entry_ids(self) -> List[str]:
        return list(self._editors)
