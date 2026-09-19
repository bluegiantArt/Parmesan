# parmesan

Surface the most used parameters that matter from any Houdini graph into a single panel. 

## Model

**The graph is master.** Every value lives on the node parms, exactly where
it always did. The panel is a generated view over them:

| Direction | Mechanism | When |
|---|---|---|
| Panel -> graph | Python parm callback writes through | On edit, immediately |
| Graph -> panel | Refresh re-reads and diffs | On demand |

No channel references are ever written onto source parms. That is the design, and two things follow from it:

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
  scoring.py    the ranking heuristic  (pure Python)
  scan.py       walks the graph gathering evidence    (needs hou)
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
on the control node, so they render as an ordinary Houdini parameter interface
in the Parameters pane, and they keep working when the panel is closed.

1. Select a node in the network editor and press **Create New** — that makes a
   `CONTROLS` null to hang controls on. (**Use Selected** adopts an existing
   one instead.)
2. Press **Scan Graph and Build Panel**. That reads every node under the scan
   root, ranks every parameter in it, and surfaces the best ones — grouped into
   one collapsible folder per source node, so it is always obvious which node a
   knob came from. No node-by-node hunting; that is the point of the tool.
3. **Show Controls** makes the control node current so the sliders appear in
   the Parameters pane. Dragging one writes straight down into the graph.
4. Change something on a source node directly, then press **Refresh**. The
   count on the button is how many things need your attention. Rows say what
   happened in plain words; **Apply Checked** acts on them. Conflicts ask once
   which side wins rather than guessing.

**Scan from** limits the scan; blank means the network the control node lives
in, which is usually what you want. **How many** sets the panel size.
**Review first** shows the ranking before anything is added — off by default,
because one press should give a working panel.

Every surfaced control records *why* it was chosen, shown in the panel's list.
If the ranking picks badly, that column is the diagnostic: it says which signal
misfired. **Add Manually** is the fallback when the scan misses something, and
removing a control never touches the graph — the value stays exactly where it
was, which is the point of the whole design.

## How the ranking works

`scoring.py` is pure Python and every weight in it is a named constant meant to
be argued with. The premise is that **a parameter that has been touched is a
parameter that matters**, so `isAtDefault()` carries the most weight and
everything else corroborates:

| Signal | Why it counts |
|---|---|
| Edited (not at default) | The artist changed it. The primary evidence. |
| Animated | Changed over time, not just once. |
| Referenced by other parms | Literally the most-used knobs in the graph. |
| Node renamed from its default | Renaming is an act of authorship. |
| On the display node | What the artist is currently looking at. |
| Knob-like name | `height`, `scale`, `seed` — art direction, not plumbing. |
| Look-defining node type | `mountain`, `scatter`, `polyextrude`. |
| Locked / greyed out | Negative: a control that cannot do anything. |
| Plumbing name or type | Negative: `vexpression`, `group`, `merge`, `file`. |

The fan-out cap sits deliberately below the edited weight, so no amount of
corroboration outranks direct evidence. An unknown node type scores zero rather
than being penalised, so a graph of custom HDAs falls back to evidence instead
of being punished for being unrecognised. And no single node may take more than
`DEFAULT_MAX_PER_NODE` slots, so one over-tweaked node cannot fill the panel.

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
  name on your build. `scan.walk()` falls back to the positional form.
- **`parm.parmsReferencingThis()`** — the fan-out signal depends on it, and on
  it being fast enough to call for every promising parm in a large graph.
- **Scan speed.** If a scan of a production graph is slow, the two-tier
  gathering in `scan._scan_node()` is the thing to tighten.
- **`hou.NodeError`** — confirm it is the right exception class to raise
  from a parm callback for a legible error.

## Not built yet

- **Graph position as a signal.** Depth in the chain and downstream fan-out
  would both sharpen the ranking, but both cost a walk per parm; the current
  scan deliberately stops at signals it can read cheaply.
- **Tuning against real scenes.** Every weight in `scoring.py` was reasoned
  about, not measured. They need a production graph and an artist disagreeing
  with the results.
- **Staleness detection.** `node.addEventCallback` to light up the badge
  without a manual scan.
- **Ramps and multiparms.** `build_parm_template()` raises on these rather
  than generating a control that silently does nothing. Scoring should
  exclude them until there is a real plan for binding them.
