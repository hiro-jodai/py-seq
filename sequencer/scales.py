"""Scale definitions for random-note generation (intervals from root)."""

SCALES = {
    "chromatic": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    "minor_pentatonic": [0, 3, 5, 7, 10],
    "major_pentatonic": [0, 2, 4, 7, 9],
    "natural_minor": [0, 2, 3, 5, 7, 8, 10],
    "natural_major": [0, 2, 4, 5, 7, 9, 11],
    "blues": [0, 3, 5, 6, 7, 10],
    "whole_tone": [0, 2, 4, 6, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
}
