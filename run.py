#!/usr/bin/env python3
"""PI-SEQ — Raspberry Pi MIDI step sequencer (browser UI).

Usage:
    python run.py                 # serve on 0.0.0.0:8000, virtual MIDI port
    python run.py --port 8080
    python run.py --midi-port "my synth"
    python run.py --no-virtual    # don't create a virtual MIDI port
"""
import argparse
from pathlib import Path

import uvicorn

from sequencer.core import Sequencer
from sequencer.web import build_app

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description="PI-SEQ step sequencer")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--midi-port", default=None, help="explicit MIDI output port name")
    ap.add_argument("--no-virtual", action="store_true", help="don't create a virtual MIDI port")
    ap.add_argument("--bpm", type=int, default=120)
    ap.add_argument("--bars", type=int, default=2)
    args = ap.parse_args()

    seq = Sequencer(
        midi_port=args.midi_port,
        virtual=not args.no_virtual,
        bpm=args.bpm,
        bars=args.bars,
    )
    print(f"[pi-seq] MIDI out: {seq.port_label}")
    app = build_app(seq, ROOT / "web")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
