"""Tests for the hou-free parts of the panel layer.

The panel itself needs Qt and a running Houdini, so what is testable here is
the wording shown to the artist and the label composition -- both of which are
pure, and both of which are easy to break by accident.
"""

import unittest

from parmesan.diff import (
    KIND_ACTIONS,
    KIND_LABELS,
    ChangeKind,
    human_action,
    human_kind,
)
from parmesan.promote import SUPPORTED_KINDS, compose_label


class Wording(unittest.TestCase):
    def test_every_kind_has_a_human_label(self):
        # A missing entry would show an enum value like "type_changed" in the
        # UI, which means nothing to an artist.
        for kind in ChangeKind:
            self.assertIn(kind, KIND_LABELS, f"no label for {kind}")
            self.assertTrue(KIND_LABELS[kind].strip())

    def test_every_actionable_kind_explains_what_apply_does(self):
        for kind in ChangeKind:
            if kind is ChangeKind.IN_SYNC:
                continue  # nothing to do, so nothing to explain
            self.assertIn(kind, KIND_ACTIONS, f"no action text for {kind}")

    def test_labels_are_not_enum_names(self):
        self.assertEqual(human_kind(ChangeKind.PULL), "Changed in the graph")
        self.assertEqual(human_kind(ChangeKind.DIVERGED), "Changed in both places")

    def test_action_text_is_available_for_conflicts(self):
        self.assertEqual(human_action(ChangeKind.DIVERGED), "Choose which value wins")

    def test_unknown_kind_falls_back_without_raising(self):
        self.assertEqual(human_action(ChangeKind.IN_SYNC), "")


class LabelComposition(unittest.TestCase):
    def test_node_name_qualifies_the_parm_label(self):
        self.assertEqual(compose_label("mountain1", "Height"), "mountain1 / Height")

    def test_redundant_node_name_is_dropped(self):
        # HDA parms are often already named after the asset; repeating it gives
        # "cliff1 / Cliff1 Erosion".
        self.assertEqual(compose_label("cliff1", "Cliff1 Erosion"), "Cliff1 Erosion")

    def test_case_insensitive_redundancy_check(self):
        self.assertEqual(compose_label("Rock", "rock scale"), "rock scale")

    def test_missing_parm_label_falls_back_to_node_name(self):
        self.assertEqual(compose_label("box1", ""), "box1")

    def test_missing_node_name_falls_back_to_parm_label(self):
        self.assertEqual(compose_label("", "Size X"), "Size X")

    def test_whitespace_is_not_treated_as_a_label(self):
        self.assertEqual(compose_label("box1", "   "), "box1")


class SupportedKinds(unittest.TestCase):
    def test_ramps_are_excluded(self):
        # build_parm_template() raises on ramps; letting one become a candidate
        # would produce a control that silently does nothing.
        self.assertNotIn("ramp", SUPPORTED_KINDS)
        self.assertNotIn("unsupported", SUPPORTED_KINDS)

    def test_the_scalar_types_are_all_present(self):
        self.assertEqual(
            SUPPORTED_KINDS, {"float", "int", "toggle", "string", "menu"}
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
