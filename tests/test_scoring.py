"""Tests for the ranking heuristic.

This is the part of the tool that decides what an artist sees, so it gets the
most tests. All pure: the scan hands scoring plain dicts, so every judgement
below is checkable without Houdini.
"""

import unittest

from parmesan import scoring


def cand(**kw):
    """A candidate with no evidence at all, overridden per test."""
    base = dict(
        node_path="/obj/geo1/box1",
        node_name="box1",
        source_parm="sizex",
        parm_label="Size X",
        index=0,
        type_name="box",
        edited=False,
        animated=False,
        referenced_count=0,
        has_expression=False,
        node_renamed=False,
        on_display_node=False,
        locked=False,
        disabled=False,
    )
    base.update(kw)
    return base


class Signals(unittest.TestCase):
    def test_untouched_plumbing_scores_nothing_much(self):
        points, reasons = scoring.score(cand(source_parm="group", parm_label="Group"))
        self.assertLess(points, scoring.MIN_SCORE)
        self.assertNotIn("edited", reasons)

    def test_edited_is_the_strongest_single_signal(self):
        edited, _ = scoring.score(cand(edited=True))
        animated, _ = scoring.score(cand(animated=True))
        renamed, _ = scoring.score(cand(node_renamed=True))
        self.assertGreater(edited, animated)
        self.assertGreater(edited, renamed)

    def test_edited_alone_clears_the_bar(self):
        points, reasons = scoring.score(cand(edited=True))
        self.assertGreaterEqual(points, scoring.MIN_SCORE)
        self.assertIn("edited", reasons)

    def test_references_accumulate_but_are_capped(self):
        one, _ = scoring.score(cand(referenced_count=1))
        many, _ = scoring.score(cand(referenced_count=50))
        self.assertGreater(many, one)
        self.assertLessEqual(many - one, scoring.W_REFERENCED_CAP)

    def test_reference_count_is_reported_in_english(self):
        _, reasons = scoring.score(cand(referenced_count=3))
        self.assertIn("used by 3 other parms", reasons)
        _, single = scoring.score(cand(referenced_count=1))
        self.assertIn("used by 1 other parm", single)

    def test_locked_parms_are_pushed_below_the_bar(self):
        # A locked parm cannot be written, so a control for it is dead on
        # arrival however interesting it otherwise looks.
        points, _ = scoring.score(cand(edited=True, locked=True))
        self.assertLess(points, scoring.MIN_SCORE)

    def test_every_signal_contributes_a_reason(self):
        points, reasons = scoring.score(
            cand(edited=True, animated=True, referenced_count=2, node_renamed=True)
        )
        for expected in ("edited", "animated", "renamed node"):
            self.assertIn(expected, reasons)

    def test_missing_keys_are_absent_evidence_not_an_error(self):
        points, reasons = scoring.score({"source_parm": "scale"})
        self.assertIsInstance(points, float)


class NameInterest(unittest.TestCase):
    def test_knob_like_names_score(self):
        points, reason = scoring.name_interest("scale", "Scale")
        self.assertEqual(points, scoring.W_NAME_HINT)
        self.assertTrue(reason)

    def test_plumbing_names_are_penalised(self):
        points, _ = scoring.name_interest("vexpression", "VEXpression")
        self.assertEqual(points, scoring.W_NAME_PENALTY)

    def test_anti_hints_win_over_hints(self):
        # "groupsize" contains "size" but it is still group plumbing.
        points, _ = scoring.name_interest("groupsize", "Group Size")
        self.assertEqual(points, scoring.W_NAME_PENALTY)

    def test_the_label_can_carry_the_signal(self):
        # Cryptic internal name, human label.
        points, _ = scoring.name_interest("pr0", "Peak Height")
        self.assertEqual(points, scoring.W_NAME_HINT)

    def test_unremarkable_names_are_neutral(self):
        points, reason = scoring.name_interest("xyzzy", "Xyzzy")
        self.assertEqual(points, 0.0)
        self.assertEqual(reason, "")


class DefaultNodeNames(unittest.TestCase):
    def test_houdini_default_names_are_recognised(self):
        self.assertTrue(scoring.is_default_node_name("box1", "box"))
        self.assertTrue(scoring.is_default_node_name("mountain12", "mountain"))
        self.assertTrue(scoring.is_default_node_name("box", "box"))

    def test_renamed_nodes_are_recognised(self):
        self.assertFalse(scoring.is_default_node_name("hero_rock", "box"))
        self.assertFalse(scoring.is_default_node_name("cliff_noise", "mountain"))

    def test_namespaced_types_strip_to_the_bare_name(self):
        self.assertTrue(scoring.is_default_node_name("rigpose1", "kinefx::rigpose"))
        self.assertFalse(scoring.is_default_node_name("arm_pose", "kinefx::rigpose"))

    def test_versioned_types_ignore_the_version(self):
        self.assertTrue(
            scoring.is_default_node_name("rigpose2", "kinefx::rigpose::2.0")
        )

    def test_unknown_inputs_claim_no_authorship(self):
        # No evidence should never be read as positive evidence.
        self.assertTrue(scoring.is_default_node_name("", ""))
        self.assertTrue(scoring.is_default_node_name("thing", ""))


class TypeInterest(unittest.TestCase):
    def test_look_defining_types_get_a_bonus(self):
        points, reason = scoring.node_type_interest("mountain")
        self.assertGreater(points, 0)
        self.assertTrue(reason)

    def test_plumbing_types_get_a_penalty(self):
        points, _ = scoring.node_type_interest("merge")
        self.assertLess(points, 0)

    def test_unknown_types_are_neutral(self):
        # A graph full of custom HDAs must fall back to the evidence signals
        # rather than being penalised for being unrecognised.
        points, _ = scoring.node_type_interest("studio::super_secret_asset::1.2")
        self.assertEqual(points, 0.0)

    def test_namespaced_known_types_still_match(self):
        points, _ = scoring.node_type_interest("Labs::mountain::2.0")
        self.assertGreater(points, 0)


class Ranking(unittest.TestCase):
    def test_low_scorers_are_dropped_entirely(self):
        ranked = scoring.rank([cand(source_parm="group")])
        self.assertEqual(ranked, [])

    def test_results_carry_their_score_and_reasons(self):
        ranked = scoring.rank([cand(edited=True, animated=True)])
        self.assertEqual(len(ranked), 1)
        self.assertGreater(ranked[0]["score"], 0)
        self.assertIn("edited", ranked[0]["why"])

    def test_input_dicts_are_not_mutated(self):
        original = cand(edited=True)
        scoring.rank([original])
        self.assertNotIn("score", original)

    def test_best_first(self):
        weak = cand(node_path="/obj/a", source_parm="scale", edited=True)
        strong = cand(
            node_path="/obj/b", source_parm="height", edited=True, animated=True
        )
        ranked = scoring.rank([weak, strong])
        self.assertEqual(ranked[0]["node_path"], "/obj/b")

    def test_nothing_is_dropped_for_being_past_an_arbitrary_count(self):
        # Forty parms with identical evidence: a "top 12" would keep twelve of
        # them and silently bin twenty-eight that are exactly as well
        # evidenced. The quality bar decides, not a number.
        many = [
            cand(node_path=f"/obj/n{i}", edited=True, animated=True) for i in range(40)
        ]
        self.assertEqual(len(scoring.rank(many)), 40)

    def test_an_explicit_limit_is_still_honoured(self):
        many = [
            cand(node_path=f"/obj/n{i}", edited=True, animated=True) for i in range(40)
        ]
        self.assertEqual(len(scoring.rank(many, limit=5)), 5)

    def test_one_node_cannot_fill_the_whole_panel(self):
        # A heavily-tweaked node would otherwise crowd out every other node's
        # single important knob.
        hoggish = [
            cand(node_path="/obj/hog", source_parm=f"scale{i}", edited=True, animated=True)
            for i in range(20)
        ]
        ranked = scoring.rank(hoggish, limit=12, max_per_node=4)
        self.assertEqual(len(ranked), 4)

    def test_the_cap_leaves_room_for_other_nodes(self):
        hoggish = [
            cand(node_path="/obj/hog", source_parm=f"scale{i}", edited=True, animated=True)
            for i in range(20)
        ]
        modest = [cand(node_path="/obj/quiet", source_parm="height", edited=True)]
        ranked = scoring.rank(hoggish + modest, limit=12, max_per_node=4)
        paths = {c["node_path"] for c in ranked}
        self.assertIn("/obj/quiet", paths)

    def test_identical_input_ranks_identically(self):
        # A panel that reshuffles between identical scans looks broken.
        items = [
            cand(node_path=f"/obj/n{i}", source_parm="scale", edited=True)
            for i in range(8)
        ]
        first = [c["node_path"] for c in scoring.rank(items)]
        second = [c["node_path"] for c in scoring.rank(list(reversed(items)))]
        self.assertEqual(first, second)

    def test_empty_input_is_not_an_error(self):
        self.assertEqual(scoring.rank([]), [])

    def test_summary_reads_as_english(self):
        ranked = scoring.rank(
            [
                cand(node_path="/obj/a", edited=True),
                cand(node_path="/obj/b", source_parm="height", edited=True),
            ]
        )
        self.assertEqual(scoring.summarize(ranked), "2 parameters from 2 nodes")

    def test_summary_handles_singulars(self):
        ranked = scoring.rank([cand(edited=True)])
        self.assertEqual(scoring.summarize(ranked), "1 parameter from 1 node")

    def test_summary_of_nothing_says_so(self):
        self.assertIn("Nothing", scoring.summarize([]))


class Levels(unittest.TestCase):
    """How much to surface is a quality bar, not a count."""

    def graph(self, size=10):
        """Three tiers of evidence, so the levels have something to separate."""
        strong = [
            # Edited, animated, on a node someone named, referenced elsewhere.
            cand(
                node_path=f"/obj/strong{i}", source_parm="height", edited=True,
                animated=True, node_renamed=True, referenced_count=3,
            )
            for i in range(size)
        ]
        middling = [
            # Edited, with a knob-like name and nothing else behind it.
            cand(node_path=f"/obj/mid{i}", source_parm="height", edited=True)
            for i in range(size)
        ]
        faint = [
            # Never edited; only a renamed node and a promising name.
            cand(node_path=f"/obj/faint{i}", source_parm="height", node_renamed=True)
            for i in range(size)
        ]
        return strong + middling + faint

    def test_every_level_is_configured(self):
        for key, settings in scoring.LEVELS.items():
            self.assertTrue(settings["label"], f"{key} has no label")
            self.assertTrue(settings["hint"], f"{key} has no hint")
            self.assertIsInstance(settings["min_score"], float)

    def test_the_default_level_exists(self):
        self.assertIn(scoring.DEFAULT_LEVEL, scoring.LEVELS)

    def test_stricter_levels_surface_less(self):
        graph = self.graph()
        counts = {
            key: len(scoring.rank_at(graph, key)) for key in scoring.LEVELS
        }
        self.assertLess(counts["essentials"], counts["recommended"])
        self.assertLess(counts["recommended"], counts["everything"])

    def test_everything_meaningful_has_no_per_node_cap(self):
        # A node with twenty well-evidenced parms should give all twenty at
        # the loosest setting; capping there would contradict the label.
        hoggish = [
            cand(node_path="/obj/hog", source_parm=f"height{i}", edited=True,
                 animated=True, node_renamed=True)
            for i in range(20)
        ]
        self.assertEqual(len(scoring.rank_at(hoggish, "everything")), 20)

    def test_tighter_levels_still_cap_a_single_node(self):
        hoggish = [
            cand(node_path="/obj/hog", source_parm=f"height{i}", edited=True,
                 animated=True, node_renamed=True, referenced_count=3)
            for i in range(20)
        ]
        self.assertEqual(len(scoring.rank_at(hoggish, "essentials")), 2)

    def test_unevidenced_parms_are_excluded_at_every_level(self):
        plumbing = [cand(source_parm="group", type_name="merge")]
        for key in scoring.LEVELS:
            self.assertEqual(scoring.rank_at(plumbing, key), [], f"at {key}")

    def test_an_unknown_level_falls_back_instead_of_raising(self):
        graph = self.graph(5)
        self.assertEqual(
            len(scoring.rank_at(graph, "nonsense")),
            len(scoring.rank_at(graph, scoring.DEFAULT_LEVEL)),
        )


class RealisticGraph(unittest.TestCase):
    """The judgement that matters: does a plausible graph rank sensibly?"""

    def setUp(self):
        self.graph = [
            # The artist's hero knob: renamed node, edited, good name.
            cand(
                node_path="/obj/geo1/cliff_noise",
                node_name="cliff_noise",
                source_parm="height",
                parm_label="Height",
                type_name="mountain",
                edited=True,
                node_renamed=True,
                on_display_node=True,
            ),
            # A hub parm other things reference.
            cand(
                node_path="/obj/geo1/master_scale",
                node_name="master_scale",
                source_parm="scale",
                parm_label="Scale",
                type_name="null",
                edited=True,
                node_renamed=True,
                referenced_count=4,
            ),
            # Pure plumbing on a default-named node.
            cand(
                node_path="/obj/geo1/merge1",
                node_name="merge1",
                source_parm="group",
                parm_label="Group",
                type_name="merge",
            ),
            # Touched, but locked: a dead control.
            cand(
                node_path="/obj/geo1/box1",
                node_name="box1",
                source_parm="sizex",
                parm_label="Size X",
                type_name="box",
                edited=True,
                locked=True,
            ),
            # A file path someone typed: edited, but not art direction.
            cand(
                node_path="/obj/geo1/file1",
                node_name="file1",
                source_parm="file",
                parm_label="Geometry File",
                type_name="file",
                edited=True,
            ),
        ]

    def test_the_hero_knob_ranks_first(self):
        ranked = scoring.rank(self.graph)
        self.assertEqual(ranked[0]["node_name"], "cliff_noise")

    def test_the_referenced_hub_makes_the_cut(self):
        names = [c["node_name"] for c in scoring.rank(self.graph)]
        self.assertIn("master_scale", names)

    def test_plumbing_and_dead_controls_are_excluded(self):
        names = [c["node_name"] for c in scoring.rank(self.graph)]
        self.assertNotIn("merge1", names)
        self.assertNotIn("box1", names)

    def test_an_edited_file_path_does_not_outrank_real_knobs(self):
        ranked = scoring.rank(self.graph)
        names = [c["node_name"] for c in ranked]
        if "file1" in names:
            self.assertGreater(names.index("file1"), names.index("cliff_noise"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
