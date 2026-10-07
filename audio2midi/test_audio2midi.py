"""Tests: synthesise sounds whose notes we know, convert, and compare."""

import os
import struct
import tempfile

import numpy as np
import pytest
import soundfile as sf

import audio2midi as a2m

SR = 44100  # test files are 44.1k so the resampler is exercised too


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def tone(midi, dur, harmonics=6, sr=SR):
    t = np.arange(int(dur * sr)) / sr
    y = sum(np.sin(2 * np.pi * hz(midi) * h * t) / h for h in range(1, harmonics + 1))
    env = np.minimum(1, np.minimum(t / 0.01, (dur - t) / 0.03))
    return (y * env * 0.3).astype(np.float32)


def place(parts, total, sr=SR):
    out = np.zeros(int(total * sr), dtype=np.float32)
    for start, y in parts:
        i = int(start * sr)
        out[i : i + len(y)] += y[: len(out) - i]
    return out


def write_wav(y, name):
    p = os.path.join(tempfile.mkdtemp(), name)
    sf.write(p, y, SR)
    return p


def read_midi(path):
    """Tiny MIDI reader -> (ppq, tempo, {track_name: [(start_tick, end_tick, pitch, vel, channel)]})."""
    d = open(path, "rb").read()
    assert d[:4] == b"MThd"
    fmt, ntr, ppq = struct.unpack(">HHH", d[8:14])
    pos, tempo, tracks = 14, None, {}
    for _ in range(ntr):
        assert d[pos : pos + 4] == b"MTrk"
        ln = struct.unpack(">I", d[pos + 4 : pos + 8])[0]
        body, pos = d[pos + 8 : pos + 8 + ln], pos + 8 + ln
        i, tick, name, on, notes, status = 0, 0, "", {}, [], 0
        while i < len(body):
            v = 0
            while True:
                b = body[i]
                i += 1
                v = (v << 7) | (b & 0x7F)
                if not b & 0x80:
                    break
            tick += v
            if body[i] == 0xFF:
                typ, ln2 = body[i + 1], body[i + 2]
                data = body[i + 3 : i + 3 + ln2]
                i += 3 + ln2
                if typ == 0x51:
                    tempo = int.from_bytes(data, "big")
                elif typ == 0x03:
                    name = data.decode()
                continue
            if body[i] & 0x80:
                status = body[i]
                i += 1
            kind, ch = status & 0xF0, status & 0x0F
            if kind == 0x90:
                on[(ch, body[i])] = (tick, body[i + 1])
                i += 2
            elif kind == 0x80:
                st, vel = on.pop((ch, body[i]))
                notes.append((st, tick, body[i], vel, ch))
                i += 2
            elif kind == 0xC0:
                i += 1
            else:
                raise AssertionError(f"unexpected status {status:#x}")
        assert not on, "note left hanging"
        tracks[name or f"t{len(tracks)}"] = sorted(notes)
    return ppq, tempo, tracks


def to_seconds(ticks, ppq, tempo):
    return ticks / ppq * tempo / 1e6


# ---------------------------------------------------------------------------


def test_midi_roundtrip_header_and_tempo():
    out = os.path.join(tempfile.mkdtemp(), "x.mid")
    tr = a2m.Track("Melody", [a2m.Note(0.0, 0.5, 60, 100), a2m.Note(0.5, 1.0, 64, 90)])
    a2m.write_midi(out, [tr], bpm=120)
    ppq, tempo, tracks = read_midi(out)
    assert ppq == 480 and tempo == 500000
    n = tracks["Melody"]
    assert [(x[2], x[3]) for x in n] == [(60, 100), (64, 90)]
    assert (n[0][0], n[0][1], n[1][0], n[1][1]) == (0, 480, 480, 960)  # 120 bpm: 0.5s = 1 beat


def test_melody_finds_each_note_with_right_timing():
    seq = [(60, 0.0), (64, 0.5), (67, 1.0), (72, 1.5), (55, 2.0)]
    y = place([(s, tone(m, 0.45)) for m, s in seq], 2.6)
    out = os.path.join(tempfile.mkdtemp(), "m.mid")
    a2m.convert(write_wav(y, "m.wav"), out, "melody", bpm=120)
    ppq, tempo, tracks = read_midi(out)
    got = tracks["Melody"]
    assert [g[2] for g in got] == [m for m, _ in seq]
    for g, (_, s) in zip(got, seq):
        assert abs(to_seconds(g[0], ppq, tempo) - s) < 0.06
        assert abs(to_seconds(g[1] - g[0], ppq, tempo) - 0.45) < 0.08


def test_melody_ignores_silence_and_noise_floor():
    y = place([(0.5, tone(69, 0.5))], 1.5) + np.random.default_rng(0).normal(0, 1e-4, int(1.5 * SR)).astype(np.float32)
    notes = a2m.audio_to_melody(*a2m.load_audio(write_wav(y, "s.wav")))
    assert [n.pitch for n in notes] == [69]


def test_bass_note_low_e():
    y = place([(0.1, tone(40, 0.6))], 1.0)  # E2, 82 Hz
    notes = a2m.audio_to_melody(*a2m.load_audio(write_wav(y, "b.wav")))
    assert [n.pitch for n in notes] == [40]


def test_chords_c_major_then_a_minor():
    c = [60, 64, 67]
    am = [57, 60, 64]
    y = place(
        [(0.0, sum(tone(m, 1.0) for m in c)), (1.2, sum(tone(m, 1.0) for m in am))],
        2.4,
    )
    notes = a2m.audio_to_chords(*a2m.load_audio(write_wav(y, "c.wav")))
    first = sorted(n.pitch for n in notes if n.start < 1.0)
    second = sorted(n.pitch for n in notes if n.start >= 1.0)
    assert first == c, first
    assert second == am, second
    # chord change should land near 1.2 s
    assert abs(min(n.start for n in notes if n.start >= 1.0) - 1.2) < 0.05


def test_chords_no_ghost_notes_on_single_note():
    y = place([(0.0, tone(48, 1.0))], 1.2)
    notes = a2m.audio_to_chords(*a2m.load_audio(write_wav(y, "one.wav")))
    assert sorted({n.pitch for n in notes}) == [48]


def _kick(sr=SR):
    t = np.arange(int(0.25 * sr)) / sr
    f = 50 + 100 * np.exp(-t * 40)
    return (np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t * 14)).astype(np.float32)


def _snare(sr=SR):
    rng = np.random.default_rng(1)
    t = np.arange(int(0.2 * sr)) / sr
    noise = rng.normal(0, 1, len(t)) * np.exp(-t * 25)
    return (0.6 * noise + 0.5 * np.sin(2 * np.pi * 200 * t) * np.exp(-t * 30)).astype(np.float32)


def _hat(dur, sr=SR):
    rng = np.random.default_rng(2)
    t = np.arange(int(dur * sr)) / sr
    n = rng.normal(0, 1, len(t))
    # crude high-pass: first difference twice
    n = np.diff(n, 2, prepend=[0, 0])
    return (0.25 * n * np.exp(-t * (6.0 / dur))).astype(np.float32)


def test_drums_kick_snare_hat_pattern():
    beat = 0.5
    parts = []
    expect = []
    for bar in range(2):
        b0 = bar * 4 * beat
        for i, kind in enumerate(["k", "h", "s", "h", "k", "h", "s", "h"]):
            t = b0 + i * beat / 2
            if kind == "k":
                parts.append((t, _kick()))
                expect.append((t, a2m.KICK))
            elif kind == "s":
                parts.append((t, _snare()))
                expect.append((t, a2m.SNARE))
            else:
                parts.append((t, _hat(0.05)))
                expect.append((t, a2m.CLOSED_HAT))
    y = place(parts, 4.3)
    notes = a2m.audio_to_drums(*a2m.load_audio(write_wav(y, "d.wav")))
    for t, pitch in expect:
        near = [n for n in notes if abs(n.start - t) < 0.04]
        assert any(n.pitch == pitch for n in near), (t, pitch, [(round(n.start, 3), n.pitch) for n in near])
    extras = [n for n in notes if not any(abs(n.start - t) < 0.04 for t, _ in expect)]
    assert not extras, [(round(n.start, 3), n.pitch) for n in extras]


def test_drums_open_hat_vs_closed():
    y = place([(0.2, _hat(0.04)), (1.0, _hat(0.4))], 2.0)
    notes = a2m.audio_to_drums(*a2m.load_audio(write_wav(y, "h.wav")))
    assert [n.pitch for n in notes] == [a2m.CLOSED_HAT, a2m.OPEN_HAT]


def test_kick_and_hat_together_gives_two_notes():
    # the lone hat at 0.8 s teaches the detector what a hat sounds like in this file
    hat = np.zeros(len(_kick()), dtype=np.float32)
    hat[:2205] = _hat(0.05)
    y = place([(0.3, _kick() + hat), (0.8, _hat(0.05))], 1.2)
    notes = a2m.audio_to_drums(*a2m.load_audio(write_wav(y, "kh.wav")))
    at = lambda t: sorted(n.pitch for n in notes if abs(n.start - t) < 0.04)
    assert at(0.3) == [a2m.KICK, a2m.CLOSED_HAT]
    assert at(0.8) == [a2m.CLOSED_HAT]


def test_quantize_snaps_to_grid_and_never_overlaps_same_pitch():
    bpm = 120  # 1/16 = 0.125 s
    notes = [a2m.Note(0.02, 0.26, 60, 90), a2m.Note(0.27, 0.40, 60, 90), a2m.Note(0.51, 0.75, 62, 90)]
    q = a2m.quantize_notes(notes, bpm, "1/16")
    for n in q:
        assert abs(n.start / 0.125 - round(n.start / 0.125)) < 1e-9
    same = sorted((n for n in q if n.pitch == 60), key=lambda n: n.start)
    assert same[0].end <= same[1].start


def test_all_mode_writes_three_tracks_and_drum_channel():
    y = place([(0.0, tone(60, 0.5)), (0.6, _kick())], 1.2)
    out = os.path.join(tempfile.mkdtemp(), "all.mid")
    a2m.convert(write_wav(y, "all.wav"), out, "all", sensitivity=0.7)
    _, _, tracks = read_midi(out)
    assert {"Melody", "Chords", "Drums"} <= set(tracks)
    assert all(n[4] == 9 for n in tracks["Drums"])


def test_errors_are_friendly():
    silent = write_wav(np.zeros(SR, dtype=np.float32), "z.wav")
    with pytest.raises(ValueError, match="silent"):
        a2m.convert(silent, silent + ".mid")
    ok = write_wav(tone(60, 0.5), "ok.wav")
    with pytest.raises(ValueError):
        a2m.convert(ok, ok + ".mid", mode="nope")
    with pytest.raises(ValueError):
        a2m.convert(ok, ok + ".mid", bpm=5)


# ---- stem separation (Demucs is replaced by a stand-in that writes 4 known stems) ----

FAKE_DEMUCS = '''
import os, sys
import numpy as np, soundfile as sf
a = sys.argv[1:]
out, path = a[a.index("-o") + 1], a[-1]
track = os.path.splitext(os.path.basename(path))[0]
d = os.path.join(out, "htdemucs", track)
os.makedirs(d, exist_ok=True)
sr = 44100
t = np.arange(sr) / sr
tone = lambda f: (0.3 * np.sin(2 * np.pi * f * t) * np.minimum(1, np.minimum(t / 0.01, (1 - t) / 0.03))).astype("float32")
kick = np.zeros(sr, dtype="float32")
tk = np.arange(int(0.25 * sr)) / sr
kick[int(0.5 * sr):int(0.5 * sr) + len(tk)] = np.sin(2 * np.pi * np.cumsum(50 + 100 * np.exp(-tk * 40)) / sr) * np.exp(-tk * 14)
for name, y in {"vocals": tone(440.0), "bass": tone(82.41), "drums": kick,
                "other": sum(tone(f) for f in (261.63, 329.63, 392.0)) / 1.0}.items():
    sf.write(os.path.join(d, name + ".wav"), y.astype("float32"), sr)
'''


@pytest.fixture
def fake_demucs(tmp_path, monkeypatch):
    pkg = tmp_path / "fakepkgs" / "demucs"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "__main__.py").write_text(FAKE_DEMUCS)
    monkeypatch.syspath_prepend(str(tmp_path / "fakepkgs"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "fakepkgs") + os.pathsep + os.environ.get("PYTHONPATH", ""))


def test_song_mode_splits_and_converts_each_stem(fake_demucs):
    src = write_wav(tone(60, 1.0), "song.wav")
    out = os.path.join(tempfile.mkdtemp(), "song.mid")
    a2m.convert(src, out, "song")
    _, _, tracks = read_midi(out)
    assert set(tracks) - {"t0"} == {"Vocals", "Bass", "Chords", "Drums"}  # t0 = unnamed tempo track
    assert [n[2] for n in tracks["Vocals"]] == [69]  # A4
    assert [n[2] for n in tracks["Bass"]] == [40]  # E2
    assert sorted({n[2] for n in tracks["Chords"]}) == [60, 64, 67]
    assert [n[2] for n in tracks["Drums"]] == [a2m.KICK]
    assert {n[4] for n in tracks["Vocals"]} == {0} and {n[4] for n in tracks["Bass"]} == {1}
    assert {n[4] for n in tracks["Drums"]} == {9}


def test_song_mode_cleans_up_temp_files(fake_demucs, monkeypatch):
    made = []
    real = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: made.append(real(*a, **k)) or made[-1])
    src = write_wav(tone(60, 1.0), "song2.wav")
    a2m.convert(src, src + ".mid", "song")
    assert any("audio2midi_stems_" in m for m in made)
    assert not any(os.path.exists(m) for m in made if "audio2midi_stems_" in m)


def test_song_mode_without_demucs_gives_install_hint(monkeypatch):
    import stems

    monkeypatch.setattr(stems, "available", lambda: False)
    src = write_wav(tone(60, 1.0), "nodemucs.wav")
    with pytest.raises(RuntimeError, match="pip install -r requirements-stems.txt"):
        a2m.convert(src, src + ".mid", "song")


# ---- full-mix drums setting ----

def _tom(sr=SR):
    t = np.arange(int(0.3 * sr)) / sr
    return (0.7 * np.sin(2 * np.pi * 170 * t) * np.exp(-t * 12)).astype(np.float32)


def test_full_mix_drums_skips_toms_and_open_hats_but_keeps_kick_snare():
    y = place([(0.2, _kick()), (0.9, _tom()), (1.6, _hat(0.4)), (2.3, _snare()), (3.0, _hat(0.04))], 3.6)
    x, sr = a2m.load_audio(write_wav(y, "fm.wav"))
    normal = {n.pitch for n in a2m.audio_to_drums(x, sr)}
    mixed = a2m.audio_to_drums(x, sr, full_mix=True)
    assert normal & {a2m.TOM_LOW, a2m.TOM_MID, a2m.TOM_HIGH}, normal  # the tom is heard normally
    assert a2m.OPEN_HAT in normal
    got = {n.pitch for n in mixed}
    assert not got & {a2m.TOM_LOW, a2m.TOM_MID, a2m.TOM_HIGH, a2m.OPEN_HAT, a2m.CRASH}, got
    assert {a2m.KICK, a2m.SNARE, a2m.CLOSED_HAT} <= got
    # the open hat is still written, just as a closed hat
    assert sum(1 for n in mixed if n.pitch == a2m.CLOSED_HAT) == 2


def test_convert_passes_full_mix_flag(tmp_path):
    y = place([(0.2, _kick()), (0.9, _tom()), (1.6, _snare())], 2.4)
    src = write_wav(y, "fm2.wav")
    tr = a2m.convert(src, str(tmp_path / "a.mid"), "drums", full_mix_drums=True)
    assert not {n.pitch for n in tr[0].notes} & {a2m.TOM_LOW, a2m.TOM_MID, a2m.TOM_HIGH}
    tr = a2m.convert(src, str(tmp_path / "b.mid"), "drums")
    assert {n.pitch for n in tr[0].notes} & {a2m.TOM_LOW, a2m.TOM_MID, a2m.TOM_HIGH}


def test_cli_flag(tmp_path):
    src = write_wav(place([(0.2, _kick()), (0.9, _tom())], 1.6), "cli.wav")
    out = str(tmp_path / "cli.mid")
    assert a2m.main([src, "-m", "drums", "--full-mix-drums", "-o", out]) == 0
    _, _, tracks = read_midi(out)
    assert {n[2] for n in tracks["Drums"]} == {a2m.KICK}
