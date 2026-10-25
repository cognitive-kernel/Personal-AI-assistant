# Adaptive learning recommendations

The local learning dashboard can request a deterministic ranked list of next steps from:

1. Due spaced-repetition reviews, ranked by overdue age.
2. Topics already in progress, so the learner can continue before starting another lesson.
3. Completed topics with assessment scores below the current 80-point threshold, unless a future spaced-repetition review is already scheduled.
4. The first pending topic in a path only when every earlier topic is completed.

The API is GET /api/learning/recommendations with optional limit (1-50) and path_id. The result includes a policy version, generation timestamp, recommendation reason, action label, and an explicit autonomous_action_taken=false flag.

The planner is intentionally deterministic and read-only. It does not generate lessons, change scores, mark topics complete, schedule reviews, or enqueue work. The user chooses whether to start a lesson or record a review. Assessment scores also drive the versioned SM-2-style review mapping: below 40 maps to rating 0, 40–59 to 2, 60–79 to 3, 80–89 to 4, and 90–100 to 5. This adapts review spacing but does not adapt lesson difficulty or establish semantic mastery. The ranking policy remains a baseline to evaluate against learning outcomes before any adaptive model is considered.
