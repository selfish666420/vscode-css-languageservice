"""Little window for audio2midi. Run:  python gui.py"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import audio2midi as a2m

MODE_LABELS = {
    "Melody  (voice, bass, lead synth - one note at a time)": "melody",
    "Chords  (piano, guitar, pads - many notes at once)": "chords",
    "Drums  (kick, snare, hats...)": "drums",
    "Everything  (all three, as separate tracks)": "all",
    "Full song  (splits into vocals / bass / drums / other first - slow, needs extra install)": "song",
}
AUDIO_TYPES = [("Audio files", "*.wav *.flac *.ogg *.mp3 *.aiff *.aif"), ("All files", "*.*")]


class App(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=14)
        root.title("Audio to MIDI  (for FL Studio)")
        root.minsize(560, 560)
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(6, weight=1)

        self.in_path = tk.StringVar()
        self.mode = tk.StringVar(value=next(iter(MODE_LABELS)))
        self.bpm = tk.StringVar(value="130")
        self.quant = tk.StringVar(value="off")
        self.sens = tk.DoubleVar(value=0.5)
        self.full_mix = tk.BooleanVar(value=False)
        self.msgs: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.last_out = ""

        f1 = ttk.LabelFrame(self, text="1. Pick your sound file", padding=8)
        f1.grid(row=0, sticky="ew", pady=4)
        f1.columnconfigure(0, weight=1)
        ttk.Entry(f1, textvariable=self.in_path).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(f1, text="Browse...", command=self.browse).grid(row=0, column=1)

        f2 = ttk.LabelFrame(self, text="2. What kind of sound is it?", padding=8)
        f2.grid(row=1, sticky="ew", pady=4)
        for label in MODE_LABELS:
            ttk.Radiobutton(f2, text=label, variable=self.mode, value=label).grid(sticky="w")

        f3 = ttk.LabelFrame(self, text="3. Settings", padding=8)
        f3.grid(row=2, sticky="ew", pady=4)
        f3.columnconfigure(1, weight=1)
        ttk.Label(f3, text="Your FL Studio tempo (BPM):").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Spinbox(f3, from_=20, to=400, increment=1, textvariable=self.bpm, width=8).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(f3, text="Snap notes to grid:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(f3, textvariable=self.quant, values=a2m.QUANTIZE_CHOICES, state="readonly", width=8).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(f3, text="Sensitivity:").grid(row=2, column=0, sticky="w", pady=2)
        sens_row = ttk.Frame(f3)
        sens_row.grid(row=2, column=1, sticky="ew", padx=6)
        sens_row.columnconfigure(1, weight=1)
        ttk.Label(sens_row, text="fewer notes").grid(row=0, column=0)
        ttk.Scale(sens_row, from_=0, to=1, variable=self.sens).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(sens_row, text="more notes").grid(row=0, column=2)

        ttk.Checkbutton(
            f3,
            text="Drums come from a whole song (ignore toms, open hats, crash)",
            variable=self.full_mix,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        f4 = ttk.Frame(self)
        f4.grid(row=3, sticky="ew", pady=8)
        f4.columnconfigure(0, weight=1)
        self.go = ttk.Button(f4, text="4. Convert to MIDI", command=self.convert)
        self.go.grid(row=0, column=0, sticky="ew", ipady=6)
        self.bar = ttk.Progressbar(self, mode="indeterminate")
        self.bar.grid(row=4, sticky="ew")

        ttk.Label(self, text="What's happening:").grid(row=5, sticky="w", pady=(8, 2))
        self.log = tk.Text(self, height=9, state="disabled", wrap="word")
        self.log.grid(row=6, sticky="nsew")
        self.show = ttk.Button(self, text="Show the MIDI file in its folder", command=self.show_file, state="disabled")
        self.show.grid(row=7, sticky="ew", pady=(8, 0))
        self.after(100, self.poll)

    # -- helpers ---------------------------------------------------------
    def say(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def browse(self) -> None:
        p = filedialog.askopenfilename(title="Choose audio", filetypes=AUDIO_TYPES)
        if p:
            self.in_path.set(p)

    def show_file(self) -> None:
        if not self.last_out or not os.path.exists(self.last_out):
            return
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(self.last_out)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", self.last_out])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(self.last_out)])

    # -- conversion ------------------------------------------------------
    def convert(self) -> None:
        src = self.in_path.get().strip().strip('"')
        if not src or not os.path.isfile(src):
            messagebox.showwarning("Pick a file", "Please choose a sound file first (step 1).")
            return
        try:
            bpm = float(self.bpm.get())
        except ValueError:
            messagebox.showwarning("Tempo", "The tempo must be a number, like 130.")
            return
        out = filedialog.asksaveasfilename(
            title="Save MIDI as",
            defaultextension=".mid",
            initialfile=os.path.splitext(os.path.basename(src))[0] + ".mid",
            initialdir=os.path.dirname(src),
            filetypes=[("MIDI file", "*.mid")],
        )
        if not out:
            return
        mode, quant, sens = MODE_LABELS[self.mode.get()], self.quant.get(), float(self.sens.get())
        full_mix = bool(self.full_mix.get())
        self.go.configure(state="disabled")
        self.show.configure(state="disabled")
        self.bar.start(12)
        self.say("-" * 30)

        def work() -> None:
            try:
                a2m.convert(src, out, mode, bpm, quant, sens, log=lambda m: self.msgs.put(("log", m)), full_mix_drums=full_mix)
                self.msgs.put(("done", out))
            except Exception as e:  # noqa: BLE001 - shown to the user
                self.msgs.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def poll(self) -> None:
        try:
            while True:
                kind, payload = self.msgs.get_nowait()
                if kind == "log":
                    self.say(str(payload))
                else:
                    self.bar.stop()
                    self.go.configure(state="normal")
                    if kind == "done":
                        self.last_out = str(payload)
                        self.show.configure(state="normal")
                        self.say("Done! Drag the .mid file into FL Studio.")
                        messagebox.showinfo("Done", "Your MIDI file is ready.\n\nDrag it into FL Studio's Playlist or Channel rack.")
                    else:
                        self.say("Problem: " + str(payload))
                        messagebox.showerror("Could not convert", str(payload))
        except queue.Empty:
            pass
        self.after(100, self.poll)


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
