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

The page also summarises the snapshot: transports and their setlists, and the
number of tracks, layers, cues and media references. A 148 MB archive takes
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

Example:

```sh
python3 d3_extract.py "exports/my_show.d3" --project my_show -o snapshots/my_show.json
```

The exit status is `0` on success and `1` if the archive can't be read. The
error names the resource and byte offset where parsing stopped.

### Comparing snapshots

Open https://macswg.github.io/d3_snapshot_diff/ and load two snapshots. Any mix
works: two archives, two plugin captures, or one of each.

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
- **`renderEnable` is inferred.** Every layer in the reference project was
  enabled, so the byte used for disabled layers hasn't been checked.
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

- `index.html`: the browser page.
- `d3extract.js`: the extractor in JavaScript, used by the page. It also loads in
  Node with `require('./d3extract.js')`.
- `d3_extract.py`: the command-line extractor. It and `d3extract.js` are ports of
  each other, so change both together.
- `.github/workflows/pages.yml`: deploys the page and stamps its version.
- `FORMAT.md`: notes on the `.d3` container and object format the extractor relies on.

Project archives (`*.d3`) and snapshots (`*.json`) are gitignored, because they
contain real show data.
