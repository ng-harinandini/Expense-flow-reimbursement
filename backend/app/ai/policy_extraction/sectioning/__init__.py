"""Section detection: turning a document's chunks into the units extraction actually runs on.

The pipeline is deterministic first and model-assisted only where determinism runs out:

    tier 1  NUMBERING  a numbered heading ("4.1 Meals")            regex, near-certain
    tier 2  HEADING    a standalone title line                     regex + category vocabulary
    tier 3  SEMANTIC   a structural guess from line shape          heuristic, below threshold
    tier 4  LLM        sub-dividing a section too large to send    one narrow call, only when needed

Tier 4 is not part of finding sections at all — it only ever divides one that heuristics already
found and that exceeds the prompt budget. A document whose sections all fit never reaches it, so the
common case costs no extra model calls.

No stage of this package performs a vector search or touches an embedding. Extraction reads the
document it was given; retrieval is a different feature with a different purpose.
"""

from app.ai.policy_extraction.sectioning.detectors import (
    DEFAULT_DETECTORS,
    HeadingDetector,
    HeadingDetectorProtocol,
    NumberedHeadingDetector,
    SemanticHeadingDetector,
)
from app.ai.policy_extraction.sectioning.pipeline import (
    PREAMBLE_TITLE,
    WHOLE_DOCUMENT_TITLE,
    SectionDetectionPipeline,
)
from app.ai.policy_extraction.sectioning.splitting import (
    ProposedSubsection,
    SubsectionAdvisor,
    split_oversized_sections,
)
from app.ai.policy_extraction.sectioning.types import (
    DetectedSection,
    DetectionMethod,
    HeadingCandidate,
    SectionStatus,
)

__all__ = [
    "DEFAULT_DETECTORS",
    "PREAMBLE_TITLE",
    "WHOLE_DOCUMENT_TITLE",
    "DetectedSection",
    "DetectionMethod",
    "HeadingCandidate",
    "HeadingDetector",
    "HeadingDetectorProtocol",
    "NumberedHeadingDetector",
    "ProposedSubsection",
    "SectionDetectionPipeline",
    "SectionStatus",
    "SemanticHeadingDetector",
    "SubsectionAdvisor",
    "split_oversized_sections",
]
