"""Minimal fragmented-MP4 muxer for H.264 (stdlib only) — feeds browser MSE.

The scrcpy stream is Annex-B (start codes). MSE wants an init segment
(ftyp+moov with an avcC built from SPS/PPS) and then one moof+mdat fragment per
access unit, samples length-prefixed (AVCC). Pure functions, unit-tested.
"""
from __future__ import annotations
import struct

TIMESCALE = 1_000_000          # scrcpy pts are microseconds
DEFAULT_DUR = 33_333           # duration of the first sample
MIN_DUR, MAX_DUR = 1_000, 100_000


def split_annexb(data: bytes) -> list[bytes]:
    """Annex-B byte stream → list of NAL units (start codes removed)."""
    nals, i, n = [], 0, len(data)
    starts = []
    while i < n - 2:
        if data[i] == 0 and data[i + 1] == 0:
            if data[i + 2] == 1:
                starts.append((i, i + 3)); i += 3; continue
            if i < n - 3 and data[i + 2] == 0 and data[i + 3] == 1:
                starts.append((i, i + 4)); i += 4; continue
        i += 1
    for k, (_, s) in enumerate(starts):
        e = starts[k + 1][0] if k + 1 < len(starts) else n
        if e > s:
            nals.append(data[s:e])
    if not starts and data:
        nals.append(data)
    return nals


def nal_type(nal: bytes) -> int:
    return nal[0] & 0x1F


def sps_pps(config: bytes):
    sps = pps = None
    for nal in split_annexb(config):
        t = nal_type(nal)
        if t == 7 and sps is None:
            sps = nal
        elif t == 8 and pps is None:
            pps = nal
    return sps, pps


def to_avcc(frame: bytes) -> bytes:
    """Annex-B access unit → 4-byte length-prefixed sample (drop SPS/PPS/AUD)."""
    out = b""
    for nal in split_annexb(frame):
        if nal_type(nal) in (7, 8, 9):
            continue
        out += struct.pack(">I", len(nal)) + nal
    return out


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def full(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return box(kind, struct.pack(">I", (version << 24) | flags) + payload)


def avcc_box(sps: bytes, pps: bytes) -> bytes:
    return box(b"avcC", bytes([1, sps[1], sps[2], sps[3], 0xFF, 0xE1])
               + struct.pack(">H", len(sps)) + sps + b"\x01" + struct.pack(">H", len(pps)) + pps)


def init_segment(sps: bytes, pps: bytes, width: int, height: int) -> bytes:
    matrix = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")
    mvhd = full(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, TIMESCALE, 0) + struct.pack(">IH", 0x10000, 0x100)
                + b"\x00" * 10 + matrix + b"\x00" * 24 + struct.pack(">I", 2))
    tkhd = full(b"tkhd", 0, 3, struct.pack(">IIIII", 0, 0, 1, 0, 0) + b"\x00" * 8
                + struct.pack(">hhHH", 0, 0, 0, 0) + matrix
                + struct.pack(">II", width << 16, height << 16))
    mdhd = full(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, TIMESCALE, 0) + struct.pack(">HH", 0x55C4, 0))
    hdlr = full(b"hdlr", 0, 0, struct.pack(">I", 0) + b"vide" + b"\x00" * 12 + b"kakao\x00")
    vmhd = full(b"vmhd", 0, 1, b"\x00" * 8)
    dinf = box(b"dinf", full(b"dref", 0, 0, struct.pack(">I", 1) + full(b"url ", 0, 1, b"")))
    avc1 = box(b"avc1", b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 16
               + struct.pack(">HH", width, height) + struct.pack(">II", 0x480000, 0x480000)
               + struct.pack(">I", 0) + struct.pack(">H", 1) + b"\x00" * 32
               + struct.pack(">Hh", 0x18, -1) + avcc_box(sps, pps))
    stsd = full(b"stsd", 0, 0, struct.pack(">I", 1) + avc1)
    empty = lambda k: full(k, 0, 0, struct.pack(">I", 0))
    stbl = box(b"stbl", stsd + empty(b"stts") + empty(b"stsc")
               + full(b"stsz", 0, 0, struct.pack(">II", 0, 0)) + empty(b"stco"))
    minf = box(b"minf", vmhd + dinf + stbl)
    mdia = box(b"mdia", mdhd + hdlr + minf)
    trak = box(b"trak", tkhd + mdia)
    trex = full(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, 0, 0, 0))
    moov = box(b"moov", mvhd + trak + box(b"mvex", trex))
    return ftyp + moov


def fragment(seq: int, base_time: int, sample: bytes, key: bool, duration: int = DEFAULT_DUR) -> bytes:
    """One moof+mdat for a single AVCC sample."""
    flags = 0x02000000 if key else 0x01010000           # sync vs non-sync, depends-on
    trun_flags = 0x000001 | 0x000100 | 0x000200 | 0x000400  # data-offset, dur, size, flags
    def build(data_offset):
        trun = full(b"trun", 0, trun_flags, struct.pack(">Ii", 1, data_offset)
                    + struct.pack(">III", duration, len(sample), flags))
        tfhd = full(b"tfhd", 0, 0x020000, struct.pack(">I", 1))   # default-base-is-moof
        tfdt = full(b"tfdt", 1, 0, struct.pack(">Q", base_time))
        traf = box(b"traf", tfhd + tfdt + trun)
        mfhd = full(b"mfhd", 0, 0, struct.pack(">I", seq))
        return box(b"moof", mfhd + traf)
    moof = build(0)
    moof = build(len(moof) + 8)                            # offset to the mdat payload
    return moof + box(b"mdat", sample)


class Muxer:
    """Stateful per-viewer muxer: init when SPS/PPS/size are known, then fragments.

    The media timeline is kept *contiguous*: each sample starts where the previous
    one ended. Browsers treat a decode-time jump larger than twice the previous
    sample's duration as a discontinuity and then drop every frame until the next
    keyframe — with real pts and a screen that only updates on change, that would
    freeze the picture. A sample's duration is the gap since the previous frame
    (clamped), so playback still advances at roughly wall-clock speed."""

    def __init__(self):
        self.seq = 0
        self.prev_pts = None
        self.next_dts = 0

    def init(self, config: bytes, width: int, height: int) -> bytes | None:
        sps, pps = sps_pps(config)
        if not sps or not pps:
            return None
        self.seq, self.prev_pts, self.next_dts = 0, None, 0
        return init_segment(sps, pps, width, height)

    def frame(self, pts: int, key: bool, data: bytes) -> bytes | None:
        sample = to_avcc(data)
        if not sample:
            return None
        dur = DEFAULT_DUR if self.prev_pts is None else max(MIN_DUR, min(MAX_DUR, pts - self.prev_pts))
        self.prev_pts = pts
        t, self.next_dts = self.next_dts, self.next_dts + dur
        self.seq += 1
        return fragment(self.seq, t, sample, key, dur)


def parse_boxes(data: bytes, depth: int = 0, containers=(b"moov", b"trak", b"mdia", b"minf",
                b"stbl", b"mvex", b"moof", b"traf", b"dinf")):
    """Tiny box walker (for tests/debugging): [(depth, type, size)]."""
    out, i = [], 0
    while i + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        if size < 8 or i + size > len(data):
            raise ValueError("bad box at %d" % i)
        out.append((depth, kind.decode("latin1"), size))
        if kind in containers:
            out += parse_boxes(data[i + 8:i + size], depth + 1, containers)
        i += size
    return out
