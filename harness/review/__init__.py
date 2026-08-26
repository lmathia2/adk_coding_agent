"""Optional, advisory final-diff review support."""

from .ablation import (
    ReviewAblationReport,
    ReviewAblationSample,
    ReviewVariantSummary,
    compare_reviewer_ablation,
)
from .diff import build_diff_review_packet
from .models import DiffReviewPacket, FinalDiffReview, ReviewFinding

__all__ = [
    "DiffReviewPacket",
    "FinalDiffReview",
    "ReviewAblationReport",
    "ReviewAblationSample",
    "ReviewFinding",
    "ReviewVariantSummary",
    "build_diff_review_packet",
    "compare_reviewer_ablation",
]
