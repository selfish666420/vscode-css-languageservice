# Audio to MIDI (for FL Studio)

Turn a sound file into a **MIDI file** you can drag into FL Studio.

Think of it like this: you hum a tune into a recorder, and this program
*listens* to the recording and writes down which piano keys you were singing.
Those "written down keys" are the MIDI file. Then FL Studio can play them with
any instrument you like.

It can listen for three kinds of sound:

| You pick...  | Use it for...                                  | What you get                          |
|--------------|------------------------------------------------|---------------------------------------|
| **Melody**   | a voice, bass, a lead synth (one note at a time) | one note after another                |
| **Chords**   | piano, guitar, pads (many notes at once)        | stacked notes                         |
| **Drums**    | a drum loop                                     | kick, snare, hats, toms, crash        |
| **Everything** | a bit of all of it                            | all three, each on its own track      |
| **Full song**  | a finished song (needs the extra install below) | splits it into vocals / bass / drums / other first, then converts each part with the right mode |

> **This is a standalone program, not a plugin that lives inside FL Studio.**
> You run it next to FL Studio, make a `.mid` file, then drag that file in.
> That is the most reliable way to do it, and it works the same in every
> version of FL Studio.

---

## Step 1 - Install Python (one time only)

Python is the language this program is written in. Your computer needs it once.

1. Go to <https://www.python.org/downloads/> and click the big yellow **Download Python** button.
2. Open the file you downloaded.
3. **Important:** at the bottom of the first screen, tick the box **"Add python.exe to PATH"**.
4. Click **Install Now**, and wait for it to finish.

## Step 2 - Start the program

1. Open the `audio2midi` folder.
2. Double-click **`run.bat`**.
3. The first time, a black window appears for a minute while it downloads some helper
   pieces (it needs the internet for this). Then the program window opens.
   Next times it opens straight away.

> If Windows says *"Windows protected your PC"*, click **More info** then **Run anyway**.
> The file is a short text script you can open in Notepad and read.

## Step 3 - Make your MIDI file

In the program window, go from top to bottom:

1. **Pick your sound file.** Click *Browse* and choose a `.wav`, `.mp3`, `.flac` or `.ogg`.
2. **Say what kind of sound it is** (melody, chords, drums or everything).
3. **Settings**
   - **Tempo (BPM):** type the same number as your FL Studio project
     (the big number at the top of FL Studio; the default is 130).
     This is what makes the notes land in the right place on FL Studio's grid.
   - **Snap to grid:** `off` keeps the exact timing of the recording.
     `1/16` nudges every note onto the nearest sixteenth-note line, which is
     tidier for electronic music.
   - **Drums come from a whole song:** tick this when the sound is a finished song
     (not an isolated drum loop). The program then only writes kick, snare and hi-hat,
     because in a full mix it often mistakes vocals and synths for toms and open hats.
   - **Sensitivity:** slide right if notes are missing, left if there are
     too many wrong little notes.
4. Click **Convert to MIDI**, choose where to save, and wait. A 3-minute song takes only a few seconds.
5. When you see **Done!**, you are finished with this program.

## Step 4 - Put it in FL Studio

1. Open FL Studio.
2. Drag the `.mid` file from your folder into the FL Studio window
   (or use *File > Import > MIDI file*).
3. Each track becomes a channel in the Channel Rack. Click a channel's name
   to open the Piano roll and see the notes.
4. Load any instrument on that channel and press play.

**Tip for sound that starts on the beat:** if you want the notes to line up with
FL Studio's grid, make sure the recording starts right on beat 1 (cut off any
silence at the start), and that the BPM you typed is the project's BPM.

**Drums:** notes use the standard "General MIDI" numbers:

| Sound        | MIDI number | Where it sits in FL's Piano roll (middle C is shown as C5) |
|--------------|-------------|---------------|
| Kick         | 36          | C3            |
| Snare        | 38          | D3            |
| Closed hat   | 42          | F#3           |
| Open hat     | 46          | A#3           |
| Crash        | 49          | C#4           |
| Toms         | 43, 47, 50  | G3, B3, D4    |

Different drum plugins put their sounds on different keys. If the wrong sound plays,
select all the notes in the Piano roll and drag them up or down until they match.

---

## Be honest: how good is it?

Computers listening to music is hard. It is good, not magic.

- **Melody:** very good on a single clean voice or instrument, including bass.
  Wobbly (vibrato) singing is handled. It will not invent notes in silence.
- **Chords:** decent on clean recordings of one instrument. The more instruments playing
  and the more reverb, the more mistakes. Very low notes (below about C2) are the
  hardest to hear correctly. Expect to fix a few notes by hand.
- **Drums:** best on a drum loop *by itself*. In a full song, bass and other sounds get
  mixed up with the kick and the snare. If a snare and a hat hit at the very same instant,
  only the snare is written down. For drums taken from a whole song, tick
  **Drums come from a whole song** to avoid invented toms and open hats.
- It does not copy pitch bends, slides or how long a note rings in a fancy way.
  Everything is plain notes with a loudness.
- It does not guess the tempo for you. You type it.

If you have a full song, use **Full song** mode (see below) so each part is converted on its own.
Results are far better that way.

## Full songs: splitting into parts first (optional, slow)

A full song has everything mixed together, which confuses the converter. "Full song"
mode first pulls the song apart into four parts with a free tool called **Demucs**,
then listens to each part on its own:

| Part   | Converted as | MIDI track |
|--------|--------------|------------|
| Vocals | melody       | Vocals     |
| Bass   | melody (low) | Bass       |
| Other (guitars, keys, synths) | chords | Chords |
| Drums  | drums        | Drums      |

To turn it on (one time), open a Command Prompt in the `audio2midi` folder and type:

```
pip install -r requirements-stems.txt
```

This is a **big download (a few GB)** because it brings PyTorch. The first run also
downloads the splitting model. A 3-minute song takes a few minutes on a normal PC
(much faster with an NVIDIA graphics card). It only works with `run.bat`, not the
`.exe`. You can keep using the other modes without installing it.

> Honest note: this part is wired up and tested with a stand-in splitter, but I could
> not download the real Demucs model where I built it, so I have **not** tried it on a
> real song. If it fails on your computer, the error message will say why.

## Using it from the command line (optional)

```
python audio2midi.py my_loop.wav -m drums --bpm 128 -q 1/16
python audio2midi.py my_vocal.mp3 -m melody -s 0.6 -o vocal.mid
```

- `-m` is `melody`, `chords`, `drums`, `all` or `song`
- `--bpm` is your project tempo
- `-q` is `off`, `1/4`, `1/8`, `1/16` or `1/32`
- `-s` is sensitivity from `0` to `1`
- `--full-mix-drums` is the same as the tick box above

## Make a real `.exe` (optional)

If you want a program you can double-click without Python: double-click
**`build_exe.bat`**. After a few minutes you will have `dist\Audio2MIDI.exe`.

## For developers

```
pip install -r requirements-dev.txt
pytest
```

The tests make their own sounds with known notes (a melody, bass, two chords, a drum
pattern, open and closed hats) and check that the notes come back out.

| File               | What it is                                        |
|--------------------|---------------------------------------------------|
| `audio2midi.py`    | the engine (pitch tracking, chords, drums, MIDI writer) |
| `stems.py`         | optional song splitter (Demucs)                   |
| `gui.py`           | the window                                        |
| `test_audio2midi.py` | the tests                                       |
| `run.bat`          | double-click launcher for Windows                 |
| `build_exe.bat`    | builds `Audio2MIDI.exe`                           |
