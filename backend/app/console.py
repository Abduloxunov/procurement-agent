"""Console setup for CLI scripts.

Windows defaults stdout to cp1252, which raises UnicodeEncodeError on any
non-Latin character. Half our sources are Chinese and specs routinely carry
symbols like the less-than-or-equal sign, so every script needs UTF-8 out.
"""

from __future__ import annotations

import sys
import warnings


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass  # already UTF-8, or a stream that cannot be reconfigured

    # Library deprecation notices we cannot act on drown the agent trace,
    # which is the thing worth reading. Our own warnings still show.
    warnings.filterwarnings(
        "ignore", category=DeprecationWarning, module=r"langgraph.*"
    )
    warnings.filterwarnings("ignore", message=r".*allowed_objects.*")
