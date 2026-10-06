# d3_proj_analyzer

Pull a [susan_summary](https://github.com/macswg/d3plg_susan_summary) snapshot
straight out of a disguise Designer `.d3` project archive. You don't need
Designer running or the plugin installed.

The output is a schemaVersion 7 snapshot, the same JSON the plugin writes. It
loads in [d3_snapshot_diff](https://macswg.github.io/d3_snapshot_diff/) and
diffs cleanly against captures taken with the plugin.

There are two ways to run it. Both produce byte-identical JSON:

- **In the browser:** open **https://macswg.github.io/d3_proj_analyzer/** and drop
  a `.d3` on it. The archive is read in the tab and never uploaded.
- **From the command line:** `python3 d3_extract.py project.d3`.

Related: [d3_snapshot_diff](https://macswg.github.io/d3_snapshot_diff/) compares
snapshots, and the [susan_summary plugin](https://github.com/macswg/d3plg_susan_summary)
captures them from a running Designer.

## In the browser

1. Open https://macswg.github.io/d3_proj_analyzer/. `index.html` also works
   opened straight from a local checkout, with no server or build step.
2. Drop a `.d3` project onto it, or click to choose one.
3. Check the project name. The archive doesn't store one, so it defaults to the
   file name. Change it to the name Designer shows if you'll diff against plugin
   captures.
4. Click **Download snapshot JSON**, then load the file into
   [d3_snapshot_diff](https://macswg.github.io/d3_snapshot_diff/).
5. Optionally, click **Download keyframes JSON** for the show's layer animation
   (see [Keyframes](#keyframes)).

The page also summarises the snapshot: transports and their setlists, and the
number of tracks, layers, cues, media references, animated parameters,
keyframes and CDLs. A 148 MB archive takes
under a second in Chrome.

## From the command line

Requires Python 3 (tested on 3.13). Standard library only, nothing to install.

```sh
python3 d3_extract.py "path/to/project.d3"
```

The snapshot is written next to the archive as `<timestamp>_<project>.json`,
and a one-line summary is printed:

```
wrote path/to/2026-09-13_15-38-31_project.json: 6 transports, 72 tracks, 2270 layers
```

### Options

| Option | Default | Purpose |
| --- | --- | --- |
| `-o`, `--output PATH` | `<timestamp>_<project>.json` next to the archive | Where to write the snapshot. |
| `--project NAME` | the archive's file name, without `.d3` | Project name to record. The archive doesn't store one, so pass the name Designer shows if you'll diff against plugin captures. |
| `--captured-at ISO` | the archive's modification time | Timestamp to record, e.g. `2026-09-13T15:36:30-07:00`. |
| `--keyframes [PATH]` | off; with no path, `<snapshot name>_keyframes.json` | Also write the keyframes file (see [Keyframes](#keyframes)). |

Example:

```sh
python3 d3_extract.py "exports/my_show.d3" --project my_show -o snapshots/my_show.json
```

The exit status is `0` on success and `1` if the archive can't be read. The
error names the resource and byte offset where parsing stopped.

### Comparing snapshots

Open https://macswg.github.io/d3_snapshot_diff/ and load two snapshots. Any mix
works: two archives, two plugin captures, or one of each.

## Keyframes

The keyframes file is a separate JSON document (`"format": "d3_keyframes"`)
holding every **animated** layer parameter in the show: a parameter with two or
more keys, or one driven by an expression. A single key is just the parameter's
constant value, and a show holds tens of thousands of those. Most sit at their
default and are left out; one that differs from the default is a setting
someone made (a blend mode, a mapping, an end-point behaviour) and is kept,
marked `"static": true` -- 9,121 of them on the reference show, against some
144,000 left at their defaults. The comparison is on the stored values, so
rounding cannot make a default look changed. The exception is a layer's **CDL**: a grade is nearly always set with one
key, so a `cdl` field is exported whenever it applies a CDL (see
[CDLs](#cdls)). It covers every track in the show, not just the ones in a
setlist. The diff tool doesn't read this file.

```json
{
  "format": "d3_keyframes", "formatVersion": 4, "scope": "animated+set",
  "project": "my_show", "capturedAt": "2026-09-13T15:38:31-07:00",
  "trackCount": 64, "layerCount": 958, "fieldCount": 1794, "keyCount": 4984,
  "staticCount": 9121,
  "cdlCount": 1,
  "cdls": {
    "objects/cdl/300_lasers_yellow_jc": {
      "name": "300_lasers_yellow_jc", "source": "designer",
      "archivePath": "objects/cdl/300_lasers_yellow_jc.apx",
      "slope": [1.0, 0.95, 0.75], "power": [0.92, 1.0, 1.0],
      "offset": [0.0, 0.0, 0.0], "saturation": 1.0, "error": null
    }
  },
  "tracks": [{
    "id": "430_intro", "name": "430_intro", "path": "objects/track/430_intro.apx", "bpm": 60.0,
    "layers": [{
      "id": "#10313707525644097714", "uid": 10313707525644097714,
      "name": "[VID] intro_loop", "type": "VariableVideoModule",
      "groupPath": [], "tStart": 60.06, "tEnd": 94.628, "notchBlock": null,
      "fields": [{
        "name": "brightness", "label": null, "labelSource": null,
        "valueType": "float", "default": 1.0, "expression": null,
        "keys": [
          {"t": 61.060547, "value": 0.0, "interpolation": "linear"},
          {"t": 61.194336, "value": 0.998, "interpolation": "linear"}
        ]
      }]
    }]
  }]
}
```

- **Ids** match the snapshot: track `id` and layer `id`/`uid` are the same, so a
  keyframe can be joined to its layer.
- **`t`** is in track seconds, not relative to the layer. Keys can sit outside
  the layer's own start and end.
- **`value`** is a number for numeric parameters, rounded to the shortest decimal
  that is the same stored value (`0.998`, not `0.9980000257492065`). Clip and
  resource keys give the resource path, and string keys give the string.
  Vector parameters are split per component (`scale.x`, `scale.y`).
- **`expression`** holds the expression text when one drives the parameter
  (e.g. `1-wildcard10()`).
- **`interpolation`** is `linear`, `step` or `smooth`. **These names are
  inferred**, not confirmed in Designer: the stored codes are used as linear on
  nearly every numeric key and as step on whole-number, clip and string keys, and
  the third appears on only a few keys. Setting known interpolations on a few
  keys in a test project would confirm them.
- **Notch parameters** are named by attribute id
  (`Value::Attributes::<GUID>`). **`label`** gives the name exposed by the Notch
  block (`IMAG_FADE`, `CUE_TIME__A`, `COL_000 r`), as Designer last read it from
  that block. The names follow the block, so re-exporting after the block
  changes picks up renamed or new parameters. `labelSource` says where the name
  came from:
  - `"layer"`: stored on this layer. Notch layers store every exposed name.
  - `"show"`: borrowed. RenderStream layers carry the same attribute ids with no
    names, because their parameter list comes live from the render node. They
    take the name from another layer with that id, but only when every named
    occurrence agrees.
  - `null`: no name anywhere in the project. This happens for a RenderStream
    parameter no Notch layer shares.

  Built-in parameters (`brightness`, `scale.x`) have readable names already, so
  their `label` is usually null. **`notchBlock`** on a layer is the Notch block
  file it uses (e.g. `objects/notchfile/show_master.dfxdll`). The block itself
  isn't packed into a `.d3`, so its full parameter list isn't available, only
  the names the layers store.

### CDLs

A video layer's colour grade is its `cdl` field (`valueType` `CDL::RP`). Each
key's `value` is the CDL the layer switches to at `t`, or `null` for none, and
the grade holds until the next key (`step`). Every CDL a layer names is decoded
once into the top-level `cdls` table, keyed by that same value:

- **`source`** is `designer` for a CDL made in Designer (`objects/cdl/...`) or
  `ccFile` for an imported ASC `.cc` file (`objects/lutfile/<name>.cc`, packed
  as `internal/lutfile/<name>.cc.apx`; `archivePath` says which).
- **`slope`**, **`power`**, **`offset`** are RGB triplets; **`saturation`** is a
  number. The order they're stored in is **inferred** from the values, not
  documented. See [FORMAT.md](FORMAT.md#cdl).
- **`error`** is `"not in archive"` or `"unreadable CDL resource"` when the
  values couldn't be read, and the four values are then `null`.

In formatVersion 2, a `cdl` field was left out unless it had two or more keys,
which dropped nearly every graded layer.

FormatVersion 4 added the set values: every field carries `static`, and one-key
fields that differ from their default are exported with it true.
`fieldCount`, `keyCount`, `layerCount` and `trackCount` still count animation
only, so they read the same as in format 3; `staticCount` counts the settings.
A layer or track holding only settings is now in the file, which grew the
reference show's from 1.6 MB to 6.8 MB.

## What's in the snapshot

The same fields as a plugin capture:

- **Transports:** name, setlist, and the ordered track list for every transport,
  with the active transport first.
- **Tracks:** length, BPM, timecode, and every cue (section breaks, notes, TC,
  cue and MIDI tags).
- **Layers:** name, module type, group path, start and end times in seconds,
  beats and timecode, a stable `id` taken from the layer's UID, and the media each
  layer uses (clip path, enabled version, region set, audio).
- **Showfile census:** every track in the show, so a diff can tell a deleted
  track from one that was just dropped from a setlist.

### Frame rate

A track's timecode needs a frame rate, and the archive stores one only on a
transport's LTC input; a track has none of its own. So a track takes the rate
of the first transport, active one first, that plays it **and** has an LTC
input. A track no LTC transport plays falls back to the active transport's
rate, and when that has none either its `fps`, `tcStart`, `tcEnd` and cue
`timecode` are `null` and `debug` names it if it carries TC tags.

Earlier versions took the rate from whichever transport reached the track
first. On a show saved with an LTC-less transport active, that was the LTC-less
editor transport, which plays the same setlist as the show transports, and every
track came out with no timecode.

The `29.97` rate covers both 29.97 clock types. A drop-frame input is read with
non-drop-frame arithmetic, as the plugin does, so the two agree; whether
Designer shows drop-frame labels there is untested.

## What an archive can't provide

A `.d3` doesn't hold everything the plugin reads from a running Designer:

| Field | In a file-based snapshot |
| --- | --- |
| `system.options` (option switches) | `values: null` with an explanation. `options.bin` and `machine.bin` aren't packed into archives. The diff tool reports that switches couldn't be compared. |
| `system.build` | Version, revision, branch and build id come from the archive. Licence type, custom release name, RenderStream version and the release flags are `null`. |
| `project` | The archive's file name unless you pass `--project`. |
| `capturedAt` | When the archive was saved, unless you pass `--captured-at`. |

So when you diff a file-based snapshot against a plugin capture, expect a few
build fields to show as changed.

## Caveats

- **Tested on one project.** The format was reverse-engineered from a single
  Designer r34.0.3 project and checked against a plugin capture of it. All 72
  tracks and 2,270 layers matched. Other Designer versions may lay objects out
  differently. If the parser meets something it doesn't know, it stops with an
  error rather than guessing.
- **Layers carry `enabled` and `muted`, which the plugin does not write.**
  `enabled` is Designer's Disable, saved with the project, and false inside a
  disabled group too. `muted` is the director's mute at the moment of saving --
  mute is per machine and resets on a track change -- and is `null` when the
  director state cannot be read. Both were located on a two-layer test project,
  one change at a time. `renderEnable` is neither and is 1 on every layer seen
  so far.
- **Some orderings differ from the plugin.** Transports after the active one,
  and the showfile census, are sorted alphabetically. The diff tool matches both
  by name, so this doesn't show up as a change.

## Deploying the page

Every push to `main` deploys the page through `.github/workflows/pages.yml`. The
workflow stamps the footer with `v1.0.<commit count>` (the commit hash is in its
tooltip), so the version rises by one per commit without a bump. Only the
deployed copy is stamped: the committed `index.html` says `dev`, which is what a
local copy shows.

## Files

- `index.html`: the browser page (snapshot and keyframes downloads).
- `d3extract.js`: the extractor in JavaScript, used by the page. It also loads in
  Node with `require('./d3extract.js')`.
- `d3_extract.py`: the command-line extractor. It and `d3extract.js` are ports of
  each other, so change both together.
- `.github/workflows/pages.yml`: deploys the page and stamps its version.
- `FORMAT.md`: notes on the `.d3` container and object format the extractor relies on.

Project archives (`*.d3`) and snapshots (`*.json`) are gitignored, because they
contain real show data.
