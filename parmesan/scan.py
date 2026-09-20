"""Walking a whole graph and gathering evidence about every parameter.

The hou-dependent half of the scoring pass. This module reads; scoring.py
decides. Keeping them apart is what makes the ranking testable, and it means
the weights can be argued about without a Houdini session.

Cost is the design constraint here. A production graph is thousands of nodes
with tens of parms each, and the panel has to feel like one click rather than
a progress bar. So signals are gathered in two tiers: everything cheap is read
for every parm, and the expensive question -- "what else in this scene points
at this parm?" -- is asked only of parms that already look interesting. A parm
sitting at its default with a plumbing name is not worth a reference walk.
"""

from __future__ import annotations

import contextlib
from typing import Any, Dict, List, Optional

from . import promote, scoring, sync
from .manifest import Manifest

try:
    import hou
except ImportError:  # pragma: no cover
    hou = None


#: Stop walking rather than hang on a pathological scene. Reaching this means
#: the scan is reported as partial instead of silently truncated.
MAX_NODES = 3000

#: Per-node parm ceiling. Some HDAs expose hundreds of parms; the first slice
#: is where the interesting ones almost always are, and this keeps a single
#: monster asset from dominating scan time.
MAX_PARMS_PER_NODE = 200


#: How often to report progress. Every node would spend more time drawing a
#: progress bar than reading parms.
PROGRESS_EVERY = 25


class ScanReport:
    """What a scan found, plus what it had to give up on."""

    def __init__(self) -> None:
        self.candidates: List[Dict[str, Any]] = []
        self.nodes_visited = 0
        self.parms_examined = 0
        self.truncated = False
        self.interrupted = False
        self.errors: List[str] = []

    def summary(self) -> str:
        line = (
            f"Looked at {self.parms_examined} parameters on "
            f"{self.nodes_visited} nodes"
        )
        if self.interrupted:
            line += " (stopped early)"
        if self.truncated:
            line += f" (stopped at the {MAX_NODES}-node limit)"
        if self.errors:
            line += f"; {len(self.errors)} node(s) could not be read"
        return line


def _interrupted_error():
    """Houdini's escape exception, or a type nothing raises where absent."""
    return getattr(hou, "OperationInterrupted", ()) if hou is not None else ()


@contextlib.contextmanager
def _escapable(title: str):
    """A progress operation that Escape can cancel.

    A scan with no count limit can cross a whole production scene, and an
    artist who realises they pointed it at the wrong network should not have
    to wait it out. Degrades to a plain no-op where the API is unavailable,
    so the scan still runs, just without a way to stop it.
    """
    if hou is None or not hasattr(hou, "InterruptableOperation"):
        yield None
        return
    with hou.InterruptableOperation(
        title, long_operation_name=title, open_interrupt_dialog=True
    ) as operation:
        yield operation


def _require_hou() -> None:
    if hou is None:
        raise RuntimeError("This function requires a running Houdini session.")


def default_root(panel_node):
    """Where to scan from when the artist has not said.

    The control node's parent: a panel almost always drives the network it
    lives in, and defaulting to the whole scene would drag in cameras, lights
    and every other object's transforms on the first click.
    """
    _require_hou()
    return sync._search_root(panel_node)


def walk(root) -> List[Any]:
    """Every node at or under ``root``, capped."""
    _require_hou()
    nodes = [root]
    try:
        nodes.extend(root.allSubChildren(recurse_in_locked_nodes=False))
    except TypeError:
        # Kwarg name differs on some builds; the positional form is the same
        # call and a locked-asset walk is still better than no walk.
        nodes.extend(root.allSubChildren())
    return nodes[:MAX_NODES]


def _reference_count(parm) -> int:
    """How many other parms point at this one."""
    try:
        return len(parm.parmsReferencingThis())
    except (AttributeError, hou.OperationFailed):
        return 0


def _has_expression(parm) -> bool:
    try:
        parm.expression()
        return True
    except hou.OperationFailed:
        return False
    except AttributeError:  # pragma: no cover
        return False


def _is_disabled(parm) -> bool:
    try:
        return parm.isDisabled()
    except AttributeError:  # pragma: no cover
        return False


def scan(
    root,
    manifest: Optional[Manifest] = None,
    skip_node=None,
) -> ScanReport:
    """Gather scoring evidence for every promotable parm under ``root``.

    ``manifest`` excludes already-surfaced parms, so a second scan proposes new
    material instead of re-proposing what is already on the panel.
    """
    _require_hou()
    report = ScanReport()
    bound = manifest.bound_keys() if manifest is not None else set()
    skip_path = skip_node.path() if skip_node is not None else None

    nodes = walk(root)
    report.truncated = len(nodes) >= MAX_NODES
    total = float(len(nodes)) or 1.0

    try:
        with _escapable("Scanning graph") as operation:
            for index, node in enumerate(nodes):
                # Escape raises out of updateProgress, so the partial result
                # is kept and reported rather than thrown away.
                if operation is not None and index % PROGRESS_EVERY == 0:
                    operation.updateProgress(index / total)
                if skip_path is not None and node.path() == skip_path:
                    continue
                try:
                    report.candidates.extend(_scan_node(node, bound, report))
                    report.nodes_visited += 1
                except hou.OperationFailed as exc:
                    # One unreadable node must not abort a scan of three thousand.
                    report.errors.append(f"{node.path()}: {exc}")
    except _interrupted_error():
        report.interrupted = True

    return report


def _scan_node(node, bound: set, report: ScanReport) -> List[Dict[str, Any]]:
    """Evidence for one node's parms."""
    type_name = node.type().name()
    node_renamed = not scoring.is_default_node_name(node.name(), type_name)

    try:
        on_display = bool(node.isDisplayFlagSet())
    except AttributeError:
        on_display = False  # not a SOP, or a context without display flags

    stamp = node.userData(sync.STAMP_KEY) or ""
    out: List[Dict[str, Any]] = []

    for parm in node.parms()[:MAX_PARMS_PER_NODE]:
        report.parms_examined += 1
        if not promote.is_promotable(parm):
            continue
        if (stamp, parm.name(), parm.componentIndex()) in bound:
            continue

        candidate = promote.describe_candidate(parm)
        candidate.update(
            {
                "type_name": type_name,
                "node_renamed": node_renamed,
                "on_display_node": on_display,
                "disabled": _is_disabled(parm),
                "has_expression": False,
                "referenced_count": 0,
            }
        )

        # Second tier: only worth asking about parms that already look like
        # they matter. An untouched plumbing parm gets no reference walk.
        name_points, _ = scoring.name_interest(
            candidate["source_parm"], candidate["parm_label"]
        )
        promising = (
            candidate["edited"]
            or candidate["animated"]
            or node_renamed
            or name_points > 0
        )
        if promising:
            candidate["has_expression"] = _has_expression(parm)
            candidate["referenced_count"] = _reference_count(parm)

        out.append(candidate)

    return out


def propose(panel_node, root=None, level: str = scoring.DEFAULT_LEVEL):
    """Scan and rank in one call. Returns (ranked, report).

    This is what the panel's one-click action runs. How much comes back is
    decided by the evidence bar of ``level``, not by a count.
    """
    _require_hou()
    if root is None:
        root = default_root(panel_node)
    manifest = sync.load_manifest(panel_node)
    report = scan(root, manifest, skip_node=panel_node)
    ranked = scoring.rank_at(report.candidates, level)
    return ranked, report
