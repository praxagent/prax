"""Reusable multi-agent pipelines for content synthesis.

The :mod:`synthesis` module provides a generic research → write → publish →
review → revise loop that both the content spoke (blog posts) and the
knowledge spoke (deep-dive notes) use to produce high-quality synthesized
content. The pipeline handles cross-provider reviewer diversity, revision
loops, and publication lifecycle in one reusable abstraction.
"""
from prax.agent.pipelines.synthesis import (
    PipelineResult,
    PublisherFn,
    ResearcherFn,
    ReviewerFn,
    SynthesisPipeline,
    WriterFn,
)

__all__ = [
    "PipelineResult",
    "PublisherFn",
    "ResearcherFn",
    "ReviewerFn",
    "SynthesisPipeline",
    "WriterFn",
]
