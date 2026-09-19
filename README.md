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
  diff.py       reconciliation engine + UI wording   (pure Python)
  callbacks.py  parm templates + generated callback   (needs hou)
  sync.py       observe / write-through / apply       (needs hou)
  promote.py    add + remove + rename controls        (needs hou)
  panel.py      the Python Panel                     (needs hou + Qt)
python_panels/
  parmesan.pypanel   panel registration, path injected at install
install.py      copies the panel into Houdini's prefs
tests/          51 tests, no Houdini required
```

Observation and decision are split on purpose: `sync.observe()` does the
hou-dependent reading and hands plain dicts to `diff.compute()`, so the
logic most likely to contain bugs is testable off-host.

## Install

In Houdini, open **Windows > Python Source Editor** and run:

```python
import sys; sys.path.insert(0, r"/path/to/Parmesan")   # this repo
import install; install.install()
```

That copies the panel into your Houdini preferences with this repo's path
baked in, and adds the repo to `PYTHONPATH` in `houdini.env` (backing the file
up first, and skipping it if already present). Restart Houdini, then add the
panel: any pane's tab menu > **New Pane Tab Type > Python Panel**, then pick
**Parmesan** from the panel's own menu.

`install.install(update_env=False)` skips the `houdini.env` edit and prints
the line to add yourself. `install.uninstall()` removes the panel file.

The `PYTHONPATH` entry matters beyond the panel: the generated callbacks do
`from parmesan import sync`, so without it a panel edit after a fresh Houdini
start raises a `hou.NodeError` naming the problem rather than failing silently.

## Using the panel

The panel is the machinery; **the sliders are not in it.** They are spare parms
on the control node, so they live in Houdini's own parameter editor where you
already work, and they keep working when the panel is closed.

1. Select a node in the network editor and press **Create New** — that makes a
   `CONTROLS` null to hang controls on. (**Use Selected** adopts an existing
   one instead.)
2. Select the node whose parameters you want and press **Add Parameters**.
   Parameters you have edited sort to the top and are shown in bold, since a
   value that differs from its default is the strongest hint it matters.
3. **Show Controls** makes the control node current so its sliders appear in
   the Parameters pane. Dragging one writes straight down into the graph.
4. Change something on the source node directly, then press **Refresh**. The
   count on the button is how many things need your attention. Rows say what
   happened in plain words; **Apply Checked** acts on them. Conflicts ask once
   which side wins rather than guessing.

Removing a control never touches the graph — the value stays exactly where it
was, which is the point of the whole design.

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

The Qt half of the panel is smoke-tested headless (PySide6, no Houdini): the
widget builds, every change kind renders, and the installer's generated
`.pypanel` is checked as both valid XML and valid Python. Everything that
touches `hou` is still unrun. Claims worth confirming before building further
on them:

- **The panel appears at all.** If **Parmesan** is missing from the Python
  Panel menu, the `.pypanel` file did not land or did not parse.
- **PySide version.** Houdini 20.5+ ships PySide6, earlier ships PySide2;
  `panel.py` tries both and writes enums in the long form PySide6 requires.
- **`hou.ui.displayMessage`** with three buttons, used for conflict
  resolution — confirm the returned index is the button position.

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
- **Staleness detection.** `node.addEventCallback` to light up the badge
  without a manual scan.
- **Ramps and multiparms.** `build_parm_template()` raises on these rather
  than generating a control that silently does nothing. Scoring should
  exclude them until there is a real plan for binding them.
