"""Ingestion-run timing: a thin context-manager layer over the M2 run ledger.

:class:`~app.ai.repositories.knowledge_repository.KnowledgeIngestionRunRepository` already owns
starting, finishing and re-stamping a run row; this module only adds per-stage timing so
``pipeline.py`` can write ``with tracker.stage(IngestionStage.PARSE): ...`` once per stage instead
of hand-threading a timings dict and repeated ``set_stage`` calls through the whole function.

One thing worth knowing before reading ``pipeline.py``'s failure handling:
:meth:`KnowledgeIngestionRunRepository.finish` unconditionally sets ``run.stage`` to
``IngestionStage.FINALIZE`` for every outcome, including a failure — that method is M2-shipped and
out of this macro's scope to change. So *which* stage actually failed is recorded in
``error_message`` (prefixed with the stage name) rather than in the ``stage`` column, which by the
time ``finish()`` runs always reads ``FINALIZE`` regardless of the outcome.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

from app.ai.core.enums import IngestionStage
from app.ai.models.knowledge import KnowledgeIngestionRun
from app.ai.repositories.knowledge_repository import KnowledgeIngestionRunRepository


class RunTracker:
    """Times each stage of one ingestion run and accumulates ``stage_timings``."""

    def __init__(self, repo: KnowledgeIngestionRunRepository, run: KnowledgeIngestionRun) -> None:
        self._repo = repo
        self.run = run
        self.stage_timings: dict[str, int] = {}
        self.current_stage: IngestionStage = IngestionStage.FETCH

    @contextmanager
    def stage(self, stage: IngestionStage) -> Iterator[None]:
        self.current_stage = stage
        self._repo.set_stage(self.run, stage)
        started = time.monotonic()
        try:
            yield
        finally:
            self.stage_timings[stage.value] = int((time.monotonic() - started) * 1000)


__all__ = ["RunTracker"]
