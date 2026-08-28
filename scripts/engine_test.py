"""Engine-level tests for PI-SEQ (no ALSA needed — uses a mock MIDI output)."""
import time

from sequencer.core import STEPS_PER_BAR, Sequencer


class TimedMockOut:
    def __init__(self):
        self.msgs = []  # [(time.monotonic(), mido.Message)]

    def send(self, m):
        self.msgs.append((time.monotonic(), m))


# ---------------------------------------------------------------- basic play
seq = Sequencer(virtual=True, bpm=120, bars=2)
seq.out = TimedMockOut()
mock = seq.out
seq.play()
time.sleep(0.9)
seq.stop()
ons = [m for m in mock.msgs if m[1].type == "note_on"]
print("basic play: note_on =", len(ons), "| channels =", sorted(set(m[1].channel for m in ons)))
assert len(ons) > 5
assert sorted(set(m[1].channel for m in ons)) == [0, 1, 2, 3]

# ---------------------------------------------------------------- swing timing
seq2 = Sequencer(virtual=True, bpm=120, bars=1)
seq2.set_swing(50)
hat = seq2.tracks[2]
for s in range(STEPS_PER_BAR):
    hat.steps[0][s] = [True, 100, None]
for tr in seq2.tracks:
    if tr is not hat:
        for b in range(len(tr.steps)):
            for s in range(STEPS_PER_BAR):
                tr.steps[b][s] = [False, 100, None]
seq2.out = TimedMockOut()
m2 = seq2.out
seq2.play()
time.sleep(1.35)
seq2.stop()
times = [t for t, m in m2.msgs if m.type == "note_on" and m.channel == 2]
gaps = [round((b - a) * 1000) for a, b in zip(times, times[1:])]
on_off = gaps[0::2]   # onbeat -> offbeat
off_on = gaps[1::2]   # offbeat -> next onbeat
print("swing gaps ms:", gaps[:10])
print("on->off avg:", round(sum(on_off) / len(on_off)), "ms | off->on avg:", round(sum(off_on) / len(off_on)), "ms")
assert round(sum(on_off) / len(on_off)) > 150, "swing not delaying offbeats?"
assert round(sum(off_on) / len(off_on)) < 100, "offbeat not snapping back?"

# ---------------------------------------------------------- pattern switching
seq.set_pattern(2)
assert seq.current_pattern == 2
assert all(not s[0] for s in seq.tracks[2].steps[0]), "P3 should start empty"
print("pattern switch OK -> P3 active and empty")
seq.set_pattern(0)
assert seq.current_pattern == 0
assert any(s[0] for s in seq.tracks[0].steps[0]), "P1 should have the seed pattern"
print("back to P1 OK")

# ---------------------------------------------------------------- song mode
seq3 = Sequencer(virtual=True, bpm=240, bars=1)
for p in seq3.patterns:
    p.pattern_length = 1
seq3.song = [0, 1, 2, 3]
seq3.song_len = 4
seq3.song_on = True
seq3.out = TimedMockOut()
seq3.play()
time.sleep(1.3)  # at 240bpm one bar = 1s -> should now be in song entry 1
p_after = seq3.current_pattern
pos = seq3._song_entry
seq3.stop()
print("song: after 1.3s -> pattern =", p_after, "| song_pos =", pos)
assert p_after == 1 and pos == 1, "song should have advanced to entry 1"

# ------------------------------------------------------------- piano roll notes
seq4 = Sequencer(virtual=True, bpm=120, bars=1)
bass = seq4.tracks[3]
for b in range(len(bass.steps)):
    for s in range(STEPS_PER_BAR):
        bass.steps[b][s] = [False, 100, None]
seq4.set_step_note(3, 0, 0, 48)   # C3 explicit
seq4.set_step_note(3, 0, 2, 55)   # G3 explicit
seq4.set_step_note(3, 0, 4, None)  # clear (should turn off)
seq4.out = TimedMockOut()
m4 = seq4.out
seq4.play()
time.sleep(0.7)
seq4.stop()
bass_notes = sorted(set(m[1].note for m in m4.msgs if m[1].type == "note_on" and m[1].channel == 3))
print("piano roll: bass notes played =", bass_notes)
assert bass_notes == [48, 55], bass_notes
assert seq4.tracks[3].steps[0][4] == [False, 100, None], "cleared note should be off"
print("piano roll OK")

print("ALL ENGINE v0.4 TESTS PASSED")
