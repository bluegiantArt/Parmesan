"""Deciding which parameters matter, from evidence rather than from a list.

This is the heart of the tool. Given plain dicts describing every promotable
parm in a graph -- gathered by scan.py, which does the hou-dependent reading --
this module ranks them so the panel can surface the important ones in one
action, with no node-by-node hunting.

Pure Python, therefore testable: the weights below are opinions, and opinions
need to be arguable and adjustable without a Houdini licence in the loop.

The ranking rests on one idea: **a parameter that has been touched is a
parameter that matters.** Everything else is corroboration. That is also why
nothing in this project writes channel references onto source parms -- doing so
would make isAtDefault() permanently false and destroy the strongest signal
here on the second refresh.

Every score carries its reasons, so the panel can always answer "why is this
on my panel?". A heuristic nobody can interrogate is a heuristic nobody trusts.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# weights
# --------------------------------------------------------------------------

#: The artist changed it from its default. The whole premise.
W_EDITED = 5.0

#: Keyframed or time-dependent: not just changed, changed over time.
W_ANIMATED = 4.0

#: Other parms reference this one, so it is literally "most used" in the
#: graph. Counted per referrer and capped.
#:
#: The cap is deliberately below W_EDITED. Fan-out is strong corroboration,
#: but a hub parm is often internal wiring -- a control null feeding a dozen
#: expressions is real, and so is a rig's private plumbing. Keeping the cap
#: under the primary signal means no amount of corroboration can outrank
#: direct evidence that the artist touched something.
W_REFERENCED_EACH = 2.0
W_REFERENCED_CAP = 4.0

#: It is driven by an expression, so someone deliberately wired it.
W_EXPRESSION = 1.0

#: The node was renamed from its Houdini default (box1, mountain2). Renaming
#: is an act of authorship: artists name what they care about.
W_RENAMED_NODE = 2.0

#: The node carries the display flag -- it is what the artist is looking at.
W_DISPLAY_NODE = 1.5

#: The parm name reads like an art direction knob rather than plumbing.
W_NAME_HINT = 1.5
W_NAME_PENALTY = -2.5

#: Locked parms cannot be written, so surfacing one produces a dead control.
W_LOCKED = -6.0

#: Greyed out by a conditional, so it does nothing in the current state.
W_DISABLED = -2.0

#: Below this, there is no real evidence the parameter matters. A candidate
#: under it is not "ranked lower", it is unevidenced.
MIN_SCORE = 3.0

#: How much to surface, as a quality bar rather than a count.
#:
#: A fixed "top N" is the wrong control: N is a number nobody can justify, and
#: it silently drops parameters that are every bit as meaningful as the ones
#: above the cut. What an artist actually wants to say is how strong the
#: evidence has to be, so that is what these offer. The count then falls out
#: of the graph rather than being imposed on it.
#:
#: ``per_node`` still caps any single node, because one heavily-tweaked node
#: with forty edited parms would otherwise bury every other node's one
#: important knob. None means no cap.
LEVELS = {
    "essentials": {
        "label": "Just the essentials",
        "min_score": 9.0,
        "per_node": 2,
        "hint": "Only parameters with strong, corroborated evidence.",
    },
    "recommended": {
        "label": "Recommended",
        "min_score": 6.0,
        "per_node": 4,
        "hint": "Edited parameters with something else backing them up.",
    },
    "everything": {
        "label": "Everything meaningful",
        "min_score": MIN_SCORE,
        "per_node": None,
        "hint": "Every parameter with any real evidence behind it.",
    },
}

DEFAULT_LEVEL = "recommended"

#: Above this the panel says so in its status line -- a note, not a prompt.
#: Interrupting someone to confirm a number they can already see is noise.
LARGE_PANEL = 60


# --------------------------------------------------------------------------
# node type interest
# --------------------------------------------------------------------------

#: Types whose parameters tend to be look-defining. Not exhaustive and not
#: meant to be -- an unknown type scores 0 here and relies on the evidence
#: signals, which is the correct default for a graph full of custom HDAs.
TYPE_BONUS = {
    "mountain": 2.0,
    "attribnoise": 2.0,
    "noise": 2.0,
    "attribwrangle": 0.5,
    "scatter": 1.5,
    "copytopoints": 1.5,
    "copyxform": 1.5,
    "polyextrude": 1.5,
    "polybevel": 1.0,
    "sweep": 1.5,
    "bend": 1.5,
    "twist": 1.5,
    "taper": 1.5,
    "linear": 1.0,
    "subdivide": 1.0,
    "remesh": 1.0,
    "resample": 1.0,
    "carve": 1.0,
    "smooth": 1.0,
    "fuse": 0.5,
    "transform": 1.0,
    "xform": 1.0,
    "vdbsmooth": 1.0,
    "voronoifracture": 1.5,
    "labs::mountain": 2.0,
}

#: Plumbing: structural nodes whose parms are wiring, not art direction.
TYPE_PENALTY = {
    "merge": -1.5,
    "null": -1.5,
    "output": -1.5,
    "switch": -1.0,
    "object_merge": -1.0,
    "blast": -0.5,
    "delete": -0.5,
    "groupcreate": -1.0,
    "grouprange": -1.0,
    "groupexpression": -1.0,
    "file": -1.0,
    "filecache": -1.0,
    "rop_geometry": -1.5,
    "subnet": -0.5,
}


# --------------------------------------------------------------------------
# parm name interest
# --------------------------------------------------------------------------

#: Substrings that read as art direction. Matched against the parm name and
#: its label, so "Peak Height" scores through the label even if the internal
#: name is cryptic.
NAME_HINTS = (
    "size", "scale", "radius", "height", "width", "length", "depth",
    "amount", "strength", "intensity", "density", "count", "number",
    "seed", "offset", "angle", "rotate", "rotation", "spin",
    "speed", "rate", "frequency", "freq", "roughness", "detail",
    "iteration", "division", "segment", "thickness", "spacing",
    "random", "variance", "blend", "mix", "weight", "falloff",
    "distance", "threshold", "tolerance", "smooth", "level",
    "spread", "twist", "bend", "taper", "octave", "gain",
    "lacunarity", "translate", "uniformscale", "pscale", "elevation",
    "roundness", "softness", "contrast", "gamma", "bias", "power",
)

#: Substrings that read as plumbing, naming, or code. A parm called
#: "vexpression" is not a knob an artist wants on a control panel.
NAME_ANTI_HINTS = (
    "group", "attrib", "attribute", "class", "pattern",
    "vexpression", "snippet", "expression", "code", "callback",
    "path", "file", "folder", "dir", "name", "label", "message",
    "comment", "sepparm", "stdswitcher", "button", "execute",
    "reload", "cache", "usdpath", "primpath", "lop", "bind",
)


def name_interest(parm_name: str, parm_label: str = "") -> Tuple[float, str]:
    """Score a parm by what it is called. Returns (points, reason).

    The anti-hints are checked first and win outright: "groupsize" contains
    "size", but it is still group plumbing.
    """
    haystack = f"{parm_name} {parm_label}".lower()
    for bad in NAME_ANTI_HINTS:
        if bad in haystack:
            return W_NAME_PENALTY, ""
    for good in NAME_HINTS:
        if good in haystack:
            return W_NAME_HINT, "knob-like name"
    return 0.0, ""


# --------------------------------------------------------------------------
# node names
# --------------------------------------------------------------------------

_TRAILING_DIGITS = re.compile(r"\d+$")
_VERSION_PART = re.compile(r"[\d.]+")


def bare_type(type_name: str) -> str:
    """Strip namespace and version from a node type name.

    ``Labs::mountain::2.0`` and ``kinefx::rigpose`` both reduce to the name
    Houdini uses when naming new nodes, which is what both the type table and
    the default-name check need to compare against.
    """
    if not type_name:
        return ""
    parts = type_name.split("::")
    # A trailing version component ("2.0") is not part of the name.
    if len(parts) > 1 and _VERSION_PART.fullmatch(parts[-1]):
        parts = parts[:-1]
    return parts[-1].lower()


def is_default_node_name(node_name: str, type_name: str) -> bool:
    """Is this node still called what Houdini called it?

    Houdini names a new node after its type plus a number: box1, mountain2,
    attribnoise1. Namespaced and versioned types (``kinefx::rigpose::2.0``)
    are named after the bare type, so both are stripped before comparing.

    A node the artist renamed is a node the artist thought about, which is why
    this is worth a signal at all.
    """
    if not node_name or not type_name:
        return True  # no evidence of authorship, so claim none

    stripped = _TRAILING_DIGITS.sub("", node_name.lower())
    return stripped == bare_type(type_name)


def node_type_interest(type_name: str) -> Tuple[float, str]:
    """Score a node by its type. Unknown types score zero, deliberately."""
    if not type_name:
        return 0.0, ""
    bare = bare_type(type_name)
    for table, reason in ((TYPE_BONUS, "look-defining node"), (TYPE_PENALTY, "")):
        if type_name in table:
            return table[type_name], reason
        if bare in table:
            return table[bare], reason
    return 0.0, ""


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def score(candidate: Dict[str, Any]) -> Tuple[float, List[str]]:
    """Score one candidate. Returns (points, reasons).

    Missing keys are treated as absent evidence rather than as errors, so a
    caller that gathers only some signals still gets a usable ranking.
    """
    points = 0.0
    reasons: List[str] = []

    if candidate.get("edited"):
        points += W_EDITED
        reasons.append("edited")

    if candidate.get("animated"):
        points += W_ANIMATED
        reasons.append("animated")

    refs = int(candidate.get("referenced_count") or 0)
    if refs > 0:
        points += min(refs * W_REFERENCED_EACH, W_REFERENCED_CAP)
        reasons.append(f"used by {refs} other parm{'s' if refs > 1 else ''}")

    if candidate.get("has_expression"):
        points += W_EXPRESSION
        reasons.append("expression-driven")

    if candidate.get("node_renamed"):
        points += W_RENAMED_NODE
        reasons.append("renamed node")

    if candidate.get("on_display_node"):
        points += W_DISPLAY_NODE
        reasons.append("display node")

    name_points, name_reason = name_interest(
        candidate.get("source_parm", ""), candidate.get("parm_label", "")
    )
    points += name_points
    if name_reason:
        reasons.append(name_reason)

    type_points, type_reason = node_type_interest(candidate.get("type_name", ""))
    points += type_points
    if type_reason:
        reasons.append(type_reason)

    # Penalties last, and they are allowed to sink a candidate outright: a
    # locked parm makes a control that cannot do anything.
    if candidate.get("locked"):
        points += W_LOCKED
        reasons.append("locked")
    if candidate.get("disabled"):
        points += W_DISABLED
        reasons.append("greyed out")

    return points, reasons


def level_settings(level: str) -> Dict[str, Any]:
    """Look up a named level, falling back to the default rather than raising."""
    return LEVELS.get(level, LEVELS[DEFAULT_LEVEL])


def rank_at(
    candidates: List[Dict[str, Any]], level: str = DEFAULT_LEVEL
) -> List[Dict[str, Any]]:
    """Rank at a named level. No count limit: the quality bar decides."""
    settings = level_settings(level)
    return rank(
        candidates,
        limit=None,
        min_score=settings["min_score"],
        max_per_node=settings["per_node"],
    )


def rank(
    candidates: List[Dict[str, Any]],
    limit: Optional[int] = None,
    min_score: float = MIN_SCORE,
    max_per_node: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Score, filter and order candidates for surfacing.

    Returns new dicts carrying ``score`` and ``why``, best first. Ties break on
    node path then parm name so the same graph always ranks the same way --
    a panel that reshuffles itself between identical scans looks broken.

    ``limit`` defaults to None: capping at an arbitrary count would drop
    parameters with exactly as much evidence as the ones that made the cut.
    ``max_per_node`` is applied first, so a cap that is set still leaves room
    for the rest of the graph.
    """
    scored: List[Dict[str, Any]] = []
    for candidate in candidates:
        points, reasons = score(candidate)
        if points < min_score:
            continue
        enriched = dict(candidate)
        enriched["score"] = round(points, 2)
        enriched["why"] = ", ".join(reasons)
        scored.append(enriched)

    scored.sort(
        key=lambda c: (
            -c["score"],
            str(c.get("node_path", "")),
            str(c.get("source_parm", "")),
            int(c.get("index", 0) or 0),
        )
    )

    if max_per_node:
        seen: Dict[str, int] = {}
        capped = []
        for candidate in scored:
            key = str(candidate.get("node_path", ""))
            if seen.get(key, 0) >= max_per_node:
                continue
            seen[key] = seen.get(key, 0) + 1
            capped.append(candidate)
        scored = capped

    if limit is not None and limit >= 0:
        scored = scored[:limit]
    return scored


def summarize(ranked: List[Dict[str, Any]]) -> str:
    """One line describing a ranking, for the panel's status area."""
    if not ranked:
        return "Nothing scored high enough to surface"
    nodes = len({c.get("node_path") for c in ranked})
    return (
        f"{len(ranked)} parameter{'s' if len(ranked) != 1 else ''} "
        f"from {nodes} node{'s' if nodes != 1 else ''}"
    )
