"""Core step-sequencer engine for PI-SEQ.

A thread ticks 16th notes, fires probability-gated notes (optionally random
notes from a scale) over USB MIDI (mido / python-rtmidi), and replays a simple
parameter-automation timeline recorded from the UI.

v0.2: swing, multiple patterns, song mode.
"""
import random
import threading
import time

import mido

from .scales import SCALES

STEPS_PER_BAR = 16
MAX_BARS = 32
DEFAULT_BARS = 2
NUM_PATTERNS = 4
SONG_MAX = 16
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n):
    n = max(0, min(127, int(n)))
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


class Track:
    """One sequencer lane: 32 bars x 16 steps, each step = [on, probability, note].

    note is None -> use the track's fixed note / scale-random logic.
    note is an int -> that exact pitch plays for this step (piano roll).
    """

    def __init__(self, name, channel, note, velocity=100):
        self.name = name
        self.channel = channel            # 0-based MIDI channel
        self.note = note                  # fixed note / scale root
        self.velocity = velocity
        self.mode = "fixed"               # "fixed" | "scale"
        self.scale = "minor_pentatonic"
        self.steps = [[[False, 100, None] for _ in range(STEPS_PER_BAR)] for _ in range(MAX_BARS)]

    def randomize(self, density=0.4):
        probs = [100, 100, 100, 80, 60, 50]
        for b in range(MAX_BARS):
            for s in range(STEPS_PER_BAR):
                on = random.random() < density
                self.steps[b][s] = [on, random.choice(probs) if on else 100, None]


class Pattern:
    """A pattern = 4 tracks of step data + its own bar length."""

    def __init__(self, name="P"):
        self.name = name
        self.pattern_length = DEFAULT_BARS
        self.tracks = [
            Track("KICK", 0, 36, 120),
            Track("SNARE", 1, 38, 110),
            Track("HAT", 2, 42, 90),
            Track("BASS", 3, 40, 105),
        ]


class Sequencer:
    def __init__(self, midi_port=None, virtual=True, bpm=120, bars=DEFAULT_BARS):
        self.bpm = bpm
        self.swing = 0.0                  # 0-100 %: delays offbeat 16ths
        self.patterns = [Pattern(f"P{i + 1}") for i in range(NUM_PATTERNS)]
        for p in self.patterns:
            p.pattern_length = bars
        self.current_pattern = 0
        self.edit_bar = 0

        self.playing = False
        self.current_bar = 0
        self.current_step = 0
        self.recording = False
        self.humanize_time = 0.0      # ms of timing jitter
        self.humanize_velocity = 0    # 0-100 %
        self.note_length = 0.85       # fraction of a step a note sounds
        self.automation = []          # [(loop_time, param, value), ...]
        self._automation_idx = 0
        self._loop_start = 0.0
        self._note_offs = {}          # channel -> (note, release_time)

        # song mode: ordered list of pattern indices, each plays its own length
        self.song = [0] * SONG_MAX
        self.song_len = 4
        self.song_on = False
        self._song_entry = 0

        self._seed_default_pattern()

        self._state_listeners = []
        self._step_listeners = []
        self._midi_port_name = midi_port
        self._virtual = virtual
        self.out = None
        self.port_label = "none"
        self._open_midi()
        self._thread = None
        self._stop_evt = threading.Event()

    # ---------------------------------------------------------------- listeners
    def set_state_listener(self, fn):
        self._state_listeners.append(fn)

    def set_step_listener(self, fn):
        self._step_listeners.append(fn)

    def notify_state(self):
        for fn in self._state_listeners:
            try:
                fn()
            except Exception:
                pass

    def _notify_step(self):
        for fn in self._step_listeners:
            try:
                fn(self.current_bar, self.current_step)
            except Exception:
                pass

    # ------------------------------------------------------------------ access
    @property
    def tracks(self):
        return self.patterns[self.current_pattern].tracks

    @property
    def pattern_length(self):
        return self.patterns[self.current_pattern].pattern_length

    # -------------------------------------------------------------------- midi
    def _open_midi(self):
        try:
            if self._midi_port_name:
                self.out = mido.open_output(self._midi_port_name)
                self.port_label = self._midi_port_name
            elif self._virtual:
                self.out = mido.open_output("PI-Sequencer", virtual=True)
                self.port_label = "virtual: PI-Sequencer"
            else:
                names = mido.get_output_names()
                if names:
                    self.out = mido.open_output(names[0])
                    self.port_label = names[0]
                else:
                    self.port_label = "none (no MIDI output found)"
        except Exception as e:
            self.out = None
            self.port_label = f"error: {e}"

    def _send_note_on(self, channel, note, vel):
        if self.out is None:
            return
        try:
            self.out.send(mido.Message("note_on", channel=channel, note=note, velocity=vel))
        except Exception:
            pass

    def _send_note_off(self, channel, note):
        if self.out is None:
            return
        try:
            self.out.send(mido.Message("note_off", channel=channel, note=note, velocity=0))
        except Exception:
            pass

    def set_midi_out(self, name):
        """Re-open the MIDI output port at runtime (None closes it)."""
        if self.out is not None:
            try:
                self.out.close()
            except Exception:
                pass
            self.out = None
        if name:
            try:
                self.out = mido.open_output(name)
                self.port_label = name
            except Exception as e:
                self.out = None
                self.port_label = f"error: {e}"
        else:
            self.port_label = "closed"
        self.notify_state()

    def _all_notes_off(self):
        for ch, (note, _until) in self._note_offs.items():
            self._send_note_off(ch, note)
        self._note_offs.clear()

    # --------------------------------------------------------------- transport
    def _step_duration(self):
        return 60.0 / self.bpm / 4.0

    def _loop_duration(self):
        if self.song_on and self.song_len > 0:
            total_bars = sum(
                self.patterns[self.song[i]].pattern_length for i in range(self.song_len)
            )
            return self._step_duration() * STEPS_PER_BAR * total_bars
        return self._step_duration() * STEPS_PER_BAR * self.pattern_length

    @staticmethod
    def _is_offbeat(n):
        return n % 2 == 1

    def toggle_play(self):
        if self.playing:
            self.stop()
        else:
            self.play()

    def play(self):
        if self.playing:
            return
        self.playing = True
        if self.song_on and self.song_len > 0:
            self._song_entry = 0
            self.current_pattern = self.song[0]
        self.current_bar = 0
        self.current_step = 0
        self._automation_idx = 0
        self._loop_start = time.monotonic()
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._thread.start()
        self.notify_state()

    def stop(self):
        self.playing = False
        self._stop_evt.set()
        self._all_notes_off()
        self.notify_state()

    # -------------------------------------------------------------- tick loop
    def _tick_loop(self):
        t0 = time.monotonic()
        n = 0
        step_dur = self._step_duration()
        while self.playing:
            self._play_step(self.current_bar, self.current_step)
            self._notify_step()
            self._advance()
            n += 1
            ideal = t0 + n * step_dur
            if self._is_offbeat(n):
                ideal += (self.swing / 100.0) * step_dur
            if self.humanize_time > 0:
                ideal += random.uniform(-self.humanize_time, self.humanize_time) / 1000.0
            while self.playing:
                rem = ideal - time.monotonic()
                if rem <= 0:
                    break
                time.sleep(min(rem, 0.002))

    def _advance(self):
        self.current_step += 1
        if self.current_step >= STEPS_PER_BAR:
            self.current_step = 0
            if self.song_on and self.song_len > 0:
                self._song_entry = (self._song_entry + 1) % self.song_len
                self.current_pattern = self.song[self._song_entry]
                self.current_bar = 0
            else:
                self.current_bar = (self.current_bar + 1) % self.pattern_length

    def _play_step(self, bar, step):
        now = time.monotonic()
        step_dur = self._step_duration()
        # release notes whose duration has elapsed
        for ch, (note, until) in list(self._note_offs.items()):
            if now >= until:
                self._send_note_off(ch, note)
                del self._note_offs[ch]
        for tr in self.tracks:
            on, prob, step_note = tr.steps[bar][step]
            if not on:
                continue
            if random.random() * 100.0 >= prob:
                continue
            note = step_note if step_note is not None else self._pick_note(tr)
            vel = self._pick_velocity(tr)
            self._send_note_on(tr.channel, note, vel)
            self._note_offs[tr.channel] = (note, now + step_dur * self.note_length)
        self._apply_automation(now)

    def _pick_note(self, tr):
        if tr.mode == "scale":
            intervals = SCALES.get(tr.scale, SCALES["minor_pentatonic"])
            note = tr.note + random.choice(intervals)
        else:
            note = tr.note
        return max(0, min(127, note))

    def _pick_velocity(self, tr):
        vel = tr.velocity
        if self.humanize_velocity > 0:
            vj = self.humanize_velocity / 100.0
            vel = int(tr.velocity * (1.0 - vj / 2.0 + random.random() * vj))
        return max(1, min(127, vel))

    # ------------------------------------------------------------- automation
    def record_param(self, param, value):
        if not (self.playing and self.recording):
            return
        loop_dur = self._loop_duration()
        if loop_dur <= 0:
            return
        loop_t = (time.monotonic() - self._loop_start) % loop_dur
        self.automation.append((loop_t, param, value))
        self.automation.sort(key=lambda e: e[0])
        self.notify_state()

    def clear_automation(self):
        self.automation = []
        self._automation_idx = 0
        self.notify_state()

    def _apply_automation(self, now):
        if not self.automation:
            return
        loop_dur = self._loop_duration()
        if loop_dur <= 0:
            return
        loop_t = (now - self._loop_start) % loop_dur
        first_t = self.automation[0][0]
        if loop_t < first_t:
            self._automation_idx = 0
        while self._automation_idx < len(self.automation):
            ev_t, param, value = self.automation[self._automation_idx]
            if ev_t > loop_t:
                break
            self._set_param(param, value)
            self._automation_idx += 1

    def _set_param(self, param, value):
        changed = False
        if param == "bpm":
            self.bpm = max(40, min(300, int(value)))
            changed = True
        elif param.startswith("vel:"):
            i = int(param.split(":")[1])
            if 0 <= i < len(self.tracks):
                self.tracks[i].velocity = max(1, min(127, int(value)))
                changed = True
        elif param.startswith("note:"):
            i = int(param.split(":")[1])
            if 0 <= i < len(self.tracks):
                self.tracks[i].note = max(0, min(127, int(value)))
                changed = True
        elif param.startswith("prob:"):
            _p, i, b, s = param.split(":")
            i, b, s = int(i), int(b), int(s)
            if 0 <= i < len(self.tracks) and 0 <= b < MAX_BARS and 0 <= s < STEPS_PER_BAR:
                self.tracks[i].steps[b][s][1] = max(0, min(100, int(value)))
                changed = True
        if changed:
            self.notify_state()

    # ------------------------------------------------------------- UI commands
    def set_bpm(self, value):
        self.bpm = max(40, min(300, int(value)))

    def set_swing(self, value):
        self.swing = max(0, min(100, float(value)))

    def set_pattern(self, index):
        if 0 <= index < NUM_PATTERNS:
            self.current_pattern = index
            self.edit_bar = min(self.edit_bar, self.pattern_length - 1)
            if not self.song_on:
                self.current_bar = min(self.current_bar, self.pattern_length - 1)
            self.notify_state()

    def set_pattern_length(self, value):
        pat = self.patterns[self.current_pattern]
        pat.pattern_length = max(1, min(MAX_BARS, int(value)))
        if self.edit_bar >= pat.pattern_length:
            self.edit_bar = pat.pattern_length - 1
        if not self.song_on:
            self.current_bar = min(self.current_bar, pat.pattern_length - 1)

    def set_edit_bar(self, value):
        self.edit_bar = max(0, min(self.pattern_length - 1, int(value)))

    def set_step(self, track, bar, step, on):
        if 0 <= track < len(self.tracks) and 0 <= bar < MAX_BARS and 0 <= step < STEPS_PER_BAR:
            self.tracks[track].steps[bar][step][0] = bool(on)

    def set_step_note(self, track, bar, step, note):
        """Piano roll: set (or clear) an explicit pitch for one step."""
        if 0 <= track < len(self.tracks) and 0 <= bar < MAX_BARS and 0 <= step < STEPS_PER_BAR:
            if note is None:
                self.tracks[track].steps[bar][step][0] = False
                self.tracks[track].steps[bar][step][2] = None
            else:
                self.tracks[track].steps[bar][step][2] = max(0, min(127, int(note)))
                self.tracks[track].steps[bar][step][0] = True

    def set_prob(self, track, bar, step, prob):
        if 0 <= track < len(self.tracks) and 0 <= bar < MAX_BARS and 0 <= step < STEPS_PER_BAR:
            prob = max(0, min(100, int(prob)))
            self.tracks[track].steps[bar][step][0] = prob > 0
            self.tracks[track].steps[bar][step][1] = prob

    def cycle_prob(self, track, bar, step):
        if 0 <= track < len(self.tracks) and 0 <= bar < MAX_BARS and 0 <= step < STEPS_PER_BAR:
            cur = self.tracks[track].steps[bar][step][1]
            nxt = {100: 75, 75: 50, 50: 25, 25: 0, 0: 100}.get(cur, 100)
            self.tracks[track].steps[bar][step][1] = nxt
            self.tracks[track].steps[bar][step][0] = nxt > 0

    def set_track_note(self, track, note):
        if 0 <= track < len(self.tracks):
            self.tracks[track].note = max(0, min(127, int(note)))

    def set_track_channel(self, track, channel):
        """channel is 1-16 (UI convention); stored 0-based."""
        if 0 <= track < len(self.tracks):
            self.tracks[track].channel = max(0, min(15, int(channel) - 1))

    def set_track_vel(self, track, vel):
        if 0 <= track < len(self.tracks):
            self.tracks[track].velocity = max(1, min(127, int(vel)))

    def set_track_mode(self, track, mode):
        if 0 <= track < len(self.tracks) and mode in ("fixed", "scale"):
            self.tracks[track].mode = mode

    def set_track_scale(self, track, scale):
        if 0 <= track < len(self.tracks) and scale in SCALES:
            self.tracks[track].scale = scale

    def randomize_track(self, track):
        if 0 <= track < len(self.tracks):
            self.tracks[track].randomize()

    def set_humanize(self, time_ms=None, velocity=None):
        if time_ms is not None:
            self.humanize_time = max(0, min(50, float(time_ms)))
        if velocity is not None:
            self.humanize_velocity = max(0, min(100, int(velocity)))

    def set_humanize_time(self, value):
        self.humanize_time = max(0, min(50, float(value)))

    def set_humanize_velocity(self, value):
        self.humanize_velocity = max(0, min(100, int(value)))

    # ------------------------------------------------------------------ song
    def toggle_song(self):
        self.song_on = not self.song_on
        if self.song_on and self.song_len > 0:
            self.current_pattern = self.song[0]
        self.notify_state()

    def set_song_entry(self, index, pattern):
        if 0 <= index < SONG_MAX and 0 <= pattern < NUM_PATTERNS:
            self.song[index] = pattern
            self.notify_state()

    def set_song_len(self, length):
        self.song_len = max(1, min(SONG_MAX, int(length)))
        self.notify_state()

    # ------------------------------------------------------------------ state
    def get_state(self):
        try:
            midi_outs = mido.get_output_names()
        except Exception:
            midi_outs = []
        tracks = []
        for i, tr in enumerate(self.tracks):
            steps = [
                {
                    "on": tr.steps[self.edit_bar][s][0],
                    "prob": tr.steps[self.edit_bar][s][1],
                    "note": tr.steps[self.edit_bar][s][2],
                }
                for s in range(STEPS_PER_BAR)
            ]
            tracks.append({
                "index": i,
                "name": tr.name,
                "channel": tr.channel + 1,
                "note": tr.note,
                "note_name": note_name(tr.note),
                "velocity": tr.velocity,
                "mode": tr.mode,
                "scale": tr.scale,
                "steps": steps,
            })
        return {
            "bpm": self.bpm,
            "swing": self.swing,
            "playing": self.playing,
            "current_bar": self.current_bar,
            "current_step": self.current_step,
            "edit_bar": self.edit_bar,
            "pattern_length": self.pattern_length,
            "current_pattern": self.current_pattern,
            "song": list(self.song),
            "song_len": self.song_len,
            "song_on": self.song_on,
            "song_pos": self._song_entry,
            "recording": self.recording,
            "automation_count": len(self.automation),
            "humanize_time": self.humanize_time,
            "humanize_velocity": self.humanize_velocity,
            "midi_port": self.port_label,
            "midi_out": self.port_label,
            "midi_outs": midi_outs,
            "tracks": tracks,
        }

    # ------------------------------------------------------------------ seed
    def _seed_default_pattern(self):
        """A fun IDM-ish default so the first play sounds alive."""
        pat = self.patterns[0]
        kick = pat.tracks[0]
        for s in (0, 4, 8, 12):
            kick.steps[0][s] = [True, 100, None]
        snare = pat.tracks[1]
        for s in (4, 12):
            snare.steps[0][s] = [True, 100, None]
        hat = pat.tracks[2]
        for s in range(STEPS_PER_BAR):
            hat.steps[0][s] = [True, 40 if s % 4 == 0 else 100, None]
        bass = pat.tracks[3]
        bass.mode = "scale"
        bass.steps[0][0] = [True, 100, None]
        bass.steps[0][10] = [True, 75, None]
