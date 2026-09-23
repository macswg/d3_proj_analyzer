#!/usr/bin/env python3
"""Build a susan_summary snapshot straight from a disguise Designer `.d3` project
archive -- no running Designer, no plugin.

    python3 d3_extract.py "path/to/project.d3" [-o out.json] [--project NAME]

The output has the same shape as the susan_summary plugin's schemaVersion 7 log
(see ../d3plg_susan_summary/snapshot.py), so it loads in
https://macswg.github.io/d3_snapshot_diff/ and diffs against plugin captures.

Python 3, stdlib only. See FORMAT.md for the reverse-engineered file format this relies on.

What the archive cannot supply, and what is done instead:
  - system.options: the option switches live in internal/options/options.bin and
    {d3 Projects}/machine.bin, neither of which is packed into a .d3. Recorded as
    values=null with an error, the plugin's own "we do not know" convention.
  - system.build: conf/depends.txt names the version, revision and build id of
    the Designer that saved the archive. Fields it doesn't carry are null.
  - project: the archive doesn't record the project name. Defaults to the file
    stem; pass --project to match the name Designer reports.
  - capturedAt: the archive's modification time (the moment its state was saved).
"""
import argparse
import datetime
import json
import mmap
import os
import re
import struct
import sys
from decimal import Context, Decimal, ROUND_HALF_UP

SCHEMA_VERSION = 7
AUTOMATIC_SETLIST_PATH = "objects/setlist/automatic.apx"
TRACK_ROOT = "objects/track"
DIRECTOR_STATE = "internal/localstate/_directorstate_.apx"

# Timecode.SMPTE* clock types -> frame rate, as stored on TimecodeTransportLtc.
# float32 on purpose: the plugin reports Timecode.fps(), a float, so 29.97 comes
# back as 29.969999313354492.
_FPS_BY_CLOCK = {0: 23.976, 1: 24.0, 2: 25.0, 3: 29.97, 4: 29.97, 5: 30.0}
_TAG_NAMES = {0: "tc", 1: "cue", 2: "midi"}

KEYFRAMES_FORMAT_VERSION = 2
# Keyframe interpolation codes. Inferred, not confirmed in Designer: 2 is on
# nearly every float key, 0 on nearly every whole-number, clip and string key
# (which can only hold, not blend), and 1 on a handful of brightness and Notch
# time keys. The plugin's director probe found keys exposing linear, cubic and
# select, which fits linear / smooth / step.
INTERPOLATION = {0: "step", 1: "smooth", 2: "linear"}
TAG_TC = 0


def _f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _num(value):
    """round(float, 6) with Python 2 semantics (half away from zero), since the
    plugin runs on the director's Python 2 and the logs must compare equal.
    Python 3's round() goes half-to-even and splits e.g. 1476.0078125."""
    if value is None:
        return None
    q = Decimal(value).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return float(q)


# --- container ----------------------------------------------------------------

class Archive(object):
    """A .d3 file: 12-byte header, then `****` records of
    (u32 total, u32 payloadLen, 8 reserved, 8 timestamp, u32 nameLen, name, payload).

    A record starting `----` is a dead copy: Designer saves a change by appending
    a new copy of the resource and marking the old one dead in place, so both are
    in the file. Dead records are stepped over like live ones and never indexed;
    `dead` counts them."""

    MAGIC = b"r\x19\x04\x07blip"

    def __init__(self, path):
        self.path = path
        self._fh = open(path, "rb")
        self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        if self._mm[:8] != self.MAGIC:
            raise ValueError("not a d3 project archive: {0}".format(path))
        self.entries = {}
        self.dead = 0
        off, size = 12, len(self._mm)
        while off < size:
            tag = self._mm[off:off + 4]
            if tag not in (b"****", b"----"):
                raise ValueError("archive record out of sync at 0x{0:x}".format(off))
            total, plen = struct.unpack_from("<II", self._mm, off + 4)
            nlen = struct.unpack_from("<I", self._mm, off + 28)[0]
            if off + total > size or 32 + nlen + plen > total:
                raise ValueError("archive truncated or corrupt at 0x{0:x} "
                                 "(incomplete download or copy?)".format(off))
            if tag == b"----":
                self.dead += 1
                off += total
                continue
            name = self._mm[off + 32:off + 32 + nlen].decode("utf-8", "replace")
            self.entries[name] = (off + 32 + nlen, plen)
            off += total
        # Designer resource paths are case-insensitive (it runs on Windows): a
        # setlist can say 200_song_ABC.apx for the file stored as
        # 200_song_abc.apx.
        self._folded = dict((n.lower(), n) for n in self.entries)

    def resolve(self, name):
        """The archive's own spelling of a resource path, or None."""
        if name in self.entries:
            return name
        return self._folded.get(name.replace("\\", "/").lower())

    def has(self, name):
        return self.resolve(name) is not None

    def read(self, name):
        start, length = self.entries[self.resolve(name)]
        return bytes(self._mm[start:start + length])

    def names(self, prefix):
        return [n for n in self.entries if n.startswith(prefix)]


# --- object serialisation -----------------------------------------------------

class ParseError(Exception):
    pass


class Reader(object):
    """Cursor over one serialised object. Objects are chains of class sections:
    `Cls_UID_<hex>\\0`, then `Resource\\0 u32 version <fields>`, then each
    subclass as `Name\\0 u32 version <fields>`. Nothing is length-prefixed, so a
    reader has to know every field it passes."""

    def __init__(self, data, name=""):
        self.b = data
        self.i = 0
        self.name = name

    def u8(self):
        v = self.b[self.i]
        self.i += 1
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.b, self.i)[0]
        self.i += 4
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.b, self.i)[0]
        self.i += 4
        return v

    def f64(self):
        v = struct.unpack_from("<d", self.b, self.i)[0]
        self.i += 8
        return v

    def skip(self, n):
        self.i += n

    def cstr(self):
        j = self.b.index(b"\0", self.i)
        s = self.b[self.i:j].decode("utf-8", "replace")
        self.i = j + 1
        return s

    def peek_cstr(self):
        j = self.b.find(b"\0", self.i)
        if j < 0:
            return ""
        return self.b[self.i:j].decode("utf-8", "replace")

    def at_end(self):
        return self.i >= len(self.b)

    def fail(self, msg):
        raise ParseError("{0} @0x{1:x}: {2} (next bytes {3!r})".format(
            self.name, self.i, msg, self.b[self.i:self.i + 48]))

    def expect(self, text):
        got = self.cstr()
        if got != text:
            self.fail("expected {0!r}, got {1!r}".format(text, got))

    def section(self, name):
        self.expect(name)
        return self.u32()

    def object_head(self, cls=None):
        """`Cls_UID_hex` + the Resource section. Returns (class, uid hex)."""
        head = self.cstr()
        if "_UID_" not in head:
            self.fail("expected an object, got {0!r}".format(head))
        got_cls, uid = head.split("_UID_", 1)
        if cls is not None and got_cls != cls:
            self.fail("expected {0}, got {1}".format(cls, got_cls))
        version = self.section("Resource")
        if version != 9:
            self.fail("unsupported Resource version {0}".format(version))
        # i64, u32, u8, 8-byte timestamp, then the resource's tag/folder list.
        self.skip(8 + 4 + 1 + 8)
        for _ in range(self.u32()):
            self.skip(4)
            self.cstr()
        return got_cls, uid

    def open_object(self):
        """Skip the per-file `r\\x19\\x04\\x07 u32` preamble."""
        if self.b[:4] != b"r\x19\x04\x07":
            self.fail("missing object magic")
        self.skip(8)


def _stem(path):
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    return base[:-4] if base.endswith(".apx") else base


# --- tracks -------------------------------------------------------------------

def _read_arrows(r):
    for _ in range(r.u32()):
        r.object_head("Arrow")
        r.section("Arrow")
        r.skip(16)  # two linked uids


def _read_optional_object(r):
    """Slots after a sequence's keys: `null` or an Expression object."""
    if r.peek_cstr() == "null":
        r.cstr()
        return None
    cls, _ = r.object_head()
    if cls != "Expression":
        r.fail("unexpected object {0} in sequence".format(cls))
    r.section("Expression")
    expression = r.cstr()
    r.skip(4 + 1)
    return expression


def _read_sequence(r):
    """A keyframe track. Keys are (time in track seconds, value, interpolation
    code); see INTERPOLATION for what the codes appear to mean."""
    cls, _ = r.object_head()
    r.section("KeyContainer")
    r.section("KeySequence")
    r.skip(4)
    r.section(cls)
    keys = []
    count = r.u32()
    for _ in range(count):
        if cls == "ResourceSequence":
            r.cstr()          # key class: Key / Sequence
        elif cls not in ("FloatSequence", "StringSequence"):
            r.fail("unsupported sequence {0}".format(cls))
        r.skip(4 + 4)
        t = r.f64()
        interp = r.u8()
        r.skip(2)
        if cls == "FloatSequence":
            keys.append((t, r.f32(), interp))
        else:
            keys.append((t, r.cstr(), interp))
    r.cstr()                  # owning module name
    expression = _read_optional_object(r)
    _read_optional_object(r)
    r.skip(1)
    if cls == "FloatSequence":
        default = r.f32()
    else:
        default = r.cstr()
    # Display label. Empty on most built-in parameters; on a Notch layer it is
    # the exposed parameter's name as Designer last read it from the block
    # (IMAG_FADE, CUE_TIME__A, "COL_000 r"), since the field name itself is only
    # the block's attribute id.
    label = r.cstr()
    return {"cls": cls, "keys": keys, "expression": expression, "default": default,
            "label": label}


def _read_field_sequence(r):
    r.object_head("FieldSequence")
    r.section("FieldSequence")
    name = r.cstr()
    value_type = r.cstr()     # e.g. float / VideoClip::RP
    field = _read_sequence(r)
    field["name"] = name
    field["valueType"] = value_type
    return field


def _skip_module_config(r):
    """`null`, or a per-module *ModuleConfig resource. Those vary freely between
    module types and nothing in the snapshot needs them, so resync on the field
    sequence list that always follows: `u32 count` + `FieldSequence_UID_`.
    Returns the resource paths the config names (a Notch layer's block file),
    read as plain strings rather than by layout for the same reason."""
    if r.peek_cstr() == "null":
        r.cstr()
        return []
    j = r.b.find(b"FieldSequence_UID_", r.i)
    if j < 0:
        r.fail("no field sequences after module config")
    paths = [s for s in _cstrings(r.b[r.i:j - 4]) if s.startswith("objects/")]
    r.i = j - 4
    return paths


def _read_layer(r, group_path, out):
    cls, uid = r.object_head()
    r.section("SuperLayer")
    name = r.cstr()
    t_start = r.f64()
    duration = r.f64()
    r.skip(8)
    render_enable = bool(r.u8())
    r.skip(2)

    if cls == "GroupLayer":
        r.section("GroupLayer")
        for _ in range(r.u32()):
            _read_layer(r, group_path + [name], out)
        _read_arrows(r)
        return
    if cls != "Layer":
        r.fail("unsupported layer class {0}".format(cls))

    r.section("Layer")
    r.skip(4 + 4)
    for _ in range(r.u32()):  # module base-class sections (ColourShift, ...)
        r.cstr()
        r.skip(4)
    module = r.cstr()
    config_paths = _skip_module_config(r)
    fields = [_read_field_sequence(r) for _ in range(r.u32())]
    r.skip(1)
    if r.peek_cstr() == "null":
        r.cstr()
    else:                     # DMX control patch
        r.object_head("DmxPatch")
        r.section("ControlPatch")
        r.cstr()
        r.section("DmxPatch")
        r.skip(20)
    for _ in range(2):
        r.skip(4 * r.u32())
    r.skip(4)

    out.append({
        "name": name,
        "uid": uid,
        "type": module or "Layer",
        "groupPath": list(group_path),
        "renderEnable": render_enable,
        "tStart": t_start,
        "tEnd": t_start + duration,
        "fields": fields,
        "notchBlock": next((p for p in config_paths if p.startswith("objects/notchfile/")), None),
    })


def parse_track(data, path):
    r = Reader(data, path)
    r.open_object()
    r.object_head("Track")
    r.section("SuperTrack")
    layers = []
    for _ in range(r.u32()):
        _read_layer(r, [], layers)
    r.skip(8)
    _read_arrows(r)
    bpm = r.f32()
    r.skip(4)
    r.cstr()
    r.skip(8)
    length_sec = r.f64()
    r.f64()
    r.f64()
    r.skip(4 + 4)
    r.cstr()
    r.section("Track")
    r.skip(17)
    r.cstr()
    r.skip(8 + 4 + 8)
    r.cstr()
    r.skip(9)
    cues = [(r.f64(), r.cstr()) for _ in range(r.u32())]
    if not r.at_end():
        r.fail("{0} unread bytes at end of track".format(len(r.b) - r.i))
    return {"layers": layers, "bpm": float(bpm), "lengthInSec": length_sec, "cues": cues}


def parse_cue(data, path):
    r = Reader(data, path)
    r.open_object()
    r.object_head("Cue")
    r.section("Cue")
    note = r.cstr()
    tags = {}
    for _ in range(r.u32()):
        tag_type = r.u32()
        r.skip(4)
        text = r.cstr()
        tags.setdefault(tag_type, text)
    section = bool(r.u8())
    return {"note": note, "tags": tags, "section": section}


# --- media --------------------------------------------------------------------

class MediaResolver(object):
    def __init__(self, archive, debug):
        self.archive = archive
        self.debug = debug
        self.cache = {}

    def record(self, ref):
        if ref not in self.cache:
            self.cache[ref] = self._record(ref)
        return dict(self.cache[ref])

    def _record(self, ref):
        path = ref[:-4] if ref.endswith(".apx") else ref
        rec = {"name": _stem(ref), "path": path, "version": None,
               "hasAudio": False, "regionSet": None}
        if not ref.startswith("objects/videoclip/"):
            return rec
        if not self.archive.has(ref):
            self.debug.append("media resource missing from archive: " + ref)
            return rec
        asset = next((s for s in _cstrings(self.archive.read(ref))
                      if s.startswith("objects/videoasset/")), None)
        if asset is None or not self.archive.has(asset):
            self.debug.append("no video asset for " + ref)
            return rec
        try:
            rec.update(parse_video_asset(self.archive.read(asset), asset))
        except (ParseError, IndexError, ValueError, struct.error) as error:
            self.debug.append("video asset unreadable: {0}".format(error))
        return rec


def _cstrings(data):
    return [m.group(1).decode("utf-8", "replace")
            for m in re.finditer(rb"([\x20-\x7e]{4,})\0", data)]


def parse_video_asset(data, path):
    """VideoAsset: frame count, fps, size, hasAudio, codec, then VideoFragments
    (one per version x region), newest version first, then the region set.
    The enabled version is the newest one whose fragments carry the usable flag
    -- matched Designer's enabledVersion on all 955 clips of the reference show."""
    r = Reader(data, path)
    r.open_object()
    r.object_head("VideoAsset")
    r.section("VideoAsset")
    r.skip(4 + 4 + 4 + 4)
    has_audio = bool(r.u8())
    version = None
    region_set = None
    for match in re.finditer(rb"VideoFragment\0\x04\0\0\0.{8}([^\0]*)\0[^\0]*\0(.)",
                             data[r.i:], re.S):
        if version is None and match.group(2) == b"\x01":
            version = match.group(1).decode("utf-8", "replace")
    for s in _cstrings(data):
        if s.startswith("objects/videoregionset/"):
            region_set = _stem(s)
    return {"version": version, "hasAudio": has_audio, "regionSet": region_set}


# --- snapshot assembly --------------------------------------------------------

def _slug(text):
    return "".join(c.lower() if c.isalnum() else "_" for c in text)


def _track_id(name, path):
    """Same as snapshot._track_id: a pure function of the track's name and path."""
    if not path:
        return name
    parts = path.split("/")
    stem = _stem(parts[-1])
    folder = "/".join(parts[:-1])
    bits = []
    if folder != TRACK_ROOT:
        prefix = folder
        if prefix.endswith(TRACK_ROOT):
            prefix = prefix[:-len(TRACK_ROOT)].strip("/")
        if prefix:
            bits.append(prefix)
    if _slug(stem) != _slug(name):
        bits.append(stem)
    return "{0} #{1}".format(name, "/".join(bits)) if bits else name


def _tc_seconds(text, fps):
    """HH:MM:SS:FF tag text -> seconds on the fps clock (frames / fps)."""
    parts = re.split(r"[:;.]", text.strip())
    if len(parts) != 4:
        return None
    h, m, s, f = (int(p) for p in parts)
    nominal = int(round(fps))
    return (((h * 60 + m) * 60 + s) * nominal + f) / fps


def _format_timecode(seconds, fps):
    if seconds is None or not fps:
        return None
    sign = "-" if seconds < 0 else ""
    total = abs(float(seconds))
    whole = int(total)
    frames = int(_round_half_away(float(total - whole) * fps))
    if frames >= int(_round_half_away(fps)):
        frames = 0
        whole += 1
    return "{0}{1:02d}:{2:02d}:{3:02d}.{4:02d}".format(
        sign, whole // 3600, (whole % 3600) // 60, whole % 60, frames)


def _round_half_away(x):
    return float(Decimal(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _uid_int(uid_hex):
    """A layer's UID as the director reports it: the `_UID_<hex>` of its object
    header read as an unsigned 64-bit int. Confirmed against the plugin's test
    fixtures, which hold live-director uids for a track (7429913502632686800)
    and its trashed twin (17535762199310169262) -- exactly 0x671c5af71c4cb4d0
    and 0xf35b8acfd39af4ae, the hex UIDs of those tracks in the reference archive."""
    try:
        return int(uid_hex, 16)
    except (TypeError, ValueError):
        return None


def _derived_layer_id(record):
    """Same as snapshot._derived_layer_id: `group/name @tStart-tEnd`, 2 decimals."""
    def fixed(value):
        return "?" if value is None else "{0:.2f}".format(float(value))
    path = "/".join(list(record.get("groupPath") or []) + [record.get("name") or "Unknown"])
    return "{0} @{1}-{2}".format(path, fixed(record.get("tStart")), fixed(record.get("tEnd")))


def _assign_layer_ids(records, debug):
    """Same as snapshot._assign_layer_ids: `#<uid>` (idSource uid), else a derived
    id, with a `~<n>` suffix for repeats and a debug line for a repeated uid."""
    counts = {}
    for record in records:
        uid = record.get("uid")
        if uid is not None:
            base, source = "#{0}".format(uid), "uid"
        else:
            base, source = _derived_layer_id(record), "derived"
        counts[base] = counts.get(base, 0) + 1
        seen = counts[base]
        record["id"] = base if seen == 1 else "{0}~{1}".format(base, seen)
        record["idSource"] = source
    for base in sorted(counts):
        if counts[base] > 1 and base.startswith("#"):
            debug.append(
                "layer uid {0} captured {1} times on one track -- same resource "
                "reached twice, not {1} layers".format(base[1:], counts[base]))
    return records


class TrackBuilder(object):
    def __init__(self, archive, debug):
        self.archive = archive
        self.debug = debug
        self.media = MediaResolver(archive, debug)
        self.records = {}
        self.by_path = {}

    def id_for(self, path):
        return self.by_path.get(path) or _track_id(_stem(path), path)

    def add(self, path, fps):
        path = self.archive.resolve(path) or path
        if path in self.by_path:
            return self.by_path[path]
        track_id = _track_id(_stem(path), path)
        self.by_path[path] = track_id
        record = self._record(path, fps)
        record["id"] = track_id
        self.records[track_id] = record
        return track_id

    def sorted_records(self):
        return [self.records[k] for k in sorted(self.records)]

    def _record(self, path, fps):
        name = _stem(path)
        base = {"name": name, "path": path, "trashed": "trash" in path.split("/")}
        if not self.archive.has(path):
            self.debug.append("track missing from archive: " + path)
            base.update({"lengthInSec": None, "lengthInBeats": None, "bpm": None,
                         "hasTimecode": False, "fps": None, "firstTimecodeBeat": None,
                         "cues": [], "layerCount": 0, "layers": [],
                         "error": "track missing from archive"})
            return base
        track = parse_track(self.archive.read(path), path)
        spb = 60.0 / track["bpm"] if track["bpm"] else 1.0

        def to_time(beat):
            return beat * spb

        def to_beat(t):
            return t / spb

        cues = []
        for beat, cue_path in track["cues"]:
            if not self.archive.has(cue_path):
                self.debug.append("cue missing from archive: " + cue_path)
                continue
            cue = parse_cue(self.archive.read(cue_path), cue_path)
            cue["beat"] = beat
            cues.append(cue)
        cues.sort(key=lambda c: c["beat"])

        tc_tags = [(c["beat"], c["tags"][TAG_TC]) for c in cues if c["tags"].get(TAG_TC)]
        has_tc = bool(tc_tags) and fps is not None
        first_tc = min(b for b, _ in tc_tags) if has_tc else None

        def timecode(beat):
            if not has_tc or beat is None or beat < first_tc:
                return None
            tag_beat, text = max((tb, tx) for tb, tx in tc_tags if tb <= beat)
            base_sec = _tc_seconds(text, fps)
            if base_sec is None:
                return None
            return _format_timecode(base_sec + to_time(beat) - to_time(tag_beat), fps)

        section_beats = [c["beat"] for c in cues if c["section"]]

        def section_of(beat):
            return max(sum(1 for b in section_beats if b <= beat) - 1, 0)

        cue_records = []
        for cue in cues:
            tags = [{"type": _TAG_NAMES[t], "text": cue["tags"][t]}
                    for t in sorted(_TAG_NAMES) if cue["tags"].get(t)]
            if not (cue["section"] or cue["note"] or tags):
                continue
            cue_records.append({
                "beat": _num(cue["beat"]),
                "isSection": cue["section"],
                "note": cue["note"] or None,
                "tags": tags,
                "section": section_of(cue["beat"]),
                "t": _num(to_time(cue["beat"])),
                "timecode": timecode(cue["beat"]),
            })

        layers = []
        for layer in track["layers"]:
            b_start = _num(to_beat(layer["tStart"]))
            b_end = _num(to_beat(layer["tEnd"]))
            layers.append({
                "name": layer["name"],
                "uid": _uid_int(layer["uid"]),
                "type": layer["type"],
                "groupPath": layer["groupPath"],
                "renderEnable": layer["renderEnable"],
                "tStart": _num(layer["tStart"]),
                "tEnd": _num(layer["tEnd"]),
                "bStart": b_start,
                "bEnd": b_end,
                "tcStart": timecode(b_start),
                "tcEnd": timecode(b_end),
                "media": self._layer_media(layer),
            })

        _assign_layer_ids(layers, self.debug)

        base.update({
            "lengthInSec": _num(track["lengthInSec"]),
            "lengthInBeats": _num(to_beat(track["lengthInSec"])),
            "bpm": _num(track["bpm"]),
            "hasTimecode": has_tc,
            "fps": fps if has_tc else None,
            "firstTimecodeBeat": _num(first_tc) if has_tc else None,
            "cues": cue_records,
            "layerCount": len(layers),
            "layers": layers,
        })
        return base

    def _layer_media(self, layer):
        # The last `video` field, as when fields were keyed by name.
        videos = [f for f in layer["fields"] if f["name"] == "video"]
        field = videos[-1] if videos else None
        if not field or field["cls"] != "ResourceSequence":
            return []
        out, seen = [], set()
        for _, ref, _ in field["keys"]:
            if not ref or ref == "null" or ref in seen:
                continue
            seen.add(ref)
            out.append(self.media.record(ref))
        return out


def _paths_in(data, prefix):
    return [s for s in _cstrings(data) if s.startswith(prefix)]


def _transport_fps(archive, tm_data, debug):
    """Frame rate from the transport's LTC timecode transport (SMPTE clock type)."""
    for ltc in _paths_in(tm_data, "objects/timecodetransport"):
        if not archive.has(ltc):
            continue
        data = archive.read(ltc)
        at = data.find(b"TimecodeTransportLtc\0")
        if at < 0:
            continue
        r = Reader(data, ltc)
        r.i = at
        r.section("TimecodeTransportLtc")
        r.cstr()
        clock = r.u32()
        if clock in _FPS_BY_CLOCK:
            return _f32(_FPS_BY_CLOCK[clock])
        debug.append("unknown SMPTE clock type {0} on {1}".format(clock, ltc))
    return None


def parse_setlist(data, path):
    r = Reader(data, path)
    r.open_object()
    cls, _ = r.object_head()
    r.section("SetList")
    if cls != "UserSetList":
        return []
    r.section("UserSetList")
    return [r.cstr() for _ in range(r.u32())]


def _object_class(data):
    m = re.match(rb"r\x19\x04\x07.{4}([^\0]*)_UID_", data, re.S)
    return m.group(1).decode() if m else None


def _build_info(archive, debug):
    fields = ("version", "versionName", "releaseType", "phase", "branch", "buildId",
              "customRelease", "tags", "platform", "osImage", "renderStream",
              "starter", "beta", "rc", "custom", "debugBuild", "localPatches")
    build = dict((k, None) for k in fields)
    build["error"] = None
    if not archive.has("conf/depends.txt"):
        build["error"] = "conf/depends.txt not in archive"
        return build
    line = archive.read("conf/depends.txt").decode("utf-8", "replace").strip().splitlines()[0]
    parts = line.split()
    # "d3 <versionName> <revision> <buildId> <date>"
    if len(parts) >= 4 and parts[0] == "d3":
        name, rev, build_id = parts[1], parts[2], parts[3]
        build["versionName"] = name
        build["version"] = "{0}, rev {1}".format(name, rev)
        build["buildId"] = build_id
        m = re.match(r"r[\d.]+_(.+)-branch$", name)
        if m:
            build["branch"] = m.group(1)
    else:
        build["error"] = "unrecognised depends.txt: " + line
    debug.append("system.build from conf/depends.txt; release flags are not in the archive")
    return build


def _f32_value(value):
    """A float32 as the shortest decimal that reads back to the same float32, so
    a keyframe stored as 0.998f exports as 0.998, not 0.9980000257492065."""
    if value != value or value in (float("inf"), float("-inf")):
        return None
    # Decimal rounding with ties away from zero, not "%g": %g breaks ties to
    # even and JavaScript's toPrecision breaks them away from zero, so on a
    # value like 194529.125 the two picked different (equally valid) digits
    # and the browser's export stopped matching this one.
    target = struct.pack("<f", value)
    exact = Decimal(value)
    for precision in range(1, 10):
        candidate = float(Context(prec=precision, rounding=ROUND_HALF_UP).plus(exact))
        if struct.pack("<f", candidate) == target:
            return candidate
    return float(value)


def _keyframe_value(cls, value):
    if cls == "FloatSequence":
        return _f32_value(value)
    if value in (None, "", "null"):
        return None
    return value[:-4] if (cls == "ResourceSequence" and value.endswith(".apx")) else value


def build_keyframes(archive_path, project=None, captured_at=None):
    """Every animated layer parameter in the show, as a separate document from
    the snapshot. "Animated" means two or more keys, or driven by an expression:
    a single key is just the parameter's constant value, and the show holds
    ~190,000 of those against under a thousand real animations."""
    archive = Archive(archive_path)
    doc = {
        "format": "d3_keyframes",
        "formatVersion": KEYFRAMES_FORMAT_VERSION,
        "capturedAt": captured_at or _captured_at(archive_path),
        "project": project or os.path.splitext(os.path.basename(archive_path))[0],
        "source": os.path.basename(archive_path),
        "scope": "animated",
        "interpolation": {
            "codes": dict((str(k), v) for k, v in INTERPOLATION.items()),
            "note": "Inferred from how the codes are used across a real show; not confirmed in Designer.",
        },
        "trackCount": 0, "layerCount": 0, "fieldCount": 0, "keyCount": 0,
        "tracks": [],
        "writtenTo": None,
    }
    census = sorted(p for p in archive.names(TRACK_ROOT + "/") if p.endswith(".apx")
                    and p.count("/") == 2)
    parsed = [(path, parse_track(archive.read(path), path)) for path in census]

    # Show-wide names for exposed attributes. A Notch layer stores each exposed
    # parameter's name beside its attribute id; a RenderStream layer carries the
    # same ids with no name, since its parameter list comes live from the render
    # node. The ids are node ids from the Notch project and read the same on
    # every layer that has a name, so a nameless one borrows it -- but only
    # when every named occurrence agrees, and marked as borrowed.
    names = {}
    for _, track in parsed:
        for layer in track["layers"]:
            for field in layer["fields"]:
                if field["label"] and "::Attributes::" in field["name"]:
                    names.setdefault(field["name"], set()).add(field["label"])

    tracks = []
    for path, track in parsed:
        layers = []
        for layer in track["layers"]:
            fields = []
            for field in layer["fields"]:
                if len(field["keys"]) < 2 and not field["expression"]:
                    continue
                cls = field["cls"]
                label, source = field["label"] or None, "layer" if field["label"] else None
                if label is None and len(names.get(field["name"], ())) == 1:
                    label, source = next(iter(names[field["name"]])), "show"
                fields.append({
                    "name": field["name"],
                    "label": label,
                    "labelSource": source,
                    "valueType": field["valueType"],
                    "expression": field["expression"],
                    "default": _keyframe_value(cls, field["default"]),
                    "keys": [{"t": _num(t),
                              "value": _keyframe_value(cls, value),
                              "interpolation": INTERPOLATION.get(code, "unknown ({0})".format(code))}
                             for t, value, code in field["keys"]],
                })
            if not fields:
                continue
            uid = _uid_int(layer["uid"])
            layers.append({
                "id": "#{0}".format(uid) if uid is not None else None,
                "uid": uid,
                "name": layer["name"],
                "type": layer["type"],
                "groupPath": layer["groupPath"],
                "tStart": _num(layer["tStart"]),
                "tEnd": _num(layer["tEnd"]),
                "notchBlock": layer["notchBlock"],
                "fields": fields,
            })
            doc["fieldCount"] += len(fields)
            doc["keyCount"] += sum(len(f["keys"]) for f in fields)
        if layers:
            tracks.append({
                "id": _track_id(_stem(path), path),
                "name": _stem(path),
                "path": path,
                "bpm": _num(track["bpm"]),
                "layers": layers,
            })
            doc["layerCount"] += len(layers)
    doc["tracks"] = sorted(tracks, key=lambda t: t["id"])
    doc["trackCount"] = len(tracks)
    return doc


def _captured_at(path):
    local = datetime.datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
    offset = local.utcoffset().total_seconds()
    sign = "+" if offset >= 0 else "-"
    offset = abs(int(offset))
    return "{0}{1}{2:02d}:{3:02d}".format(local.strftime("%Y-%m-%dT%H:%M:%S"), sign,
                                         offset // 3600, (offset % 3600) // 60)


def build_snapshot(archive_path, project=None, captured_at=None):
    debug = []
    archive = Archive(archive_path)
    snapshot = {
        "schemaVersion": SCHEMA_VERSION,
        "capturedAt": captured_at or _captured_at(archive_path),
        "project": project or os.path.splitext(os.path.basename(archive_path))[0],
        "scope": "all",
        "activeTransport": None,
        "transportCount": 0,
        "transports": [],
        "trackCount": 0,
        "tracks": [],
        "showfile": {"source": AUTOMATIC_SETLIST_PATH, "trackIds": None,
                     "trackCount": None, "error": None},
        "system": {
            "build": _build_info(archive, debug),
            "options": {
                "project": {"source": None, "values": None,
                            "error": "internal/options/options.bin is not packed into a .d3 archive"},
                "machine": {"source": None, "values": None,
                            "error": "machine.bin lives outside the project and is not in a .d3 archive"},
            },
        },
        "writtenTo": None,
        "error": None,
        "debug": debug,
    }

    transports = {}
    for path in archive.names("objects/transportmanager/"):
        data = archive.read(path)
        if _object_class(data) == "TransportManager":
            transports[_stem(path)] = data

    active = None
    if archive.has(DIRECTOR_STATE):
        refs = _paths_in(archive.read(DIRECTOR_STATE), "objects/transportmanager/")
        if refs:
            active = _stem(refs[-1])
    snapshot["activeTransport"] = active
    active_fps = _transport_fps(archive, transports[active], debug) if active in transports else None

    order = ([active] if active in transports else []) + sorted(t for t in transports if t != active)
    builder = TrackBuilder(archive, debug)
    for name in order:
        data = transports[name]
        record = {"name": name, "setlist": None, "trackCount": 0, "trackRefs": [], "error": None}
        setlists = _paths_in(data, "objects/usersetlist/") + _paths_in(data, "objects/setlist/")
        if not setlists or not archive.has(setlists[0]):
            record["error"] = "transport has no setlist"
            snapshot["transports"].append(record)
            continue
        record["setlist"] = _stem(setlists[0])
        # A transport with no LTC input still reports a frame rate in Designer;
        # on the reference show it was the active transport's.
        fps = _transport_fps(archive, data, debug) or active_fps
        record["trackRefs"] = [builder.add(t, fps)
                               for t in parse_setlist(archive.read(setlists[0]), setlists[0])]
        record["trackCount"] = len(record["trackRefs"])
        snapshot["transports"].append(record)

    snapshot["transportCount"] = len(snapshot["transports"])
    snapshot["tracks"] = builder.sorted_records()
    snapshot["trackCount"] = len(snapshot["tracks"])

    census = sorted(p for p in archive.names(TRACK_ROOT + "/") if p.endswith(".apx")
                    and p.count("/") == 2)
    ids = [builder.id_for(p) for p in census]
    snapshot["showfile"]["trackIds"] = ids
    snapshot["showfile"]["trackCount"] = len(ids)
    return snapshot


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("archive", help="path to a .d3 project archive")
    ap.add_argument("-o", "--output", help="output .json (default: <stamp>_<project>.json next to the archive)")
    ap.add_argument("--project", help="project name to record (default: archive file stem)")
    ap.add_argument("--captured-at", help="ISO timestamp to record (default: archive mtime)")
    ap.add_argument("--keyframes", nargs="?", const="", metavar="PATH",
                    help="also write every animated layer parameter to a separate JSON "
                         "(default: <stamp>_<project>_keyframes.json beside the snapshot)")
    args = ap.parse_args(argv)

    try:
        snapshot = build_snapshot(args.archive, args.project, args.captured_at)
        keyframes = (build_keyframes(args.archive, args.project, args.captured_at)
                     if args.keyframes is not None else None)
    except (ParseError, ValueError) as error:
        print("error: {0}".format(error), file=sys.stderr)
        return 1
    out = args.output
    if not out:
        stamp = snapshot["capturedAt"][:19].replace("T", "_").replace(":", "-")
        safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in snapshot["project"])
        out = os.path.join(os.path.dirname(os.path.abspath(args.archive)),
                           "{0}_{1}.json".format(stamp, safe))
    snapshot["writtenTo"] = os.path.abspath(out)
    with open(out, "w") as fh:
        fh.write(json.dumps(snapshot, indent=2, sort_keys=True))
    print("wrote {0}: {1} transports, {2} tracks, {3} layers".format(
        out, snapshot["transportCount"], snapshot["trackCount"],
        sum(t["layerCount"] for t in snapshot["tracks"])))

    if keyframes is not None:
        kout = args.keyframes or (os.path.splitext(out)[0] + "_keyframes.json")
        keyframes["writtenTo"] = os.path.abspath(kout)
        with open(kout, "w") as fh:
            fh.write(json.dumps(keyframes, indent=2, sort_keys=True))
        print("wrote {0}: {1} keys on {2} parameters, {3} layers, {4} tracks".format(
            kout, keyframes["keyCount"], keyframes["fieldCount"],
            keyframes["layerCount"], keyframes["trackCount"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
