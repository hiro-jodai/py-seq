"""MIDI input mapping: turn CC/note messages from any controller into PI-SEQ actions.

Features:
- open a MIDI input port (USB class-compliant or virtual) and listen in a thread
- CC learn: pick an action, turn a knob/button -> mapping created
- relative (endless encoder) vs absolute (pot/fader) auto-calibration
- mappings persist to midi_map.json
"""
import json
import threading
from pathlib import Path

import mido

DEFAULT_SAVE_PATH = Path(__file__).resolve().parent.parent / "midi_map.json"


def resolve_target(action):
    """Return (method_name, index_or_None, lo, hi, step_per_click) or ("cmd", action)."""
    if action == "bpm":
        return ("set_bpm", None, 40, 300, 1)
    if action == "swing":
        return ("set_swing", None, 0, 100, 2)
    if action == "humanize_time":
        return ("set_humanize_time", None, 0, 50, 1)
    if action == "humanize_velocity":
        return ("set_humanize_velocity", None, 0, 100, 2)
    if action.startswith("vel:"):
        return ("set_track_vel", int(action.split(":")[1]), 1, 127, 1)
    if action.startswith("note:"):
        return ("set_track_note", int(action.split(":")[1]), 0, 127, 1)
    if action in ("toggle_play", "play", "stop", "rec", "song_toggle", "clear_auto") or \
            action.startswith("pattern:") or action.startswith("randomize:"):
        return ("cmd", action)
    return None


class MidiMapper:
    def __init__(self, seq, port_name=None, save_path=None):
        self.seq = seq
        self.save_path = Path(save_path) if save_path else DEFAULT_SAVE_PATH
        self.map = {}            # (type, channel, number) -> {"action", "rel", "cal"}
        self.learn_mode = False
        self.learn_action = "swing"
        self.port_name = None
        self.port_error = None
        self.inport = None
        self._thread = None
        self._listeners = []
        self.load()
        if port_name:
            self.open_port(port_name)

    # ---------------------------------------------------------------- events
    def set_state_listener(self, fn):
        self._listeners.append(fn)

    def _notify(self):
        for fn in self._listeners:
            try:
                fn()
            except Exception:
                pass

    # ------------------------------------------------------------------ port
    def open_port(self, name):
        self.close_port()
        try:
            self.inport = mido.open_input(name)
            self.port_name = name
            self.port_error = None
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        except Exception as e:
            self.inport = None
            self.port_name = name
            self.port_error = str(e)
        self.save()
        self._notify()

    def close_port(self):
        if self.inport is not None:
            try:
                self.inport.close()
            except Exception:
                pass
        self.inport = None
        self._thread = None
        self._notify()

    def _loop(self):
        if self.inport is None:
            return
        try:
            for msg in self.inport:
                try:
                    self.handle(msg)
                except Exception:
                    pass
        except Exception:
            pass

    # ------------------------------------------------------------ midi events
    def handle(self, msg):
        if msg.type == "control_change":
            key = ("cc", msg.channel, msg.control)
        elif msg.type == "note_on" and msg.velocity > 0:
            key = ("note", msg.channel, msg.note)
        else:
            return
        if self.learn_mode:
            self._learn_assign(key, msg)
            return
        entry = self.map.get(key)
        if entry is None:
            return
        self._calibrate(entry, msg)
        self._apply(entry, msg)

    def _learn_assign(self, key, msg):
        entry = {"action": self.learn_action, "rel": True, "cal": []}
        self.map[key] = entry
        self._calibrate(entry, msg)
        self.learn_mode = False
        self.save()
        self._notify()

    def _calibrate(self, entry, msg):
        if msg.type != "control_change":
            return
        cal = entry.setdefault("cal", [])
        cal.append(msg.value)
        if len(cal) >= 4:
            entry["rel"] = (max(cal) - min(cal)) <= 10
            entry["cal"] = []
            self._notify()

    def _apply(self, entry, msg):
        target = resolve_target(entry["action"])
        if target is None:
            return
        if target[0] == "cmd":
            # trigger on press
            if msg.type == "control_change":
                if entry.get("rel", True):
                    if msg.value < 64:
                        return
                elif msg.value == 0:
                    return
            self._do_cmd(target[1])
            return
        if entry.get("rel", True):
            delta = msg.value - 64
            if delta == 0:
                return
            cur = self._current_value(target)
            self._do_val(target, cur + delta * target[4])
        else:
            lo, hi = target[2], target[3]
            scaled = lo + (hi - lo) * msg.value / 127.0
            self._do_val(target, scaled)

    def _do_cmd(self, action):
        if action == "toggle_play":
            self.seq.toggle_play()
        elif action == "play":
            self.seq.play()
        elif action == "stop":
            self.seq.stop()
        elif action == "rec":
            self.seq.recording = not self.seq.recording
            self.seq.notify_state()
        elif action == "song_toggle":
            self.seq.toggle_song()
        elif action == "clear_auto":
            self.seq.clear_automation()
        elif action.startswith("pattern:"):
            self.seq.set_pattern(int(action.split(":")[1]))
        elif action.startswith("randomize:"):
            self.seq.randomize_track(int(action.split(":")[1]))
        self._notify()

    def _do_val(self, target, value):
        method, idx, lo, hi, step = target
        value = max(lo, min(hi, int(round(value))))
        fn = getattr(self.seq, method)
        if idx is None:
            fn(value)
        else:
            fn(idx, value)
        self._notify()

    def _current_value(self, target):
        method, idx, lo, hi, step = target
        if method == "set_bpm":
            return self.seq.bpm
        if method == "set_swing":
            return self.seq.swing
        if method == "set_humanize_time":
            return self.seq.humanize_time
        if method == "set_humanize_velocity":
            return self.seq.humanize_velocity
        if method == "set_track_vel":
            return self.seq.tracks[idx].velocity
        if method == "set_track_note":
            return self.seq.tracks[idx].note
        return 0

    # ------------------------------------------------------------ UI commands
    def set_learn(self, action=None):
        self.learn_mode = action is not None
        if action:
            self.learn_action = action
        self._notify()

    def set_mapping_rel(self, index, rel):
        keys = list(self.map.keys())
        if 0 <= index < len(keys):
            self.map[keys[index]]["rel"] = rel
            self.save()
            self._notify()

    def remove_mapping(self, index):
        keys = list(self.map.keys())
        if 0 <= index < len(keys):
            del self.map[keys[index]]
            self.save()
            self._notify()

    def clear_mappings(self):
        self.map = {}
        self.save()
        self._notify()

    # ------------------------------------------------------------- persistence
    def save(self):
        data = {
            "midi_in": self.port_name,
            "map": [
                {
                    "type": k[0], "channel": k[1], "number": k[2],
                    "action": v["action"], "rel": v.get("rel", True),
                }
                for k, v in self.map.items()
            ],
        }
        try:
            self.save_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        except Exception:
            pass

    def load(self):
        if not self.save_path.exists():
            return
        try:
            data = json.loads(self.save_path.read_text())
            self.port_name = data.get("midi_in")
            self.map = {}
            for e in data.get("map", []):
                k = (e["type"], e["channel"], e["number"])
                self.map[k] = {"action": e["action"], "rel": e.get("rel", True), "cal": []}
        except Exception:
            pass

    # ------------------------------------------------------------------ state
    def get_state(self):
        try:
            ports = mido.get_input_names()
        except Exception:
            ports = []
        mapping = []
        for i, (k, v) in enumerate(self.map.items()):
            mapping.append({
                "index": i,
                "type": k[0],
                "channel": k[1],
                "number": k[2],
                "action": v["action"],
                "rel": v.get("rel", True),
            })
        return {
            "midi_in": self.port_name or "",
            "midi_error": self.port_error,
            "midi_ports": ports,
            "learn_mode": self.learn_mode,
            "learn_action": self.learn_action,
            "mapping": mapping,
        }
