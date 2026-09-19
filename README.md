# parmesan

Surface the parameters that matter from any Houdini graph, without taking
the graph hostage.

## Model

**The graph is master.** Every value lives on the node parms, exactly where
it always did. The panel is a generated view over them:

| Direction | Mechanism | When |
|---|---|---|
| Panel -> graph | Python parm callback writes through | On edit, immediately |
| Graph -> panel | Refresh re-reads and diffs | On demand |

No channel references are ever written onto source parms. That is the whole
design, and two things follow from it:

1. **Source parms stay editable.** You can still open the node and work
   normally — the promise the tool exists to keep.
2. **`isAtDefault()` keeps working.** Channel-referencing a parm makes it
   permanently non-default, which would poison the "the artist touched this,
   so it matters" signal on every refresh after the first. Callbacks leave
   the signal intact.

## Layout

```
parmesan/
  manifest.py   schema + persistence   (pure Python)
  diff.py       reconciliation engine  (pure Python)
  callbacks.py  parm templates + generated callback   (needs hou)
  sync.py       observe / write-through / apply        (needs hou)
tests/
  test_diff.py  25 tests, no Houdini required
```

Observation and decision are split on purpose: `sync.observe()` does the
hou-dependent reading and hands plain dicts to `diff.compute()`, so the
logic most likely to contain bugs is testable off-host.

## Install

Put the repo root on Houdini's Python path:

```bash
export PYTHONPATH="/path/to/parmesan:$PYTHONPATH"
```

or add it in `houdini.env`. The generated callbacks do `from parmesan import
sync`, so if this is wrong every panel edit raises a `hou.NodeError` naming
the problem rather than failing silently.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## Smoke test in Houdini

Nothing below has run against a live Houdini — this is the first thing to
try, and the first place it will break.

```python
import hou
from parmesan import sync, callbacks
from parmesan.manifest import Entry, Manifest

geo    = hou.node("/obj").createNode("geo", "parmesan_test")
box    = geo.createNode("box")
panel  = geo.createNode("null", "CONTROLS")

m = Manifest()
m.entries.append(Entry(
    gui_parm=m.unique_gui_name("size"),
    source_uuid=sync.stamp(box),
    source_parm="sizex",
    source_path_hint=box.path(),
    label="Box Width",
    parm_type="float",
    last_synced=box.parm("sizex").eval(),
))
sync.save_manifest(panel, m)
callbacks.build_interface(panel, m, {m.entries[0].id: {"default": 1.0, "min": 0.1, "max": 5.0}})

# 1. Drag "Box Width" on CONTROLS -> box.sizex should follow.
# 2. Set box.sizex directly in the node.
# 3. Then:
manifest, result = sync.refresh(panel, geo)
print(result.summary())          # expect: 1 pull
sync.apply_changes(panel, geo, manifest, result.of(*[result.changes[0].kind]))
```

## Verify on a real install

Claims worth confirming before building further on them:

- **Slider drag granularity.** Does the callback fire per-tick during a drag
  or once on release? If per-tick, heavy parms need debouncing.
- **Undo coherence.** `hou.undos.group()` should make one Ctrl+Z revert both
  the panel parm and the graph write. Test it; if the panel parm's own
  change lands outside the group, the grouping needs rethinking.
- **`allSubChildren(recurse_in_locked_nodes=False)`** — confirm the kwarg
  name on your build.
- **`hou.NodeError`** — confirm it is the right exception class to raise
  from a parm callback for a legible error.

## Not built yet

- **The scoring pass.** `diff.compute()` accepts `candidates` but nothing
  produces them. That is the heuristic ranking — `isAtDefault()`, keyframes,
  expression references, graph position, node type, hidden/disabled flags —
  and it is the next piece.
- **The panel UI.** A Python Panel with a Refresh button showing
  `DiffResult.badge_count`, a reviewable change list, and checkboxes.
- **Staleness detection.** `node.addEventCallback` to light up the badge
  without a manual scan.
- **Ramps and multiparms.** `build_parm_template()` raises on these rather
  than generating a control that silently does nothing. Scoring should
  exclude them until there is a real plan for binding them.
