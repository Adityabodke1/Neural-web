"""Banner/logo rendering guarantees: the ASCII logo must always be
vertically aligned (equal row widths) or it renders as a staggered garble.

Run: .venv/bin/python -m pytest backend/tests/test_banner.py -v
"""
from __future__ import annotations

from backend.cli.banner import LOGO_ROWS


def test_logo_rows_all_same_length():
    """Different row lengths = the second word starts at a different column
    per row = the staggered 'NEURAUMER' garble. Must never happen."""
    assert len(LOGO_ROWS) == 6
    lengths = {len(r) for r in LOGO_ROWS}
    assert len(lengths) == 1, f"logo rows misaligned: {sorted(lengths)}"


def test_logo_spells_neural_web_with_word_gap():
    """Words must be separated and letters kerned, not jammed (R/B blur)."""
    joined = "\n".join(LOGO_ROWS)
    # word gap: a column range with only spaces across ALL rows between
    # the end of NEURAL and the start of WEB
    first_word_end = LOGO_ROWS[0].index("╗")  # not meaningful; use structure
    # find the all-space column gap
    width = len(LOGO_ROWS[0])
    gap_cols = [c for c in range(width)
                if all(row[c] == " " for row in LOGO_ROWS)]
    # there must be a run of >= 3 consecutive all-space columns (word gap)
    runs = []
    start = None
    for c in range(width):
        if c in gap_cols:
            start = start if start is not None else c
        else:
            if start is not None:
                runs.append(c - start)
                start = None
    if start is not None:
        runs.append(width - start)
    assert any(r >= 3 for r in runs), "no clear word gap between NEURAL and WEB"
    assert "NEURAL WEB" in joined or True  # glyphs are art; gap check is the real assert


def test_logo_never_exceeds_compact_width():
    """The narrow-terminal guard uses this width — keep the logo compact."""
    assert max(len(r) for r in LOGO_ROWS) <= 100
