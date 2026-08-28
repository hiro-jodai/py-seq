#!/usr/bin/env python3
"""Connect to the PI-SEQ MIDI port and print everything it sends.

Usage:
    python scripts/midi_monitor.py            # auto-find "PI-Sequencer"
    python scripts/midi_monitor.py "UM-ONE MIDI 1"
"""
import sys

import mido

want = sys.argv[1] if len(sys.argv) > 1 else "PI-Sequencer"
names = mido.get_input_names()
port = next((n for n in names if want.lower() in n.lower()), None)
if port is None:
    print(f"port '{want}' not found. available: {names}")
    sys.exit(1)

inp = mido.open_input(port)
print(f"monitoring {port} — Ctrl+C to stop", flush=True)
with inp:
    for msg in inp:
        print(msg, flush=True)
