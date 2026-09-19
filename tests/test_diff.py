"""Tests for the hou-free half: manifest schema and the diff engine.

These run anywhere Python does -- no Houdini install required. That is the
point of keeping observation and decision separate.
"""

import unittest

from parmesan.diff import ChangeKind, compute, values_equal
from parmesan.manifest import Entry, Manifest


def entry(**kw):
    base = dict(
        id="e1", gui_parm="height", source_uuid="u1", source_parm="height",
        label="Terrain Height", parm_type="float", last_synced=3.5,
    )
    base.update(kw)
    return Entry(**base)


def obs(source=3.5, gui=3.5, source_present=True, gui_present=True, parm_type="float"):
    return {
        "source_present": source_present,
        "gui_present": gui_present,
        "source_value": source,
        "gui_value": gui,
        "parm_type": parm_type,
    }


class ValueComparison(unittest.TestCase):
    def test_float_tolerance_absorbs_roundtrip_noise(self):
        self.assertTrue(values_equal(0.1 + 0.2, 0.3))

    def test_real_difference_still_detected(self):
        self.assertFalse(values_equal(3.5, 3.6))

    def test_sequences_compare_elementwise(self):
        self.assertTrue(values_equal([1.0, 2.0], (1.0, 2.0)))
        self.assertFalse(values_equal([1.0, 2.0], [1.0, 2.5]))

    def test_none_is_not_equal_to_zero(self):
        self.assertFalse(values_equal(None, 0))

    def test_bool_not_confused_with_int(self):
        self.assertTrue(values_equal(True, 1))
        self.assertFalse(values_equal(True, 0))


class DiffKinds(unittest.TestCase):
    def one(self, e, o, candidates=None):
        result = compute(Manifest(entries=[e]), {e.id: o}, candidates)
        return result.changes[0]

    def test_nothing_moved(self):
        self.assertIs(self.one(entry(), obs()).kind, ChangeKind.IN_SYNC)

    def test_graph_moved_pulls(self):
        self.assertIs(self.one(entry(), obs(source=9.0)).kind, ChangeKind.PULL)

    def test_panel_moved_pushes(self):
        self.assertIs(self.one(entry(), obs(gui=9.0)).kind, ChangeKind.PUSH)

    def test_both_moved_differently_diverges(self):
        self.assertIs(self.one(entry(), obs(source=9.0, gui=4.0)).kind, ChangeKind.DIVERGED)

    def test_both_moved_to_same_value_is_in_sync(self):
        # Not a conflict: there is nothing to reconcile, only a watermark to advance.
        self.assertIs(self.one(entry(), obs(source=9.0, gui=9.0)).kind, ChangeKind.IN_SYNC)

    def test_missing_source_node_orphans(self):
        self.assertIs(self.one(entry(), obs(source_present=False)).kind, ChangeKind.ORPHANED)

    def test_missing_panel_parm_dangles(self):
        self.assertIs(self.one(entry(), obs(gui_present=False)).kind, ChangeKind.DANGLING)

    def test_type_change_reported_before_value_comparison(self):
        c = self.one(entry(), obs(source=1, parm_type="int"))
        self.assertIs(c.kind, ChangeKind.TYPE_CHANGED)
        self.assertEqual(c.detail, "float -> int")

    def test_entry_absent_from_observation_orphans(self):
        e = entry()
        result = compute(Manifest(entries=[e]), {})
        self.assertIs(result.changes[0].kind, ChangeKind.ORPHANED)

    def test_candidates_surface_as_suggestions(self):
        c = self.one(entry(), obs(), candidates=[{"label": "Peak Scale", "reason": "edited"}])
        # first change is the entry; candidate is appended after
        result = compute(Manifest(entries=[entry()]), {"e1": obs()},
                         [{"label": "Peak Scale", "reason": "edited"}])
        cand = result.changes[-1]
        self.assertIs(cand.kind, ChangeKind.NEW_CANDIDATE)
        self.assertEqual(cand.label, "Peak Scale")


class BadgeAndSummary(unittest.TestCase):
    def test_suggestions_do_not_inflate_the_badge(self):
        # New candidates are advice, not a problem; the badge counts things
        # that actually need the artist's attention.
        result = compute(
            Manifest(entries=[entry()]), {"e1": obs()},
            [{"label": "a"}, {"label": "b"}],
        )
        self.assertEqual(result.badge_count, 0)

    def test_pull_and_conflict_both_count(self):
        m = Manifest(entries=[entry(), entry(id="e2", gui_parm="speed")])
        result = compute(m, {"e1": obs(source=9.0), "e2": obs(source=1.0, gui=2.0)})
        self.assertEqual(result.badge_count, 2)

    def test_clean_graph_summarizes_as_up_to_date(self):
        result = compute(Manifest(entries=[entry()]), {"e1": obs()})
        self.assertEqual(result.summary(), "Up to date")


class ManifestSchema(unittest.TestCase):
    def test_json_roundtrip_preserves_entries(self):
        m = Manifest(entries=[entry(user_label="Peaks", folder="Terrain", order=3)])
        back = Manifest.from_json(m.to_json())
        self.assertEqual(len(back.entries), 1)
        self.assertEqual(back.entries[0].user_label, "Peaks")
        self.assertEqual(back.entries[0].folder, "Terrain")
        self.assertEqual(back.entries[0].order, 3)

    def test_corrupt_userdata_yields_empty_manifest_not_exception(self):
        self.assertEqual(Manifest.from_json("{not json").entries, [])
        self.assertEqual(Manifest.from_json("").entries, [])

    def test_unknown_fields_from_newer_version_are_ignored(self):
        raw = '{"version": 99, "entries": [{"id": "x", "gui_parm": "h", "invented": 1}]}'
        m = Manifest.from_json(raw)
        self.assertEqual(m.entries[0].gui_parm, "h")

    def test_name_collisions_disambiguate(self):
        m = Manifest(entries=[entry(gui_parm="scale")])
        self.assertEqual(m.unique_gui_name("scale"), "scale2")

    def test_names_are_sanitized_for_houdini(self):
        m = Manifest()
        self.assertEqual(m.unique_gui_name("peak height!"), "peak_height")
        self.assertEqual(m.unique_gui_name("2nd"), "p_2nd")

    def test_bound_keys_exclude_promoted_parms_from_rescoring(self):
        # The signal-preservation guarantee: an already-surfaced parm must
        # never re-enter the candidate pool.
        m = Manifest(entries=[entry()])
        self.assertIn(("u1", "height", 0), m.bound_keys())

    def test_display_label_prefers_user_rename(self):
        self.assertEqual(entry(user_label="Peaks").display_label(), "Peaks")
        self.assertEqual(entry().display_label(), "Terrain Height")


if __name__ == "__main__":
    unittest.main(verbosity=2)
