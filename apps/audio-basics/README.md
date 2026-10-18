# Audio Basics (Python)

A commented listening tour of `solaros.synth`. Open `audio.py` to follow the
direct API calls while listening. The sequence demonstrates:

1. Sine, triangle, saw, square, and noise waveforms.
2. The amplifier's attack, decay, sustain, and release (ADSR) envelope.
3. A three-note chord, note velocity, and synth status.
4. A second oscillator with detuning.
5. A low-pass cutoff sweep and a separate filter envelope.
6. Monophonic last-note priority and pitch glide.

Install and run from the Playground **Examples** category, or use:

```text
playground install audio-basics
audio level 30
playground run audio-basics
```

The sequence plays once in about 13 seconds and returns to the shell. Press
**Ctrl+]** (the SolarOS app-exit key) to stop early. There are no arguments or
other controls. Labels print to the terminal; no graphics display is required.
The synth uses the current global speaker volume and does not change it.

Requires SolarOS **4.6.15+**, the Python runtime (`app.python`), PSRAM, an audio
output, and **`service.synth`** in the installed firmware. The manifest's
`audio` capability does not guarantee that the synth service is compiled in;
the script checks for its API before playing. No assets, microphone, MIDI
device, network connection, or additional Python modules are needed.

Close other audio applications before running: the synth claims exclusive
audio output on its first note. `note_off()` and `all_notes_off()` allow
release envelopes to finish; `stop()` immediately silences and releases the
output. A `finally` block calls `stop()` on completion, interruption, or error.
The example sets its sound configuration explicitly so repeated runs start
with the same patch. Scheduling uses cooperative waits; it is not intended
as a sample-accurate sequencer.

See the SolarOS manual's **Python audio and control API** (`man python.audio`)
for the complete synth API and status fields.
