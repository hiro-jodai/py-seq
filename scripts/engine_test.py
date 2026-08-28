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
        bass.steps[b][s] = [False, 100, None, 1]
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
assert seq4.tracks[3].steps[0][4] == [False, 100, None, 1], "cleared note should be off"
print("piano roll OK")

# ------------------------------------------------------------- chords
seq11 = Sequencer(virtual=True, bpm=120, bars=1)
bass = seq11.tracks[3]
for b in range(len(bass.steps)):
    for s in range(STEPS_PER_BAR):
        bass.steps[b][s] = [False, 100, None, 1]
seq11.set_step_note(3, 0, 0, 48)   # C3
seq11.set_step_note(3, 0, 0, 52)   # E3 -> chord
seq11.set_step_note(3, 0, 0, 55)   # G3 -> C-E-G
assert seq11.tracks[3].steps[0][0][2] == [48, 52, 55], seq11.tracks[3].steps[0][0][2]
seq11.set_step_note(3, 0, 0, 52)   # toggle E off -> C-G
assert seq11.tracks[3].steps[0][0][2] == [48, 55], seq11.tracks[3].steps[0][0][2]
seq11.set_step_length(3, 0, 0, 2)
seq11.out = TimedMockOut()
m11 = seq11.out
seq11.play()
time.sleep(0.5)
seq11.stop()
chord_ons = sorted(set(m[1].note for m in m11.msgs if m[1].type == "note_on" and m[1].channel == 3))
chord_offs = [m for m in m11.msgs if m[1].type == "note_off" and m[1].channel == 3]
print("chord: notes =", chord_ons, "| offs =", len(chord_offs))
assert chord_ons == [48, 55], chord_ons
assert len(chord_offs) == 2, len(chord_offs)
print("chord OK")

# chord add must NOT reset the step's length
seq12 = Sequencer(virtual=True, bpm=120, bars=1)
bass = seq12.tracks[3]
for b in range(len(bass.steps)):
    for s in range(STEPS_PER_BAR):
        bass.steps[b][s] = [False, 100, None, 1]
seq12.set_step_note(3, 0, 0, 48, length=4)   # fresh C with LEN 4
seq12.set_step_note(3, 0, 0, 52, length=1)   # add E with LEN 1 -> must NOT reset
assert seq12.tracks[3].steps[0][0][3] == 4, seq12.tracks[3].steps[0][0][3]
assert seq12.tracks[3].steps[0][0][2] == [48, 52]
print("chord length preserved OK")

# ---------------------------------------------------- stuck-note regression
# swing 50 + notes on every step used to orphan note_offs (stuck notes)
seq5 = Sequencer(virtual=True, bpm=120, bars=2)
seq5.set_swing(50)
for tr in seq5.tracks:
    for b in range(len(tr.steps)):
        for s in range(STEPS_PER_BAR):
            tr.steps[b][s] = [True, 100, None]
seq5.out = TimedMockOut()
m5 = seq5.out
seq5.play()
time.sleep(1.6)   # ~13 steps through a swung bar
seq5.stop()
ons = [m for m in m5.msgs if m[1].type == "note_on"]
offs = [m for m in m5.msgs if m[1].type == "note_off"]
print("stuck-note check: note_on=%d note_off=%d" % (len(ons), len(offs)))
assert len(offs) >= len(ons), "every note_on must get a note_off (no stuck notes)"
# stop() should have swept everything: per-track on == off
for ti in range(4):
    n_on = sum(1 for m in ons if m[1].channel == seq5.tracks[ti].channel)
    n_off = sum(1 for m in offs if m[1].channel == seq5.tracks[ti].channel)
    assert n_off >= n_on, (ti, n_on, n_off)
print("stuck-note regression OK (swing 50, dense pattern, stop sweeps clean)")

# ------------------------------------------- per-track output routing (smoke)
seq6 = Sequencer(virtual=True, bpm=120, bars=1)
seq6.set_track_out(3, "NO-SUCH-PORT")   # bogus device: must not crash
seq6.out = TimedMockOut()
m6 = seq6.out
seq6.play()
time.sleep(0.4)
seq6.stop()
kick_ons = [m for m in m6.msgs if m[1].type == "note_on" and m[1].channel == 0]
print("per-track out smoke: kick (global) notes =", len(kick_ons))
assert len(kick_ons) > 0, "global port track should still play when another track has a bogus device"
assert seq6.tracks[3].midi_out == "NO-SUCH-PORT"
print("per-track output routing OK (bogus device skipped gracefully)")

# --------------------------------------------------------- track management
seq7 = Sequencer(virtual=True, bars=1)
n0 = len(seq7.tracks)
seq7.add_track()
assert len(seq7.tracks) == n0 + 1
for pat in seq7.patterns:
    assert len(pat.tracks) == n0 + 1, "add must apply to all patterns"
seq7.set_track_name(n0, "FM1")
assert all(pat.tracks[n0].name == "FM1" for pat in seq7.patterns)
seq7.set_track_color(n0, "#ff0000")
assert seq7.track_colors[n0] == "#ff0000"
seq7.remove_track(n0)
assert len(seq7.tracks) == n0
print("track mgmt OK")

# ------------------------------------------------------------- follow mode
seq8 = Sequencer(virtual=True, bpm=240, bars=2)   # 1 bar = 1s at 240bpm
seq8.set_follow(True)
seq8.play()
time.sleep(1.3)   # should now be in bar 1
assert seq8.current_bar == 1, seq8.current_bar
assert seq8.edit_bar == 1, seq8.edit_bar
seq8.stop()
print("follow mode OK (edit_bar follows playhead)")

# ------------------------------------------- song with multi-bar patterns
seq9 = Sequencer(virtual=True, bpm=240, bars=1)
seq9.patterns[0].pattern_length = 2
seq9.patterns[1].pattern_length = 1
seq9.song = [0, 1]
seq9.song_len = 2
seq9.song_on = True
seq9.play()
time.sleep(1.3)   # pattern 0 has 2 bars (2s): still inside it, bar 1
p1, b1 = seq9.current_pattern, seq9.current_bar
time.sleep(1.1)   # total 2.4s -> should have advanced to pattern 1
p2 = seq9.current_pattern
seq9.stop()
print("song multi-bar: at 1.3s -> pat=%s bar=%s ; at 2.4s -> pat=%s" % (p1, b1, p2))
assert p1 == 0 and b1 == 1, (p1, b1)
assert p2 == 1, p2
print("song multi-bar OK (pattern plays its full length before advancing)")

# ------------------------------------------------------------- note lengths
seq10 = Sequencer(virtual=True, bpm=120, bars=1)
bass = seq10.tracks[3]
for b in range(len(bass.steps)):
    for s in range(STEPS_PER_BAR):
        bass.steps[b][s] = [False, 100, None, 1]
seq10.set_step_note(3, 0, 0, 48, length=4)
assert seq10.tracks[3].steps[0][0][3] == 4
seq10.out = TimedMockOut()
m10 = seq10.out
seq10.play()
time.sleep(1.0)   # 8 steps
seq10.stop()
bass_ons = [x for x in m10.msgs if x[1].type == "note_on" and x[1].channel == 3]
bass_offs = [x for x in m10.msgs if x[1].type == "note_off" and x[1].channel == 3]
assert len(bass_ons) == 1 and len(bass_offs) == 1, (len(bass_ons), len(bass_offs))
dur = bass_offs[0][0] - bass_ons[0][0]
print("note length: duration = %.3fs (expect ~0.425s = 4 steps x 0.125 x 0.85)" % dur)
assert 0.35 < dur < 0.55, dur
print("note length OK")

print("ALL ENGINE v0.5.1 TESTS PASSED")
