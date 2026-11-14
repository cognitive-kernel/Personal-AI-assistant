"""Validation of persisted independent-evaluation evidence before state transitions."""

from __future__ import annotations

from .learning_topic_method_attempts import MethodAttemptError


def _assert_persisted_evaluation(
    connection,
    *,
    evaluation_id: int,
    path_topic_id: int,
    job_id: int,
    content_sha256: str,
    outcome: str,
    score: float | None,
) -> None:
    """Require the immutable evaluator record to match the requested transition."""
    row = connection.execute(
        """SELECT path_topic_id, job_id, content_sha256, outcome, score
           FROM learning_topic_quality_evaluations WHERE id=?""",
        (evaluation_id,),
    ).fetchone()
    if (
        row is None
        or int(row["path_topic_id"]) != path_topic_id
        or int(row["job_id"]) != job_id
        or row["content_sha256"] != content_sha256
        or row["outcome"] != outcome
        or (score is None and row["score"] is not None)
        or (score is not None and (
            row["score"] is None or float(row["score"]) != float(score)
        ))
    ):
        raise MethodAttemptError(
            "The persisted independent evaluation does not authorize this method transition."
        )
