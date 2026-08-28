"""Core step-sequencer engine for PI-SEQ.

A thread ticks 16th notes, fires probability-gated notes (optionally random
notes from a scale) over USB MIDI (mido / python-rtmidi), and replays a simple
parameter-automation timeline recorded from the UI.

v0.5: dynamic tracks (add/remove/rename/recolor), follow mode.
"""
import random
import re
import threading
import time

import mido

from .scales import SCALES

STEPS_PER_BAR = 16
MAX_BARS = 32
DEFAULT_BARS = 2
NUM_PATTERNS = 4
SONG_MAX = 16
MAX_TRACKS = 16
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PALETTE = [
    "#22d3ee", "#f472b6", "#fbbf24", "#a78bfa",
    "#34d399", "#f87171", "#60a5fa", "#f97316",
    "#2dd4bf", "#e879f9", "#a3e635", "#fb7185",
    "#38bdf8", "#facc15", "#4ade80", "#c084fc",
]


def note_name(n):
    n = max(0, min(127, int(n)))
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


class Track:
    """One sequencer lane: 32 bars x 16 steps, each step = [on, probability, notes, length].

    notes is None -> use the track's fixed note / scale-random logic.
    notes is a list of ints -> a chord (one or more pitches) for this step.
    length is in step units (1 = one 16th note), shared by all chord notes.
    """

    def __init__(self, name, channel, note, velocity=100):
        self.name = name
        self.channel = channel            # 0-based MIDI channel
        self.note = note                  # fixed note / scale root
        self.velocity = velocity
        self.mode = "fixed"               # "fixed" | "scale"
        self.scale = "minor_pentatonic"
        self.midi_out = None              # output port override (None = global)
        self.steps = [[[False, 100, None, 1] for _ in range(STEPS_PER_BAR)] for _ in range(MAX_BARS)]

    def randomize(self, density=0.4):
        probs = [100, 100, 100, 80, 60, 50]
        for b in range(MAX_BARS):
            for s in range(STEPS_PER_BAR):
                on = random.random() < density
                self.steps[b][s] = [on, random.choice(probs) if on else 100, None, 1]

    @staticmethod
    def notes_of(cell):
        """Return the step's explicit note list, or None if it uses track logic."""
        raw = cell[2] if len(cell) > 2 else None
        if raw is None:
            return None
        if isinstance(raw, int):
            return [raw]
        return list(raw)


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
        self.follow = True            # auto-follow the playhead across bars

        self.track_colors = list(PALETTE[:len(self.patterns[0].tracks)])

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
        self._note_offs = {}          # (track_idx, note) -> release_time
        self._track_outputs = {}      # port name -> mido port (per-track devices)

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

    def _port_for(self, tr):
        """Return the output port for a track (per-track override or global)."""
        if tr.midi_out:
            port = self._track_outputs.get(tr.midi_out)
            if tr.midi_out not in self._track_outputs:
                try:
                    port = mido.open_output(tr.midi_out)
                except Exception:
                    port = None
                self._track_outputs[tr.midi_out] = port
            return port
        return self.out

    def _send_note_on(self, tr, note, vel):
        port = self._port_for(tr)
        if port is None:
            return
        try:
            port.send(mido.Message("note_on", channel=tr.channel, note=note, velocity=vel))
        except Exception:
            pass

    def _send_note_off(self, tr, note):
        port = self._port_for(tr)
        if port is None:
            return
        try:
            port.send(mido.Message("note_off", channel=tr.channel, note=note, velocity=0))
        except Exception:
            pass

    def _all_ports(self):
        ports = []
        if self.out is not None:
            ports.append(self.out)
        for port in self._track_outputs.values():
            if port is not None and port not in ports:
                ports.append(port)
        return ports

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
        for key, _until in list(self._note_offs.items()):
            ti, note = key
            if 0 <= ti < len(self.tracks):
                self._send_note_off(self.tracks[ti], note)
        self._note_offs.clear()
        # panic: CC123 (all notes off) on every channel of every open port
        for port in self._all_ports():
            for ch in range(16):
                try:
                    port.send(mido.Message("control_change", channel=ch, control=123, value=0))
                except Exception:
                    pass

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
                pat_len = self.patterns[self.current_pattern].pattern_length
                self.current_bar = (self.current_bar + 1) % pat_len
                if self.current_bar == 0:
                    self._song_entry = (self._song_entry + 1) % self.song_len
                    self.current_pattern = self.song[self._song_entry]
            else:
                self.current_bar = (self.current_bar + 1) % self.pattern_length
            if self.follow and self.edit_bar != self.current_bar:
                self.edit_bar = self.current_bar
                self.notify_state()   # UI must re-render the followed bar

    def _play_step(self, bar, step):
        now = time.monotonic()
        step_dur = self._step_duration()
        # release notes whose duration has elapsed
        for key in list(self._note_offs):
            until = self._note_offs[key]
            if now >= until:
                ti, note = key
                if 0 <= ti < len(self.tracks):
                    self._send_note_off(self.tracks[ti], note)
                del self._note_offs[key]
        for ti, tr in enumerate(self.tracks):
            cell = tr.steps[bar][step]
            on, prob = cell[0], cell[1]
            notes = tr.notes_of(cell)
            step_len = cell[3] if len(cell) > 3 else 1
            if not on:
                continue
            if random.random() * 100.0 >= prob:
                continue
            note_list = notes if notes is not None else [self._pick_note(tr)]
            vel = self._pick_velocity(tr)
            # cut any still-sounding notes on this track so nothing gets stuck
            for key in [k for k in self._note_offs if k[0] == ti]:
                old_note = key[1]
                self._send_note_off(tr, old_note)
                del self._note_offs[key]
            for note in note_list:
                self._send_note_on(tr, note, vel)
                self._note_offs[(ti, note)] = now + step_dur * self.note_length * step_len
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

    def set_step_note(self, track, bar, step, note, length=None):
        """Piano roll: toggle a pitch in the step's chord.

        note=None clears the whole step. Adding/removing a pitch toggles it.
        """
        if not (0 <= track < len(self.tracks) and 0 <= bar < MAX_BARS and 0 <= step < STEPS_PER_BAR):
            return
        cell = self.tracks[track].steps[bar][step]
        if note is None:
            cell[0] = False
            cell[2] = None
            return
        pitch = max(0, min(127, int(note)))
        notes = self.tracks[track].notes_of(cell) or []
        was_empty = self.tracks[track].notes_of(cell) is None
        if pitch in notes:
            notes.remove(pitch)
        else:
            notes.append(pitch)
            notes.sort()
        if notes:
            cell[0] = True
            cell[2] = notes
            # only fresh notes take a length (adding to a chord keeps its length)
            if length is not None and was_empty:
                cell[3] = max(1, min(16, int(length)))
        else:
            cell[0] = False
            cell[2] = None

    def set_step_length(self, track, bar, step, length):
        """Set how many steps a step's note sustains (1-16)."""
        if 0 <= track < len(self.tracks) and 0 <= bar < MAX_BARS and 0 <= step < STEPS_PER_BAR:
            self.tracks[track].steps[bar][step][3] = max(1, min(16, int(length)))

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

    def set_track_out(self, track, port_name):
        """Route a track to its own MIDI output device (None = global)."""
        if 0 <= track < len(self.tracks):
            self.tracks[track].midi_out = port_name or None
            if self.tracks[track].midi_out in self._track_outputs:
                del self._track_outputs[self.tracks[track].midi_out]
            self.notify_state()

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

    # ------------------------------------------------------------- track mgmt
    def add_track(self):
        if len(self.tracks) >= MAX_TRACKS:
            return
        idx = len(self.tracks)
        for pat in self.patterns:
            pat.tracks.append(Track(f"TRK{idx + 1}", idx % 16, 48 + idx, 100))
        self.track_colors.append(PALETTE[len(self.track_colors) % len(PALETTE)])
        self.notify_state()

    def remove_track(self, index):
        if len(self.tracks) <= 1:
            return
        if 0 <= index < len(self.tracks):
            for pat in self.patterns:
                if index < len(pat.tracks):
                    pat.tracks.pop(index)
            if index < len(self.track_colors):
                self.track_colors.pop(index)
            self.notify_state()

    def set_track_name(self, index, name):
        if 0 <= index < len(self.tracks):
            clean = (name or "").strip()[:16] or f"TRK{index + 1}"
            for pat in self.patterns:
                if index < len(pat.tracks):
                    pat.tracks[index].name = clean
            self.notify_state()

    def set_track_color(self, index, color):
        if 0 <= index < len(self.tracks) and re.match(r"^#[0-9a-fA-F]{6}$", color or ""):
            self.track_colors[index] = color.lower()
            self.notify_state()

    def set_follow(self, on):
        self.follow = bool(on)
        if self.follow:
            self.edit_bar = self.current_bar
        self.notify_state()

    def toggle_follow(self):
        self.set_follow(not self.follow)

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
                    "notes": tr.notes_of(tr.steps[self.edit_bar][s]),
                    "length": tr.steps[self.edit_bar][s][3] if len(tr.steps[self.edit_bar][s]) > 3 else 1,
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
                "midi_out": tr.midi_out,
                "color": self.track_colors[i] if i < len(self.track_colors) else PALETTE[i % len(PALETTE)],
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
            "follow": self.follow,
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
            kick.steps[0][s] = [True, 100, None, 1]
        snare = pat.tracks[1]
        for s in (4, 12):
            snare.steps[0][s] = [True, 100, None, 1]
        hat = pat.tracks[2]
        for s in range(STEPS_PER_BAR):
            hat.steps[0][s] = [True, 40 if s % 4 == 0 else 100, None, 1]
        bass = pat.tracks[3]
        bass.mode = "scale"
        bass.steps[0][0] = [True, 100, None, 1]
        bass.steps[0][10] = [True, 75, None, 1]
