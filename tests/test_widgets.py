"""Tests for the in-panel control widgets.

These need Qt but not Houdini, and they skip cleanly where Qt is absent so the
suite still runs anywhere. The slider mapping is the part worth testing: it is
arithmetic with an obvious division-by-zero hiding in it, and a control that
silently reports the wrong number is worse than one that fails to draw.
"""

import os
import unittest

# Qt needs a platform plugin before anything imports it. Without a display --
# CI, a remote shell, a render farm -- the default plugin aborts the process
# rather than raising, which would take the whole suite down with it.
if not os.environ.get("DISPLAY") and not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

try:
    from PySide6 import QtWidgets
    HAS_QT = True
except ImportError:  # pragma: no cover
    try:
        from PySide2 import QtWidgets
        HAS_QT = True
    except ImportError:
        HAS_QT = False

if HAS_QT:
    from parmesan import widgets

_app = None


def setUpModule():
    global _app
    if HAS_QT:
        _app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@unittest.skipUnless(HAS_QT, "PySide not available")
class NumberFieldMapping(unittest.TestCase):
    def field(self, value=1.0, **spec):
        spec.setdefault("min", 0.0)
        spec.setdefault("max", 10.0)
        return widgets.NumberField(value, spec)

    def test_value_round_trips(self):
        self.assertEqual(self.field(2.5).value(), 2.5)

    def test_slider_sits_proportionally(self):
        field = self.field(5.0)
        self.assertEqual(field.slider.value(), widgets.SLIDER_STEPS // 2)

    def test_minimum_and_maximum_hit_the_ends(self):
        # Keep references: an unparented widget is collected as soon as the
        # expression ends, taking its C++ children with it.
        low = self.field(0.0)
        high = self.field(10.0)
        self.assertEqual(low.slider.value(), 0)
        self.assertEqual(high.slider.value(), widgets.SLIDER_STEPS)

    def test_values_beyond_the_range_pin_rather_than_clamp(self):
        # The typed value is kept; only the handle is pinned. A sim parm whose
        # UI range stops at 10 may still legitimately want 40.
        field = self.field(40.0)
        self.assertEqual(field.slider.value(), widgets.SLIDER_STEPS)
        self.assertEqual(field.value(), 40.0)

    def test_below_range_also_pins(self):
        field = self.field(-5.0)
        self.assertEqual(field.slider.value(), 0)
        self.assertEqual(field.value(), -5.0)

    def test_degenerate_range_does_not_divide_by_zero(self):
        # Plenty of real parms report min == max.
        field = widgets.NumberField(1.0, {"min": 3.0, "max": 3.0})
        self.assertIsInstance(field.slider.value(), int)

    def test_missing_range_falls_back(self):
        field = widgets.NumberField(1.0, {})
        self.assertIsInstance(field.value(), float)

    def test_integer_mode_yields_integers(self):
        field = widgets.NumberField(3, {"min": 0, "max": 10}, integer=True)
        field.slider.setValue(int(widgets.SLIDER_STEPS * 0.77))
        self.assertIsInstance(field.value(), int)

    def test_unparseable_text_does_not_raise(self):
        field = self.field(1.0)
        field.field.setText("not a number")
        self.assertIsInstance(field.value(), float)

    def test_set_value_is_silent(self):
        # Refreshing from the graph must not look like an artist edit, or a
        # refresh would write straight back and defeat the diff.
        field = self.field(1.0)
        seen = []
        field.valueChanged.connect(seen.append)
        field.set_value(7.0)
        self.assertEqual(seen, [])
        self.assertEqual(field.value(), 7.0)

    def test_dragging_the_slider_emits(self):
        field = self.field(1.0)
        seen = []
        field.valueChanged.connect(seen.append)
        field.slider.setValue(widgets.SLIDER_STEPS // 4)
        self.assertEqual(len(seen), 1)
        self.assertAlmostEqual(seen[0], 2.5, places=4)


@unittest.skipUnless(HAS_QT, "PySide not available")
class EditorSelection(unittest.TestCase):
    def test_each_supported_type_gets_an_editor(self):
        for parm_type, value in (
            ("float", 1.0), ("int", 1), ("toggle", True),
            ("string", "x"), ("menu", 0),
        ):
            self.assertIsNotNone(
                widgets.build_editor(parm_type, value, {}),
                f"no editor for {parm_type}",
            )

    def test_undrawable_types_return_none_rather_than_raising(self):
        # Ramps reach here only through a bug upstream, but the panel should
        # skip the row rather than fail to draw the whole group.
        self.assertIsNone(widgets.build_editor("ramp", None, {}))
        self.assertIsNone(widgets.build_editor("", None, {}))

    def test_none_values_do_not_break_numeric_editors(self):
        self.assertIsNotNone(widgets.build_editor("float", None, {}))


@unittest.skipUnless(HAS_QT, "PySide not available")
class OtherEditors(unittest.TestCase):
    def test_toggle_round_trips_and_emits(self):
        field = widgets.ToggleField(True)
        seen = []
        field.valueChanged.connect(seen.append)
        self.assertTrue(field.value())
        field.setChecked(False)
        self.assertEqual(seen, [False])

    def test_toggle_set_value_is_silent(self):
        field = widgets.ToggleField(False)
        seen = []
        field.valueChanged.connect(seen.append)
        field.set_value(True)
        self.assertEqual(seen, [])

    def test_menu_uses_labels_and_reports_an_index(self):
        field = widgets.MenuField(
            1, {"menu_items": ("a", "b"), "menu_labels": ("Fast", "Slow")}
        )
        self.assertEqual(field.currentText(), "Slow")
        self.assertEqual(field.value(), 1)

    def test_menu_survives_an_out_of_range_value(self):
        field = widgets.MenuField(99, {"menu_items": ("a", "b")})
        self.assertIsInstance(field.value(), int)

    def test_text_field_round_trips(self):
        field = widgets.TextField("water_v001")
        self.assertEqual(field.value(), "water_v001")
        field.set_value(None)
        self.assertEqual(field.value(), "")


@unittest.skipUnless(HAS_QT, "PySide not available")
class ControlsViewBuilding(unittest.TestCase):
    def rows(self):
        return [
            {
                "title": "flipfluidobject",
                "subtitle": "/obj/water_sim/flipfluidobject",
                "rows": [
                    {"entry_id": "a", "label": "Grid Scale", "parm_type": "float",
                     "value": 2.0, "spec": {"min": 0.0, "max": 4.0},
                     "tooltip": "", "expression": ""},
                    {"entry_id": "b", "label": "Ramp", "parm_type": "ramp",
                     "value": None, "spec": {}, "tooltip": "", "expression": ""},
                ],
            }
        ]

    def test_undrawable_rows_are_skipped_not_fatal(self):
        view = widgets.ControlsView()
        self.assertEqual(view.build(self.rows()), 1)
        self.assertEqual(view.entry_ids(), ["a"])

    def test_rebuilding_does_not_accumulate(self):
        view = widgets.ControlsView()
        view.build(self.rows())
        view.build(self.rows())
        self.assertEqual(view.entry_ids(), ["a"])

    def test_editing_reports_the_entry_id(self):
        view = widgets.ControlsView()
        view.build(self.rows())
        seen = []
        view.edited.connect(lambda key, value: seen.append((key, value)))
        view._editors["a"].slider.setValue(widgets.SLIDER_STEPS)
        self.assertEqual(seen[-1][0], "a")

    def test_set_value_targets_the_right_control(self):
        view = widgets.ControlsView()
        view.build(self.rows())
        self.assertTrue(view.set_value("a", 3.0))
        self.assertFalse(view.set_value("nonexistent", 1.0))

    def test_message_replaces_the_controls(self):
        view = widgets.ControlsView()
        view.build(self.rows())
        view.show_message("Nothing surfaced yet.")
        self.assertEqual(view.entry_ids(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
