"""Audio -> MIDI converter (melody, chords, drums).

Standalone engine used by both the command line and the window (gui.py).
Output is a standard .mid file that FL Studio can import
(drag it onto the Playlist or Channel rack).

Dependencies: numpy, scipy, soundfile.
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np
from scipy import ndimage, signal

TARGET_SR = 22050
PPQ = 480  # MIDI ticks per quarter note

MODES = ("melody", "chords", "drums", "all", "song")
QUANTIZE_CHOICES = ("off", "1/4", "1/8", "1/16", "1/32")

# General-MIDI drum notes (FL Studio's FPC and most drum kits follow this map).
KICK, SNARE, CLOSED_HAT, OPEN_HAT, CRASH = 36, 38, 42, 46, 49
TOM_LOW, TOM_MID, TOM_HIGH = 43, 47, 50


@dataclass
class Note:
    start: float  # seconds
    end: float  # seconds
    pitch: int  # MIDI note number 0..127
    velocity: int  # 1..127


@dataclass
class Track:
    name: str
    notes: List[Note]
    channel: int = 0
    program: int = 0


# --------------------------------------------------------------------------
# Loading audio
# --------------------------------------------------------------------------


def load_audio(path: str, sr: int = TARGET_SR) -> Tuple[np.ndarray, int]:
    """Read any file libsndfile understands (wav, flac, ogg, mp3...) as mono."""
    try:
        import soundfile as sf

        data, file_sr = sf.read(path, dtype="float32", always_2d=True)
    except ImportError:  # fall back to plain WAV through scipy
        from scipy.io import wavfile

        file_sr, data = wavfile.read(path)
        if data.dtype.kind in "iu":
            data = data.astype(np.float32) / float(np.iinfo(data.dtype).max)
        data = data.astype(np.float32)
        if data.ndim == 1:
            data = data[:, None]
    mono = data.mean(axis=1)
    if file_sr != sr:
        g = math.gcd(int(file_sr), int(sr))
        mono = signal.resample_poly(mono, sr // g, int(file_sr) // g)
    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 0:
        mono = mono / peak
    return mono.astype(np.float32), sr


# --------------------------------------------------------------------------
# Melody: YIN pitch tracker
# --------------------------------------------------------------------------


def _frames(x: np.ndarray, size: int, hop: int) -> np.ndarray:
    pad = np.zeros(size // 2, dtype=x.dtype)
    xp = np.concatenate([pad, x, pad])
    if len(xp) < size:
        xp = np.pad(xp, (0, size - len(xp)))
    view = np.lib.stride_tricks.sliding_window_view(xp, size)[::hop]
    return view


def yin_pitch(
    x: np.ndarray,
    sr: int,
    fmin: float = 55.0,
    fmax: float = 1500.0,
    hop: int = 256,
    thresh: float = 0.15,
):
    """Return (f0_hz, aperiodicity, rms) per frame. f0 is 0 where nothing found."""
    w = 1 << int(math.ceil(math.log2(sr / fmin)))  # integration window
    size = 2 * w
    tau_min = max(2, int(sr / fmax))
    tau_max = min(w - 2, int(sr / fmin))
    frames = _frames(x, size, hop)
    n = frames.shape[0]
    f0 = np.zeros(n)
    ap = np.ones(n)
    rms = np.zeros(n)
    nfft = 2 * size
    chunk = 512
    for s in range(0, n, chunk):
        fr = frames[s : s + chunk].astype(np.float64)
        rms[s : s + chunk] = np.sqrt(np.mean(fr**2, axis=1))
        a = fr[:, :w]
        fa = np.fft.rfft(a, nfft, axis=1)
        ff = np.fft.rfft(fr, nfft, axis=1)
        cross = np.fft.irfft(np.conj(fa) * ff, nfft, axis=1)[:, :w]
        cs = np.cumsum(fr**2, axis=1)
        e1 = cs[:, w - 1][:, None]
        taus = np.arange(w)
        upper = cs[:, w - 1 + taus]
        lower = np.where(taus[None, :] > 0, cs[:, np.maximum(taus - 1, 0)], 0.0)
        e2 = upper - lower
        d = e1 + e2 - 2.0 * cross
        d[:, 0] = 0.0
        run = np.cumsum(d[:, 1:], axis=1)
        cmnd = np.ones_like(d)
        cmnd[:, 1:] = d[:, 1:] * taus[1:][None, :] / np.maximum(run, 1e-12)

        rng = cmnd[:, tau_min : tau_max + 1]
        below = rng < thresh
        has = below.any(axis=1)
        first = below.argmax(axis=1) + tau_min
        glob = rng.argmin(axis=1) + tau_min
        t = np.where(has, first, glob)
        rows = np.arange(len(t))
        # slide down to the local minimum of the dip
        for _ in range(200):
            nxt = np.minimum(t + 1, tau_max)
            move = has & (cmnd[rows, nxt] < cmnd[rows, t]) & (t < tau_max)
            if not move.any():
                break
            t = t + move
        # parabolic interpolation around t
        tm = np.clip(t, 1, w - 2)
        c0, c1, c2 = d[rows, tm - 1], d[rows, tm], d[rows, tm + 1]
        den = c0 - 2 * c1 + c2
        shift = 0.5 * (c0 - c2) / np.where(np.abs(den) > 1e-12, den, np.inf)
        tau = tm + np.clip(shift, -1, 1)
        f0[s : s + chunk] = sr / tau
        ap[s : s + chunk] = cmnd[rows, tm]
    return f0, ap, rms


def _runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    """Start/end (end exclusive) of each run of True."""
    if mask.size == 0:
        return []
    m = np.concatenate([[False], mask.astype(bool), [False]])
    d = np.diff(m.astype(np.int8))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def _velocity(level_db: float, lo_db: float = -40.0) -> int:
    """level_db is relative to the loudest thing in the file (<= 0)."""
    frac = np.clip((level_db - lo_db) / (-lo_db), 0.0, 1.0)
    return int(round(40 + 87 * frac))


def audio_to_melody(x: np.ndarray, sr: int, sensitivity: float = 0.5, fmin: float = 55.0) -> List[Note]:
    hop = 256
    thresh = 0.10 + 0.15 * sensitivity  # more sensitive -> accept less clean pitches
    f0, ap, rms = yin_pitch(x, sr, fmin=fmin, hop=hop, thresh=thresh)
    ref = max(float(rms.max()), 1e-9)
    db = 20 * np.log10(np.maximum(rms, 1e-9) / ref)
    gate_db = -42.0 - 20.0 * sensitivity  # -42 dB .. -62 dB
    voiced = (ap < 0.25 + 0.2 * sensitivity) & (db > gate_db) & (f0 > 0)
    midi = np.where(f0 > 0, 69 + 12 * np.log2(np.maximum(f0, 1e-6) / 440.0), 0.0)

    min_frames = max(2, int(0.045 * sr / hop))
    min_note = 0.060
    t = lambda i: i * hop / sr  # frame i is centred at i*hop (we padded by size/2)
    notes: List[Note] = []
    for s, e in _runs(voiced):
        seg = midi[s:e]
        if len(seg) >= 5:
            seg = signal.medfilt(seg, 5)
        q = np.round(seg).astype(int)
        # run-length encode, then absorb short runs into their neighbour
        runs = []
        for k, p in enumerate(q):
            if runs and runs[-1][0] == p:
                runs[-1][2] = k + 1
            else:
                runs.append([p, k, k + 1])
        changed = True
        while changed and len(runs) > 1:
            changed = False
            for i, (p, a, b) in enumerate(runs):
                if b - a < min_frames:
                    if i > 0:
                        runs[i - 1][2] = b
                    else:
                        runs[i + 1][1] = a
                    del runs[i]
                    changed = True
                    break
            merged = []
            for r in runs:
                if merged and merged[-1][0] == r[0]:
                    merged[-1][2] = r[2]
                else:
                    merged.append(r)
            runs = merged
        for p, a, b in runs:
            start = t(s + a) - 0.5 * hop / sr
            end = t(s + b) - 0.5 * hop / sr
            if end - start < min_note or not (0 <= p <= 127):
                continue
            lvl = float(np.mean(db[s + a : s + b]))
            notes.append(Note(max(0.0, start), end, int(p), _velocity(lvl)))
    return notes


# --------------------------------------------------------------------------
# Chords: harmonic-sum salience with greedy cancellation
# --------------------------------------------------------------------------


# The base pitch counts most: a major chord is exactly the 4th/5th/6th overtone of a low
# note, so without this the chord C-E-G would be heard as one deep C.
HARMONIC_WEIGHTS = (4.0, 2.0, 1.0, 0.7, 0.5, 0.4, 0.3, 0.25)


def _note_filterbank(n_bins: int, sr: int, n_fft: int, lo: int, hi: int, n_harm: int):
    """Returns (weights, masks), both shaped (128, n_bins).

    weights[n] : normalised sum of harmonics (salience of note n)
    masks[n]   : 0..1 coverage of note n's harmonics (used for cancellation)
    """
    bin_hz = sr / n_fft
    weights = np.zeros((128, n_bins))
    masks = np.zeros((128, n_bins))
    for n in range(lo, hi + 1):
        f0 = 440.0 * 2 ** ((n - 69) / 12)
        for h in range(1, n_harm + 1):
            f = f0 * h
            if f >= sr / 2 - 2 * bin_hz:
                break
            half = max(1.2, 0.5 * (2 ** (1 / 12) - 1) * f / bin_hz)
            c = f / bin_hz
            b0, b1 = int(max(0, math.floor(c - half))), int(min(n_bins - 1, math.ceil(c + half)))
            idx = np.arange(b0, b1 + 1)
            tri = np.clip(1 - np.abs(idx - c) / half, 0, None)
            # every harmonic gets a unit-area window so wide (high) windows don't count extra
            weights[n, idx] += tri / max(tri.sum(), 1e-9) * HARMONIC_WEIGHTS[h - 1]
            # the cancellation mask is wider (~a semitone) so leftovers of a picked
            # note are not mistaken for its neighbours
            mh = 1.8 * half
            m0, m1 = int(max(0, math.floor(c - mh))), int(min(n_bins - 1, math.ceil(c + mh)))
            midx = np.arange(m0, m1 + 1)
            masks[n, midx] = np.maximum(masks[n, midx], np.clip(1 - np.abs(midx - c) / mh, 0, None) ** 0.5)
        s = weights[n].sum()
        if s > 0:
            weights[n] /= s
    return weights, masks


def audio_to_chords(x: np.ndarray, sr: int, sensitivity: float = 0.5, max_poly: int = 6) -> List[Note]:
    n_fft, hop = 4096, 512
    lo, hi = 36, 96  # C2 .. C7
    win = signal.windows.hann(n_fft, sym=False)
    frames = _frames(x, n_fft, hop)
    n_frames = frames.shape[0]
    n_bins = n_fft // 2 + 1
    weights, masks = _note_filterbank(n_bins, sr, n_fft, lo, hi, 8)

    # magnitude spectrogram, log-compressed
    mags = np.empty((n_frames, n_bins), dtype=np.float32)
    for s in range(0, n_frames, 1024):
        mags[s : s + 1024] = np.abs(np.fft.rfft(frames[s : s + 1024] * win, axis=1))
    ref = max(float(np.percentile(mags, 99.5)), 1e-9)
    frame_energy = np.sqrt((mags.astype(np.float64) ** 2).sum(axis=1))
    frame_db = 20 * np.log10(np.maximum(frame_energy, 1e-9) / (frame_energy.max() + 1e-12))

    rel_global = 0.30 - 0.22 * sensitivity  # fraction of loudest salience a note must reach
    rel_frame = 0.55 - 0.30 * sensitivity  # fraction of the strongest note in the same frame
    gate_db = -38.0 - 20.0 * sensitivity

    strength = np.zeros((n_frames, 128), dtype=np.float32)
    first_pick = np.zeros(n_frames, dtype=np.float32)
    for s in range(0, n_frames, 512):
        comp = np.sqrt(mags[s : s + 512] / ref)
        rows = np.arange(comp.shape[0])
        for k in range(max_poly):
            sal = comp @ weights.T  # (frames, 128)
            sal[:, :lo] = 0
            sal[:, hi + 1 :] = 0
            pick = sal.argmax(axis=1)
            val = sal[rows, pick]
            if k == 0:
                first_pick[s : s + 512] = val
            strength[s + rows, pick] = np.maximum(strength[s + rows, pick], val)
            comp = comp * (1.0 - 0.85 * masks[pick])
    # two notes a single semitone apart are almost always leakage: keep the stronger one
    dominated = (strength > 0) & ((np.roll(strength, 1, axis=1) > strength) | (np.roll(strength, -1, axis=1) > strength))
    strength[dominated] = 0
    top = max(float(first_pick.max()), 1e-9)
    # hiss has no tall spectral peaks; a musical tone towers over the median bin
    tonal = (mags.max(axis=1) / (np.median(mags, axis=1) + 1e-12)) > 30.0
    active = (
        (strength >= rel_global * top)
        & (strength >= rel_frame * first_pick[:, None])
        & (frame_db[:, None] > gate_db)
        & tonal[:, None]
    )

    # tidy up in time: smooth, bridge small gaps, drop blips
    act = ndimage.median_filter(active.astype(np.uint8), size=(5, 1)).astype(bool)
    min_frames = max(3, int(0.09 * sr / hop))
    gap_frames = 3
    SMEAR = 0.045
    notes: List[Note] = []
    for p in range(lo, hi + 1):
        col = act[:, p]
        if not col.any():
            continue
        runs = _runs(col)
        merged: List[List[int]] = []
        for a, b in runs:
            if merged and a - merged[-1][1] <= gap_frames:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        for a, b in merged:
            if b - a < min_frames:
                continue
            lvl = float(np.mean(strength[a:b, p])) / top
            vel = int(round(45 + 80 * np.clip(lvl, 0, 1)))
            # the 186 ms analysis window smears edges by ~45 ms each way: pull them back in
            # (not at the very start/end of the file, where there is nothing to smear)
            start = a * hop / sr + (SMEAR if a > 0 else 0.0)
            end = b * hop / sr - (SMEAR if b < n_frames else 0.0)
            if end - start < 0.05:
                continue
            notes.append(Note(start, end, p, min(127, vel)))
    notes.sort(key=lambda n: (n.start, n.pitch))
    return notes


# --------------------------------------------------------------------------
# Drums: onset detection + band-energy classification
# --------------------------------------------------------------------------


def audio_to_drums(x: np.ndarray, sr: int, sensitivity: float = 0.5, full_mix: bool = False) -> List[Note]:
    """full_mix=True is for drums heard inside a whole song: only kick, snare and
    (closed) hat are reported. Toms, open hats and crashes are skipped because vocals,
    synths and bass get mistaken for them."""
    n_fft, hop = 1024, 128
    win = signal.windows.hann(n_fft, sym=False)
    frames = _frames(x, n_fft, hop)
    mags = np.abs(np.fft.rfft(frames * win, axis=1)).astype(np.float64)
    n_frames = mags.shape[0]
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    ref = max(float(np.percentile(mags, 99.5)), 1e-9)
    logm = np.log1p(200.0 * mags / ref)

    # onset strength: positive log-magnitude change, summed across bins
    # (prepend silence so a hit on the very first frame still counts)
    diff = np.maximum(0, np.diff(logm, axis=0, prepend=np.zeros((1, logm.shape[1]))))
    flux = ndimage.gaussian_filter1d(diff.sum(axis=1), 1.0)
    if flux.max() <= 0:
        return []
    flux /= flux.max()
    local = ndimage.uniform_filter1d(flux, size=int(0.5 * sr / hop))
    delta = 0.30 - 0.24 * sensitivity
    peak_win = int(0.030 * sr / hop)
    is_peak = (
        (flux == ndimage.maximum_filter1d(flux, size=2 * peak_win + 1))
        & (flux > local + delta * 0.5)
        & (flux > 0.5 * delta)
    )
    onsets = np.where(is_peak)[0]
    if len(onsets) == 0:
        return []

    bands = {"low": (35, 150), "mid": (150, 1500), "hi1": (1500, 6000), "hi2": (6000, sr / 2)}
    power = mags**2
    band_env = {k: power[:, (freqs >= a) & (freqs < b)].sum(axis=1) for k, (a, b) in bands.items()}
    hi_env_all = band_env["hi1"] + band_env["hi2"]

    post_n = max(2, int(0.050 * sr / hop))
    pre_n = max(2, int(0.030 * sr / hop))
    flux_max = max(float(flux[onsets].max()), 1e-9)

    recs = []
    for idx, o in enumerate(onsets):
        gain = {}
        for k, env in band_env.items():
            post = env[o : o + post_n].max()
            pre = env[max(0, o - pre_n) : max(o, 1)].mean()
            gain[k] = max(0.0, post - pre)
        total = sum(gain.values())
        if total <= 0:
            continue
        fl, fm, f1, f2 = (gain[k] / total for k in ("low", "mid", "hi1", "hi2"))
        fhi = f1 + f2
        t0 = max(0.0, o * hop / sr)
        vel = int(round(50 + 77 * np.clip(flux[o] / flux_max, 0, 1)))

        # how long does the high band ring (until -20 dB or the next hit)?
        stop = onsets[idx + 1] if idx + 1 < len(onsets) else n_frames
        hi_env = hi_env_all[o : max(stop, o + post_n)]
        peak = hi_env[:post_n].max()
        ring = 0.0
        if peak > 0:
            below = np.where(hi_env[post_n:] < 0.01 * peak)[0]
            ring = (post_n + (below[0] if len(below) else len(hi_env) - post_n)) * hop / sr

        hits: List[int] = []
        if fl >= 0.30:
            hits.append(KICK)
        if fm >= 0.22 and fhi >= 0.22 and fl < 0.8:
            hits.append(SNARE)
        elif fm >= 0.45 and fhi < 0.22 and fl < 0.5 and not full_mix:
            band = (freqs >= 80) & (freqs < 400)
            seg = mags[o : o + post_n][:, band].mean(axis=0)
            cen = float((seg * freqs[band]).sum() / max(seg.sum(), 1e-12))
            hits.append(TOM_LOW if cen < 140 else TOM_MID if cen < 220 else TOM_HIGH)
        if fhi >= 0.50 and fm < 0.22 and fl < 0.3:
            if full_mix:
                hits.append(CLOSED_HAT)
            else:
                hits.append(CRASH if ring > 0.50 else OPEN_HAT if ring > 0.09 else CLOSED_HAT)
        if not hits:
            if full_mix and fm >= 0.45 and fhi < 0.22 and fl < 0.5:
                continue  # tonal mid-range hit (voice / synth), not a drum
            hits.append(max((fl, KICK), (fm, SNARE), (fhi, CLOSED_HAT))[1])
        recs.append({"t": t0, "vel": vel, "hits": hits, "hi2": gain["hi2"], "ring": ring})

    # A hat played together with a (louder) kick hides inside the kick's energy.
    # Use the file's own clear hats as a yardstick to spot it.
    pure = [r["hi2"] for r in recs if r["hits"] and r["hits"][0] in (CLOSED_HAT, OPEN_HAT)]
    if pure:
        ref_hat = float(np.median(pure))
        for r in recs:
            if r["hits"] == [KICK] and r["hi2"] >= 0.5 * ref_hat:
                r["hits"].append(OPEN_HAT if r["ring"] > 0.09 and not full_mix else CLOSED_HAT)

    notes = [Note(r["t"], r["t"] + 0.10, h, r["vel"]) for r in recs for h in r["hits"]]
    notes.sort(key=lambda n: (n.start, n.pitch))
    return notes


# --------------------------------------------------------------------------
# Quantize + MIDI writing
# --------------------------------------------------------------------------


def quantize_notes(notes: Sequence[Note], bpm: float, grid: str) -> List[Note]:
    if grid == "off" or not notes:
        return list(notes)
    den = int(grid.split("/")[1])
    step = (4.0 / den) * 60.0 / bpm  # seconds per grid step
    out: List[Note] = []
    for n in notes:
        s = round(n.start / step) * step
        e = round(n.end / step) * step
        if e <= s:
            e = s + step
        out.append(Note(s, e, n.pitch, n.velocity))
    # a pitch cannot overlap itself: trim to the next start of the same pitch
    out.sort(key=lambda n: (n.pitch, n.start))
    for a, b in zip(out, out[1:]):
        if a.pitch == b.pitch and a.end > b.start:
            a.end = b.start
    out = [n for n in out if n.end > n.start]
    out.sort(key=lambda n: (n.start, n.pitch))
    return out


def _vlq(value: int) -> bytes:
    buf = [value & 0x7F]
    value >>= 7
    while value:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(buf))


def _chunk(tag: bytes, body: bytes) -> bytes:
    return tag + struct.pack(">I", len(body)) + body


def write_midi(path: str, tracks: Sequence[Track], bpm: float) -> None:
    """Write a type-1 standard MIDI file (tempo track + one track per Track)."""
    tick_per_s = PPQ * bpm / 60.0
    tempo = int(round(60_000_000 / bpm))
    meta = bytearray()
    meta += _vlq(0) + b"\xff\x51\x03" + tempo.to_bytes(3, "big")
    meta += _vlq(0) + b"\xff\x58\x04" + bytes([4, 2, 24, 8])  # 4/4
    meta += _vlq(0) + b"\xff\x2f\x00"
    chunks = [_chunk(b"MTrk", bytes(meta))]

    for tr in tracks:
        ev = []  # (tick, order, bytes); note-off (0) sorts before note-on (1)
        for n in tr.notes:
            on = int(round(n.start * tick_per_s))
            off = max(on + 1, int(round(n.end * tick_per_s)))
            ch = tr.channel & 0x0F
            ev.append((on, 1, bytes([0x90 | ch, n.pitch & 0x7F, max(1, min(127, n.velocity))])))
            ev.append((off, 0, bytes([0x80 | ch, n.pitch & 0x7F, 0])))
        ev.sort(key=lambda e: (e[0], e[1]))
        body = bytearray()
        name = tr.name.encode("ascii", "replace")
        body += _vlq(0) + b"\xff\x03" + _vlq(len(name)) + name
        if tr.channel != 9:
            body += _vlq(0) + bytes([0xC0 | (tr.channel & 0x0F), tr.program & 0x7F])
        last = 0
        for tick, _, data in ev:
            body += _vlq(tick - last) + data
            last = tick
        body += _vlq(0) + b"\xff\x2f\x00"
        chunks.append(_chunk(b"MTrk", bytes(body)))

    header = _chunk(b"MThd", struct.pack(">HHH", 1, len(chunks), PPQ))
    with open(path, "wb") as f:
        f.write(header + b"".join(chunks))


# --------------------------------------------------------------------------
# High-level entry point
# --------------------------------------------------------------------------


def convert(
    in_path: str,
    out_path: str,
    mode: str = "melody",
    bpm: float = 130.0,
    quantize: str = "off",
    sensitivity: float = 0.5,
    log: Optional[Callable[[str], None]] = None,
    full_mix_drums: bool = False,
) -> List[Track]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if quantize not in QUANTIZE_CHOICES:
        raise ValueError(f"quantize must be one of {QUANTIZE_CHOICES}")
    if not (20 <= bpm <= 400):
        raise ValueError("bpm must be between 20 and 400")
    sensitivity = float(np.clip(sensitivity, 0.0, 1.0))
    say = log or (lambda s: None)

    say(f"Reading {os.path.basename(in_path)} ...")
    x, sr = load_audio(in_path)
    if x.size < sr // 10 or float(np.max(np.abs(x))) == 0:
        raise ValueError("The audio is empty or silent.")
    say(f"Audio length: {len(x) / sr:.1f} seconds")

    tracks: List[Track] = []
    if mode == "song":
        import shutil
        import tempfile

        import stems

        work = tempfile.mkdtemp(prefix="audio2midi_stems_")
        try:
            parts = stems.separate(in_path, work, log=say)
            say("Listening to each part on its own ...")
            st = {name: load_audio(p)[0] for name, p in parts.items()}
        finally:
            shutil.rmtree(work, ignore_errors=True)
        tracks.append(Track("Vocals", audio_to_melody(st["vocals"], sr, sensitivity), channel=0))
        tracks.append(Track("Bass", audio_to_melody(st["bass"], sr, sensitivity, fmin=35.0), channel=1, program=33))
        tracks.append(Track("Chords", audio_to_chords(st["other"], sr, sensitivity), channel=2))
        tracks.append(Track("Drums", audio_to_drums(st["drums"], sr, sensitivity), channel=9))
    if mode in ("melody", "all"):
        say("Listening for a melody ...")
        tracks.append(Track("Melody", audio_to_melody(x, sr, sensitivity), channel=0))
    if mode in ("chords", "all"):
        say("Listening for chords (this one is slower) ...")
        tracks.append(Track("Chords", audio_to_chords(x, sr, sensitivity), channel=1))
    if mode in ("drums", "all"):
        say("Listening for drums ...")
        tracks.append(Track("Drums", audio_to_drums(x, sr, sensitivity, full_mix=full_mix_drums), channel=9))

    for tr in tracks:
        tr.notes = quantize_notes(tr.notes, bpm, quantize)
        say(f"{tr.name}: {len(tr.notes)} notes")
    if not any(tr.notes for tr in tracks):
        raise ValueError("No notes were found. Try raising the sensitivity.")

    write_midi(out_path, tracks, bpm)
    say(f"Saved {out_path}")
    return tracks


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Convert audio to a MIDI file.")
    ap.add_argument("input", help="audio file (wav, flac, ogg, mp3)")
    ap.add_argument("-o", "--output", help="output .mid (default: next to the input)")
    ap.add_argument("-m", "--mode", choices=MODES, default="melody")
    ap.add_argument("--bpm", type=float, default=130.0, help="your FL Studio project tempo")
    ap.add_argument("-q", "--quantize", choices=QUANTIZE_CHOICES, default="off")
    ap.add_argument("--full-mix-drums", action="store_true", help="drums come from a whole song: skip toms/open hats/crash")
    ap.add_argument("-s", "--sensitivity", type=float, default=0.5, help="0 (strict) .. 1 (picks up more)")
    args = ap.parse_args(argv)
    out = args.output or os.path.splitext(args.input)[0] + ".mid"
    try:
        convert(args.input, out, args.mode, args.bpm, args.quantize, args.sensitivity, log=print, full_mix_drums=args.full_mix_drums)
    except Exception as e:  # noqa: BLE001 - show a friendly message
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
