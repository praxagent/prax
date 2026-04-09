"""Reusable multi-agent content synthesis pipeline.

Generic ``research → write → publish → review → revise`` loop used by both
the content spoke (blog posts) and the knowledge spoke (deep-dive notes).

The pipeline decouples the content-specific bits (how to research, how to
write, how to review, how to publish) from the orchestration (the revision
loop, status updates, approval detection, error handling) so both spokes
can share the same battle-tested coordinator.

## Architecture

Each phase is a pluggable callable:

- ``researcher(topic, notes) → str`` — returns research findings
- ``writer(topic, research, feedback, previous_draft) → str`` — returns draft
- ``publisher(title, content, tags, slug) → dict`` — returns ``{slug, url}`` or ``{error}``
- ``reviewer(draft, url, pass_number) → str`` — returns ``"APPROVED..."`` or ``"REVISE..."``

The pipeline wires them together::

    Research → Write → Publish → Review
                                   ↓
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
                APPROVED                      REVISE
                    ↓                             ↓
                  Done                   Write(feedback) →
                                          Publish(same slug) →
                                          Review (up to N times)
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type aliases for phase callables
# ---------------------------------------------------------------------------

ResearcherFn = Callable[[str, str], str]
"""``researcher(topic, notes) → research findings``."""

WriterFn = Callable[[str, str, str | None, str | None], str]
"""``writer(topic, research, feedback, previous_draft) → draft``."""

PublisherFn = Callable[[str, str, list[str] | None, str | None], dict]
"""``publisher(title, content, tags, slug) → {slug, url} or {error}``.

``slug=None`` creates a new publication; ``slug=<existing>`` updates in place.
"""

ReviewerFn = Callable[[str, str | None, int], str]
"""``reviewer(draft, published_url, pass_number) → review text``.

The review must START with ``APPROVED`` (case-insensitive, bold markers OK)
to signal approval. Anything else is treated as revision feedback.
"""

StatusFn = Callable[[str], None]
"""Optional status-update callback (e.g. for posting to TeamWork)."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class PipelineResult:
    """Outcome of a synthesis pipeline run."""
    status: str  # "approved" | "exhausted" | "research_failed" | "write_failed" | "publish_failed"
    title: str
    slug: str = ""
    url: str = ""
    passes: int = 0
    last_review: str = ""
    error: str = ""
    draft: str = ""

    def summary(self, item_kind: str = "Draft") -> str:
        """Build a user-facing summary string for the orchestrator."""
        if self.status == "approved":
            return (
                f"{item_kind} published and approved after {self.passes} review pass(es).\n\n"
                f"**{self.title}**\n{self.url}\n\n"
                f"Reviewer verdict: {self.last_review[:500]}"
            )
        if self.status == "exhausted":
            return (
                f"{item_kind} published after {self.passes} revision cycle(s) "
                f"(reviewer did not fully approve).\n\n"
                f"**{self.title}**\n{self.url}\n\n"
                f"Last review feedback:\n{self.last_review[:500]}"
            )
        if self.status == "research_failed":
            return f"Research phase failed: {self.error}"
        if self.status == "write_failed":
            return f"Writing phase failed: {self.error}"
        if self.status == "publish_failed":
            return f"Publishing failed: {self.error}"
        return f"{item_kind} pipeline ended with status '{self.status}': {self.error}"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass
class SynthesisPipeline:
    """Orchestrates the research → write → publish → review → revise loop.

    Phases are injected as callables so the same pipeline can power
    different content types (blog posts, deep-dive notes, course modules).
    """

    researcher: ResearcherFn
    writer: WriterFn
    publisher: PublisherFn
    reviewer: ReviewerFn

    max_revisions: int = 3
    status_callback: StatusFn | None = None
    item_kind: str = "Draft"  # "Blog post", "Note", "Course module", etc.
    research_failure_markers: tuple[str, ...] = ("failed", "error:", "could not")
    write_failure_markers: tuple[str, ...] = ("failed", "error:")

    # Optional: pre-fetched research can be passed in, skipping the research phase.
    # Useful when the caller already has the source material (e.g. a fetched URL).
    skip_research: bool = False
    pre_fetched_research: str = field(default="")

    def _status(self, message: str) -> None:
        """Emit a status update via the callback if one is configured."""
        if self.status_callback:
            try:
                self.status_callback(message)
            except Exception:
                logger.debug("Status callback failed", exc_info=True)

    @staticmethod
    def _is_approved(review: str) -> bool:
        """Check if a review text signals approval.

        Must START with APPROVED (possibly wrapped in bold markers).
        """
        first_line = review.strip().split("\n")[0].strip()
        cleaned = first_line.replace("*", "").replace("_", "").strip().upper()
        return cleaned.startswith("APPROVED")

    @staticmethod
    def _derive_title(topic: str) -> str:
        """Extract a title from the topic: first non-empty line, capped at 120 chars."""
        for line in topic.strip().split("\n"):
            stripped = line.strip()
            if stripped:
                return stripped[:120]
        return "Untitled"

    def _looks_failed(self, output: str, markers: tuple[str, ...]) -> bool:
        """Check if a phase output looks like a failure message."""
        if not output:
            return True
        head = output.lower()[:100]
        return any(marker in head for marker in markers)

    def run(
        self,
        topic: str,
        notes: str = "",
        tags: list[str] | None = None,
    ) -> PipelineResult:
        """Execute the full synthesis pipeline and return a structured result.

        Args:
            topic: What to write about. First line becomes the title.
            notes: Optional additional context, instructions, or raw source.
            tags: Optional tags passed to the publisher on first publish.
        """
        title = self._derive_title(topic)
        tag_list = list(tags) if tags else []

        self._status(f"Starting {self.item_kind.lower()} pipeline: *{title}*")

        # --- Phase 1: Research ---
        if self.skip_research:
            research = self.pre_fetched_research
            logger.info("Pipeline — skipping research phase, using pre-fetched content")
        else:
            self._status("Researching topic...")
            research = self.researcher(topic, notes)
            if self._looks_failed(research, self.research_failure_markers):
                return PipelineResult(
                    status="research_failed",
                    title=title,
                    error=research,
                )

        # --- Phase 2: Write first draft ---
        self._status("Writing first draft...")
        draft = self.writer(topic, research, None, None)
        if self._looks_failed(draft, self.write_failure_markers):
            return PipelineResult(
                status="write_failed",
                title=title,
                error=draft,
            )

        # --- Phase 3: Publish initial draft ---
        pub = self.publisher(title, draft, tag_list, None)
        if "error" in pub:
            return PipelineResult(
                status="publish_failed",
                title=title,
                draft=draft,
                error=pub["error"],
            )

        slug = pub.get("slug", "")
        url = pub.get("url", "")
        self._status(f"First draft published: {url}")

        # --- Phase 4: Review → Revise loop ---
        review = ""
        for pass_num in range(1, self.max_revisions + 1):
            self._status(f"Review pass {pass_num}/{self.max_revisions}...")
            review = self.reviewer(draft, url, pass_num)

            if self._is_approved(review):
                self._status(f"Approved on pass {pass_num}! Final URL: {url}")
                logger.info("Pipeline — APPROVED on pass %d", pass_num)
                return PipelineResult(
                    status="approved",
                    title=title,
                    slug=slug,
                    url=url,
                    passes=pass_num,
                    last_review=review,
                    draft=draft,
                )

            # Revise based on feedback (only if we have another pass to go).
            if pass_num == self.max_revisions:
                break

            self._status(f"Revising based on feedback (pass {pass_num})...")
            revised = self.writer(topic, research, review, draft)
            if self._looks_failed(revised, self.write_failure_markers):
                logger.warning("Revision failed on pass %d: %s", pass_num, revised[:100])
                break
            draft = revised

            # Re-publish the updated draft.
            pub = self.publisher(title, draft, tag_list, slug)
            if "url" in pub:
                url = pub["url"]

        # --- Exhausted ---
        self._status(f"Published after {self.max_revisions} revision cycles: {url}")
        return PipelineResult(
            status="exhausted",
            title=title,
            slug=slug,
            url=url,
            passes=self.max_revisions,
            last_review=review,
            draft=draft,
        )
