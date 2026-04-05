"""Standalone entry point for E2E tests: python -m e2e"""

from __future__ import annotations

import asyncio
import sys

from .harness import run_harness
from .scenarios.session_lifecycle import test_session_lifecycle
from .scenarios.question_answer import test_question_answer
from .scenarios.slash_commands import test_slash_commands
from .scenarios.live_dashboard import test_live_dashboard
from .scenarios.pagination import test_pagination
from .scenarios.image_extraction import test_image_extraction

SCENARIOS = [
    ("Session Lifecycle", test_session_lifecycle),
    ("Question / Answer Flow", test_question_answer),
    ("Slash Commands", test_slash_commands),
    ("Live Dashboard", test_live_dashboard),
    ("Pagination", test_pagination),
    ("Image Extraction", test_image_extraction),
]


def main():
    reporter = asyncio.run(run_harness(SCENARIOS))
    reporter.print_summary()
    sys.exit(0 if reporter.failed_count == 0 else 1)


if __name__ == "__main__":
    main()
