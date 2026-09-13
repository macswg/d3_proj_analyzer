# The `.d3` project archive, as far as `d3_extract.py` needs it

Reverse-engineered from a production show archive (Designer r34.0.3,
rev 1016). The extractor's output matched the susan_summary plugin capture of
the same project (all 72 tracks, 2,270 layers, cues, media and timecodes), and
the parser reads every track, cue, video asset and setlist in the archive with
no failures.

All integers are little-endian.

## Container

```
"r\x19\x04\x07blip" u32(3)              12-byte file header
repeat:
  "****" u32 recordLen u32 payloadLen
  8 bytes reserved, 8 bytes timestamp
  u32 nameLen, name                      e.g. objects/track/110_intro.apx
  payload (payloadLen bytes), then padding up to recordLen
```

Uncompressed. One record per resource file of the project folder (`objects/`,
`internal/`, `trash/`, `conf/`). **Not included:** `internal/options/options.bin`
and `{d3 Projects}/machine.bin`, which is why `system.options` can't be recovered.
Resource paths are case-insensitive: setlists may reference a different case
than the stored name.

## Objects (`.apx` payloads)

```
"r\x19\x04\x07" u32(1)
"<Class>_UID_<16 hex>\0"
"Resource\0" u32(9)  i64 u32 u8 8-byte-timestamp  u32 n  n x (u32, cstr tag)
"<BaseClass>\0" u32 version <fields> ... "<Class>\0" u32 version <fields>
```

The `<16 hex>` is the resource UID. Read as an unsigned 64-bit int it is the
`uid` Designer's Python API reports. This was checked against live-director UIDs
in the plugin's tests (7429913502632686800 = `0x671c5af71c4cb4d0`).
Schema v7 layer ids are `#<uid>`.

Fields have no names or lengths, so a reader has to know each class's layout.
References to other resources are stored as their path (`objects/...apx`), or
`null`. Objects embedded in another object (layers, sequences) have the same
shape, minus the file preamble.

### Track (`objects/track/*.apx`)

```
SuperTrack v3: u32 nLayers, layers...
               u32 u32, u32 nArrows + Arrow objects
               f32 bpm, u32, cstr, u32 u32, f64 lengthInSec, f64, f64, f32, u32, cstr
Track v64:     17 bytes, cstr, 8 bytes, u32, f64, cstr, 9 bytes
               u32 nCues, nCues x (f64 beat, cstr "internal/cue/uid_<hex>.apx")
```

Name = file stem. `lengthInBeats` = `lengthInSec * bpm / 60`.

### Layers (inside a track)

```
SuperLayer v9: cstr name, f64 tStart, f64 duration, 8 bytes, u8 renderEnable, u8, u8
GroupLayer v2: u32 nChildren, child layers..., u32 nArrows + Arrow objects
Layer v15:     u32, u32, u32 nBases, nBases x (cstr, u32)   e.g. ColourShift, ProjectionAwareModule
               cstr moduleType                             e.g. VariableVideoModule
               `null` or a <Module>Config object           (skipped by resyncing on the next field)
               u32 nFields, FieldSequence objects
               u8, `null` or DmxPatch object, u32 n + n x u32, u32 n + n x u32, u32
```

`renderEnable` is inferred: every layer in the reference archive is enabled, so
a disabled layer has never been checked against this byte.

### FieldSequence / keyframes

```
FieldSequence v17: cstr fieldName, cstr valueType, <Float|Resource|String>Sequence object
  KeyContainer v1, KeySequence v6 u32, <X>Sequence v: u32 nKeys, keys...
    Float:    u32 u32 f64 t, 3 bytes, f32 value
    Resource: cstr keyClass, u32 u32 f64 t, 3 bytes, cstr path
    String:   u32 u32 f64 t, 3 bytes, cstr value
  cstr owningModule, (`null` | Expression object), (`null` | Expression object),
  u8, default value (f32 or cstr), cstr uiCategory
```

A layer's media is the `video` field's ResourceSequence keys.

### Cue (`internal/cue/uid_*.apx`)

```
Cue v3: cstr note, u32 nTags, nTags x (u32 type, u32, cstr text), u8 isSection, ...
```

Tag types: 0 = timecode, 1 = cue, 2 = MIDI.

### Media

- `objects/videoclip/...mov.apx` (VideoClip v50) references `objects/videoasset/...apx`.
- VideoAsset v5: `u32 frames, f32 fps, f32 w, f32 h, u8 hasAudio, u8, cstr codec, ...`,
  then VideoFragment objects (`u32 u32 cstr version, cstr region, u8 usable, ...`),
  newest version first, ending with the `objects/videoregionset/.../<regionSet>.apx` path.
- Enabled version = the newest version whose fragments have `usable` = 1. This matched
  Designer's `enabledVersion` on all 955 clips in the reference show.

### Transports, setlists and state

- `objects/transportmanager/*.apx`: only the files whose class is `TransportManager`
  count as transports (a stale transport file can be a bare `Resource`). The strings inside
  name the LTC timecode transport and the `objects/usersetlist/*.apx` setlist.
- UserSetList: `SetList v1, UserSetList v1, u32 n, n x cstr trackPath`.
- `objects/setlist/automatic.apx` is empty on disk: the automatic setlist is every
  `objects/track/*.apx`.
- TimecodeTransportLtc v2: `cstr audioLine, u32 smpteClockType` → frame rate.
- Active transport: the last `objects/transportmanager/` path in
  `internal/localstate/_directorstate_.apx`.
- `conf/depends.txt`: `d3 <versionName> <revision> <buildId> <date>`.
