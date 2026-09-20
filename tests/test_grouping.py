"""Tests for nesting controls by where they live in the graph.

This is path arithmetic, which breaks quietly at the edges: an empty path, one
node, a node at the context root, two branches that diverge at the first
segment. All pure, so all checkable without Houdini.
"""

import unittest

from parmesan import grouping


def row(path, entry_id="x", label="Height"):
    return {
        "entry_id": entry_id,
        "node_path": path,
        "node_name": path.rsplit("/", 1)[-1] if path else "",
        "label": label,
        "parm_type": "float",
        "value": 1.0,
        "spec": {},
        "tooltip": "",
        "expression": "",
    }


class Paths(unittest.TestCase):
    def test_splitting_ignores_empty_segments(self):
        self.assertEqual(grouping.split_path("/obj/geo1/box1"), ["obj", "geo1", "box1"])
        self.assertEqual(grouping.split_path("//obj//geo1/"), ["obj", "geo1"])
        self.assertEqual(grouping.split_path(""), [])

    def test_common_prefix_of_siblings(self):
        self.assertEqual(
            grouping.common_prefix(["/obj/sim/a", "/obj/sim/b"]), "/obj/sim"
        )

    def test_common_prefix_stops_where_paths_diverge(self):
        self.assertEqual(
            grouping.common_prefix(["/obj/ocean/a", "/obj/smoke/b"]), "/obj"
        )

    def test_the_node_itself_is_never_the_context(self):
        # One node's worth of controls should group *by* that node, not have
        # it swallowed into the context and leave nothing to show.
        self.assertEqual(grouping.common_prefix(["/obj/sim/box1"]), "/obj/sim")

    def test_no_shared_prefix_gives_none(self):
        self.assertEqual(grouping.common_prefix(["/obj/a", "/stage/b"]), "")

    def test_empty_input_is_not_an_error(self):
        self.assertEqual(grouping.common_prefix([]), "")


class Nesting(unittest.TestCase):
    def test_siblings_become_sibling_groups(self):
        context, groups = grouping.build(
            [row("/obj/sim/flip", "a"), row("/obj/sim/pyro", "b")]
        )
        self.assertEqual(context, "/obj/sim")
        self.assertEqual([g["name"] for g in groups], ["flip", "pyro"])

    def test_depth_is_preserved(self):
        # ocean -> subnet -> node, which is the case that a flat list loses.
        _, groups = grouping.build([row("/obj/ocean/detail/mountain1", "a")], "/obj")
        self.assertEqual(len(groups), 1)
        self.assertIn("ocean", groups[0]["name"])

    def test_branches_stay_separate(self):
        _, groups = grouping.build(
            [
                row("/obj/show/ocean/waves/mountain1", "a"),
                row("/obj/show/smoke/pyro1", "b"),
            ]
        )
        self.assertEqual(sorted(g["name"] for g in groups), ["ocean/waves/mountain1", "smoke/pyro1"])

    def test_a_shared_branch_keeps_its_children_together(self):
        _, groups = grouping.build(
            [
                row("/obj/sim/ocean/mountain1", "a"),
                row("/obj/sim/ocean/mountain2", "b"),
                row("/obj/sim/smoke/pyro1", "c"),
            ]
        )
        names = [g["name"] for g in groups]
        self.assertIn("ocean", names)
        ocean = next(g for g in groups if g["name"] == "ocean")
        self.assertEqual(
            sorted(child["name"] for child in ocean["groups"]),
            ["mountain1", "mountain2"],
        )

    def test_rows_hang_off_the_node_that_owns_them(self):
        _, groups = grouping.build(
            [row("/obj/sim/ocean/mountain1", "a"), row("/obj/sim/ocean/mountain1", "b")]
        )
        leaf = groups[0]
        while leaf["groups"]:
            leaf = leaf["groups"][0]
        self.assertEqual(len(leaf["rows"]), 2)

    def test_no_control_is_lost_in_the_nesting(self):
        rows = [
            row("/obj/a/b/c/d/node1", "1"),
            row("/obj/a/other", "2"),
            row("/obj/z", "3"),
        ]
        _, groups = grouping.build(rows)
        self.assertEqual(
            sorted(r["entry_id"] for r in grouping.flatten(groups)), ["1", "2", "3"]
        )

    def test_a_missing_path_still_gets_a_home(self):
        blank = row("", "orphan")
        blank["node_name"] = "(moved)"
        _, groups = grouping.build([blank])
        self.assertEqual(len(grouping.flatten(groups)), 1)

    def test_rank_order_decides_group_order(self):
        # The best-scoring material should stay near the top rather than being
        # re-sorted alphabetically.
        _, groups = grouping.build(
            [row("/obj/sim/zebra", "a"), row("/obj/sim/alpha", "b")]
        )
        self.assertEqual([g["name"] for g in groups], ["zebra", "alpha"])


class ChainCollapsing(unittest.TestCase):
    def test_single_child_chains_are_folded(self):
        # outer -> inner -> node, with nothing else in outer or inner, is
        # three clicks to reach one slider.
        _, groups = grouping.build([row("/obj/show/outer/inner/node1", "a")], "/obj/show")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"].count("/"), 2)
        self.assertEqual(groups[0]["groups"], [])

    def test_a_branching_level_is_not_folded_away(self):
        _, groups = grouping.build(
            [row("/obj/sim/ocean/a1", "a"), row("/obj/sim/ocean/b1", "b")],
            "/obj/sim",
        )
        ocean = groups[0]
        self.assertEqual(ocean["name"], "ocean")
        self.assertEqual(len(ocean["groups"]), 2)

    def test_folding_keeps_every_row(self):
        _, groups = grouping.build([row("/obj/a/b/c/d/e/node", "only")], "/obj")
        self.assertEqual(len(grouping.flatten(groups)), 1)


class Counts(unittest.TestCase):
    def test_a_group_counts_everything_beneath_it(self):
        _, groups = grouping.build(
            [
                row("/obj/sim/ocean/m1", "a"),
                row("/obj/sim/ocean/m2", "b"),
                row("/obj/sim/smoke/p1", "c"),
            ]
        )
        ocean = next(g for g in groups if g["name"] == "ocean")
        self.assertEqual(ocean["count"], 2)

    def test_counts_survive_chain_folding(self):
        _, groups = grouping.build([row("/obj/a/b/c/node", "x")], "/obj")
        self.assertEqual(groups[0]["count"], 1)

    def test_description_reads_as_english(self):
        context, groups = grouping.build(
            [row("/obj/sim/a", "1"), row("/obj/sim/b", "2")]
        )
        self.assertEqual(
            grouping.describe(context, groups),
            "2 controls in 2 groups under /obj/sim",
        )

    def test_description_handles_singulars(self):
        context, groups = grouping.build([row("/obj/sim/a", "1")])
        self.assertIn("1 control in 1 group", grouping.describe(context, groups))


if __name__ == "__main__":
    unittest.main(verbosity=2)
