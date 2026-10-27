# AI Learning: long-lesson continuation

## Implementation checklist

- [x] Lesson prompts request substantial, detailed parts of approximately 1200–1800 words instead of forcing a broad topic into one answer.
- [x] The full lesson is the single source of truth: never generate a short lesson first or replace missing sections with a summary.
- [x] An optional summary is derived only from the complete saved lesson and never overwrites or triggers regeneration of the full text.
- [x] Every response part is instructed to end with `LESSON_STATUS: CONTINUE_REQUIRED` or `LESSON_STATUS: COMPLETE`.
- [x] Browser automation continues the same visible ChatGPT conversation when continuation is requested or the status marker is missing.
- [x] Captured parts are merged and internal status markers are removed before the lesson is stored.
- [x] Automatic continuation is capped at eight follow-up responses to avoid an infinite loop.
- [x] If completion cannot be confirmed, the run remains recoverable as `needs_user_action` and the accumulated partial response is preserved rather than silently accepted as complete.
- [x] Regression tests cover the prompt contract and completion-marker parsing/removal.

## Operational limits

The status marker is a completion guard, not proof that every fact is correct. Provider limits, network failures, browser changes, or a model that ignores the marker can still interrupt generation. In those cases, review the run history and use the dashboard recovery controls. Review lesson content before publishing it.
