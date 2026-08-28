"""Tests for MIDI input mapping (no ALSA needed — messages injected directly)."""
import os
import tempfile

import mido

from sequencer.core import Sequencer
from sequencer.mapping import MidiMapper


def msg_cc(ch, ctrl, val):
    return mido.Message("control_change", channel=ch, control=ctrl, value=val)


def msg_note_on(ch, note, vel=100):
    return mido.Message("note_on", channel=ch, note=note, velocity=vel)


seq = Sequencer(virtual=True, bars=1)
tmp = os.path.join(tempfile.mkdtemp(), "map.json")
mp = MidiMapper(seq, port_name=None, save_path=tmp)

# ---- learn: CC ch0 ctrl1 -> swing ----
mp.set_learn("swing")
mp.handle(msg_cc(0, 1, 65))          # one click up
assert mp.learn_mode is False, "learn should auto-assign on first message"
key = ("cc", 0, 1)
assert key in mp.map and mp.map[key]["action"] == "swing"
print("learn assign OK")

# ---- relative apply: swing +2 per click ----
s0 = seq.swing
mp.handle(msg_cc(0, 1, 65))
mp.handle(msg_cc(0, 1, 65))
assert seq.swing == s0 + 4, (s0, seq.swing)
print("rel apply OK:", s0, "->", seq.swing)

# ---- absolute calibration: wide values flip to abs ----
for v in (0, 127, 100, 10):
    mp.handle(msg_cc(0, 1, v))
assert mp.map[key]["rel"] is False, "should auto-detect absolute"
mp.handle(msg_cc(0, 1, 127))
assert seq.swing >= 99, seq.swing
print("abs calibration OK: swing ->", seq.swing)

# ---- note button -> toggle_play ----
mp.set_learn("toggle_play")
mp.handle(msg_note_on(0, 60))          # first press only assigns, does NOT trigger
assert mp.map[("note", 0, 60)]["action"] == "toggle_play"
assert seq.playing is False, "learning press should not trigger the command"
mp.handle(msg_note_on(0, 60))          # now it triggers
assert seq.playing is True
mp.handle(msg_note_on(0, 60))
assert seq.playing is False
print("note button OK")

# ---- persistence ----
mp.save()
mp2 = MidiMapper(seq, port_name=None, save_path=tmp)
assert ("cc", 0, 1) in mp2.map and ("note", 0, 60) in mp2.map
print("save/load OK")

# ---- remove / clear ----
mp2.remove_mapping(0)
mp2.clear_mappings()
assert len(mp2.map) == 0
print("remove/clear OK")

# ---- bpm mapping: relative step +1 ----
mp2.set_learn("bpm")
mp2.handle(msg_cc(0, 5, 65))
assert mp2.map[("cc", 0, 5)]["action"] == "bpm"
mp2.handle(msg_cc(0, 5, 65))
mp2.handle(msg_cc(0, 5, 65))
assert seq.bpm == 122, seq.bpm  # default 120 + 2
print("bpm rel OK:", seq.bpm)

print("ALL MAPPING TESTS PASSED")
