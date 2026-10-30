# AI-learning language output contract

## Finding

Outline and lesson prompts included the configured course language as context, but did not consistently state that it was a hard output requirement. A model could therefore produce content in a different language while still satisfying the visible prompt structure.

## Change

- Outline generation now explicitly requires all natural-language output to use the configured course language.
- Lesson generation applies the same requirement to headings, explanations, examples, recaps, exercises, and answer keys.
- Code, commands, identifiers, product names, and exact quoted source titles may remain unchanged when required; their explanations remain in the course language.
- Regression coverage checks both Persian (`fa`) and English (`en`) prompt construction for outlines and lessons.

## Verification limits

The tests verify deterministic prompt construction only. They do not prove that a live model will obey the language requirement. Live Persian/English benchmark execution and human adjudication remain separate acceptance gates; no local or human tests were run for this change.
