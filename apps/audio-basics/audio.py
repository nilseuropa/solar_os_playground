"""A listening tour of solaros.synth. Read main() for the API examples.

The native synth renders the audio; Python only configures and schedules notes.
No sound files, MIDI hardware, graphics, or external Python modules are needed.
"""

import solaros


def wait(duration_ms):
    # Yield often so the OS can process events and its app-exit key (Ctrl+]).
    # These waits schedule a demonstration, not sample-accurate musical timing.
    while duration_ms > 0:
        if solaros.should_exit():
            raise KeyboardInterrupt
        step = min(25, duration_ms)
        solaros.time.sleep_ms(step)
        duration_ms -= step


def main():
    # Python alone does not guarantee that the optional synth package exists.
    synth = getattr(solaros, "synth", None)
    functions = ("status", "configure", "configure_oscillator2",
                 "configure_filter", "configure_performance", "note_on",
                 "note_off", "all_notes_off", "stop")
    if synth is None or any(getattr(synth, name, None) is None
                            for name in functions):
        print("Audio Basics needs SolarOS 4.6.15+ with service.synth and Python.")
        return

    status = synth.status()
    if status["owner"]:
        print("Synth is in use by:", status["owner"])
        print("Close that audio application before running Audio Basics.")
        return

    print("Audio Basics - listen to each numbered example.")
    print("Uses the global speaker level; set it with: audio level 30")
    print("Ctrl+] exits early. The sequence also finishes automatically.")

    try:
        # Set EVERY sound component we depend on: configuration persists between
        # notes, and configure() preserves oscillator 2 and the filter settings.
        # A zero oscillator-2 mix disables it. A high cutoff opens the filter.
        synth.configure_oscillator2("sine", 0, 0, 0)
        synth.configure_filter(18000, 0, 0, 0, 0, 100, 0)
        synth.configure_performance(False, 0)  # Polyphonic, no pitch glide.

        print("1. Waveforms: sine, triangle, saw, square, noise")
        for waveform in ("sine", "triangle", "saw", "square", "noise"):
            print("  ", waveform)
            # configure(waveform, attack_ms, decay_ms, sustain_%, release_ms)
            # Times are 0..10000 ms; sustain is 0..100 percent of peak level.
            synth.configure(waveform, 10, 80, 60, 120)
            # note_on(frequency_hz, velocity) starts sound and returns immediately.
            # Frequencies are integer Hz (20..8000), NOT MIDI note numbers.
            # Velocity is 1..127; it scales this note, not the global volume.
            # The first note_on() claims exclusive audio output lazily. Another
            # audio owner can make it fail with OSError; finally still cleans up.
            # Noise has no definite musical pitch, despite the frequency argument.
            synth.note_on(440, 80)
            wait(350)
            # Match the exact frequency used for note_on(). This starts release;
            # it does not silence the note immediately or relinquish the output.
            synth.note_off(440)
            wait(180)

        print("2. ADSR: slow attack, decay, sustain, long release")
        # Attack rises to peak; decay falls to sustain. Sustain lasts until
        # note_off(), which fades to silence over release_ms.
        synth.configure("triangle", 350, 250, 35, 650)
        synth.note_on(330, 100)
        wait(1100)
        synth.note_off(330)
        wait(750)  # Let the release finish before changing the patch.

        print("3. Polyphony: C major chord")
        synth.configure("sine", 15, 100, 65, 250)
        # Multiple note_on() calls overlap, up to eight voices. Frequencies here
        # approximate C4, E4, G4. Keep velocity modest when stacking notes.
        synth.note_on(262, 65)
        synth.note_on(330, 65)
        synth.note_on(392, 65)
        wait(700)
        # status() returns a dictionary: inspect voices, ownership, sample rate,
        # and error/renderer telemetry without capturing or generating PCM here.
        status = synth.status()
        print("  Voices:", status["active_voices"], "/", status["max_voices"])
        print("  Sample rate:", status["sample_rate"], "Hz")
        # all_notes_off() releases every voice using its envelope, unlike stop().
        synth.all_notes_off()
        wait(350)

        print("4. Second oscillator: compare plain and detuned saw")
        synth.configure("saw", 15, 80, 55, 180)
        synth.note_on(220, 80)
        wait(600)
        # configure_oscillator2(waveform, octave, detune_cents, mix_%)
        # Octave: -2..2; detune: -100..100 cents; mix: 0..100 percent.
        # These changes affect sounding voices too. Seven cents adds beating.
        synth.configure_oscillator2("saw", 0, 7, 35)
        wait(900)
        synth.note_off(220)
        wait(280)
        synth.configure_oscillator2("sine", 0, 0, 0)

        print("5. Low-pass filter: dark to bright, then filter envelope")
        synth.configure("saw", 10, 80, 60, 200)
        # configure_filter(cutoff_hz, resonance_%, envelope_amount_%,
        #                  attack_ms, decay_ms, sustain_%, release_ms)
        # Cutoff: 40..18000 Hz; percentages: 0..100; times: 0..10000 ms.
        # With envelope amount zero, only cutoff and resonance shape the sound.
        synth.configure_filter(300, 25, 0, 0, 0, 100, 0)
        synth.note_on(220, 80)
        for cutoff in (300, 600, 1200, 2400, 6000):
            synth.configure_filter(cutoff, 25, 0, 0, 0, 100, 0)
            wait(250)
        synth.note_off(220)
        wait(300)
        # The filter has its OWN ADSR: positive envelope amount opens the filter
        # on note attack, then decays towards its filter sustain setting.
        synth.configure_filter(500, 25, 70, 10, 450, 10, 200)
        synth.note_on(220, 80)
        wait(900)
        synth.all_notes_off()
        wait(300)
        synth.configure_filter(18000, 0, 0, 0, 0, 100, 0)

        print("6. Mono and glide: last held note wins")
        synth.configure("triangle", 10, 80, 65, 200)
        # configure_performance(mono, glide_ms), with glide in 0..2500 ms.
        # Keep earlier notes held: each new note retargets one monophonic voice.
        synth.configure_performance(True, 180)
        synth.note_on(220, 85)
        wait(400)
        synth.note_on(330, 85)
        wait(400)
        synth.note_on(440, 85)
        wait(400)
        synth.note_off(440)  # Returns to the still-held 330 Hz note.
        wait(400)
        synth.note_off(330)  # Returns to the still-held 220 Hz note.
        wait(400)
        synth.all_notes_off()
        wait(300)
        print("Finished.")
    except KeyboardInterrupt:
        print("Audio Basics interrupted.")
    except OSError as error:
        print("Synth/audio error:", error)
        print("Check audio output and close other audio applications.")
        raise
    finally:
        # Always stop, including during a sustained note or an API failure.
        # stop() silences immediately AND releases ownership for the next app.
        # Do not call audio.deinit(): the audio hardware is managed by the OS.
        try:
            synth.stop()
        except OSError as error:
            # An unrelated owner may have won an audio claim; do not hide the
            # original exception if stop() refuses to touch that owner's synth.
            print("Synth cleanup:", error)


main()
