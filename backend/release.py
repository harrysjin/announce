"""Public engine identity; build values must be actual Git object IDs."""
from __future__ import annotations

import os
import re

VERSION = "0.2.0"
ENGINE_VERSION = "2026.11.09.2"
CAPABILITIES = (
    "kiwi_morphology", "compound_components", "numeric_readings",
    "numeric_reading_selection", "particle_boundaries", "original_glyph_alignment",
)


def release_identity() -> dict:
    commit = next((value.lower() for name in ("RENDER_GIT_COMMIT", "BUILD_COMMIT")
                   if re.fullmatch(r"[0-9a-fA-F]{40}", value := os.environ.get(name, ""))), None)
    return {"version": VERSION, "engine_version": ENGINE_VERSION,
            "build_commit": commit, "capabilities": list(CAPABILITIES)}
