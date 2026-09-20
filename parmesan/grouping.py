"""Arranging surfaced controls by where they live in the graph.

A flat list of three hundred controls is no improvement on the graph it came
from -- the point of the tool is finding the one knob you need without
digging, and a long undifferentiated list is digging.

So controls are nested the way the network is: an ``ocean`` group holding a
subnet holding the node holding the parameter. Provenance is then structural
rather than something you read off each row, and the top level stays short
enough to scan.

Pure Python: this is path arithmetic, and path arithmetic is exactly the kind
of thing that quietly breaks at the edges, so it is testable without Houdini.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


def split_path(path: str) -> List[str]:
    """/obj/water_sim/box1 -> ['obj', 'water_sim', 'box1']"""
    return [part for part in (path or "").split("/") if part]


def common_prefix(paths: Sequence[str]) -> str:
    """The deepest network that contains every path.

    Everything above it is context shared by every control, so repeating it
    down the tree would cost a level of nesting and say nothing.
    """
    parts = [split_path(p) for p in paths if p]
    if not parts:
        return ""
    shared: List[str] = []
    for index in range(min(len(p) for p in parts)):
        segment = parts[0][index]
        if all(p[index] == segment for p in parts):
            shared.append(segment)
        else:
            break
    # The node holding the parm is never the prefix: if every control comes
    # from one node, that node is what we are grouping by, not the context.
    if len(shared) == min(len(p) for p in parts):
        shared = shared[:-1]
    return "/" + "/".join(shared) if shared else ""


def _new_group(name: str, path: str) -> Dict[str, Any]:
    return {"name": name, "path": path, "groups": [], "rows": [], "count": 0}


def build(
    rows: List[Dict[str, Any]], context: Optional[str] = None
) -> Tuple[str, List[Dict[str, Any]]]:
    """Nest rows by their node path. Returns (context_path, top_level_groups).

    ``context`` is the network everything is shown relative to -- normally
    where the scan started. Pass it: deriving it from the paths instead means
    that when every control happens to live down one branch, the whole branch
    becomes context and the tree has nothing left to show. ``ocean`` inside a
    subnet inside a network is exactly the shape worth communicating, and it
    is the shape a shared prefix eats.

    Falls back to the deepest shared network when no context is given, and
    ignores one that is not actually above the rows.

    Rank order is preserved: groups appear in the order their first control
    does, so the best-scoring material stays near the top instead of being
    re-sorted alphabetically.
    """
    paths = [row.get("node_path") or "" for row in rows]
    if context is None:
        context = common_prefix(paths)
    else:
        context = "/" + "/".join(split_path(context))
        shared = common_prefix(paths)
        # A context below the rows would push them out of their own tree.
        if not shared.startswith(context.rstrip("/")):
            context = shared
    context_depth = len(split_path(context))

    top: List[Dict[str, Any]] = []
    index: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        segments = split_path(row.get("node_path") or "")[context_depth:]
        if not segments:
            # A control whose node is the context itself, or whose path is
            # unknown. Give it a home rather than dropping it.
            segments = [row.get("node_name") or "(unknown)"]

        parent_groups = top
        walked: List[str] = []
        group = None
        for segment in segments:
            walked.append(segment)
            key = "/".join(walked)
            group = index.get(key)
            if group is None:
                group = _new_group(segment, f"{context}/{key}")
                index[key] = group
                parent_groups.append(group)
            parent_groups = group["groups"]
        group["rows"].append(row)

    _count(top)
    return context, [_collapse_chain(g) for g in top]


def _count(groups: List[Dict[str, Any]]) -> int:
    """Total controls at or under each group, for the headers."""
    total = 0
    for group in groups:
        group["count"] = len(group["rows"]) + _count(group["groups"])
        total += group["count"]
    return total


def _collapse_chain(group: Dict[str, Any]) -> Dict[str, Any]:
    """Fold away levels that hold nothing but one other level.

    A subnet containing a subnet containing the node is three clicks to reach
    one slider. Merging them into ``outer/inner/node`` keeps the information
    and loses the clicking.
    """
    while not group["rows"] and len(group["groups"]) == 1:
        child = group["groups"][0]
        group = {
            "name": f"{group['name']}/{child['name']}",
            "path": child["path"],
            "groups": child["groups"],
            "rows": child["rows"],
            "count": child["count"],
        }
    group["groups"] = [_collapse_chain(child) for child in group["groups"]]
    return group


def flatten(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every row under these groups, depth first. Mostly for tests."""
    out: List[Dict[str, Any]] = []
    for group in groups:
        out.extend(group["rows"])
        out.extend(flatten(group["groups"]))
    return out


def describe(context: str, groups: List[Dict[str, Any]]) -> str:
    """One line for the panel: what is shown and where it came from."""
    total = sum(group["count"] for group in groups)
    where = context or "the scene"
    return (
        f"{total} control{'s' if total != 1 else ''} "
        f"in {len(groups)} group{'s' if len(groups) != 1 else ''} under {where}"
    )
