# Chrome Learning Bridge

The Manifest V3 extension connects the local learning module to the user's existing ChatGPT browser session. It interacts with the visible ChatGPT page and does not read cookies, passwords, or session tokens.

## One-time setup

1. Start Personal-AI-assistant on localhost and enable the module with `AI_LEARNING_ENABLED=1`.
2. Open `chrome://extensions`, enable Developer mode, and choose **Load unpacked**.
3. Select `app/ai_learning/browser_extension`.
4. Open `http://127.0.0.1:8000/ai-learning` and select the course.

## Run a course

1. In the extension popup, choose **دریافت سرفصل‌ها** and click **شروع؛ بازکردن ChatGPT و ارسال خودکار** once to start the workflow.
2. The extension opens the normal ChatGPT page, inserts the prompt, and attempts to click the visible Send button. No manual navigation to ChatGPT, manual sending, or manual answer capture should be needed in the normal successful path.
3. When the outline answer is stable, the extension saves it to the local database. Existing topic titles are skipped using normalized, case-insensitive title matching.
4. Pending topics are approved automatically to allow unattended progression. The extension then generates lessons in small batches for approved topics that do not already have a lesson. This continues after each successfully captured batch.
5. Lesson content remains in `needs_review`; it is **not automatically published**. Review and publication remain explicit quality controls.
6. If a browser selector changes, the browser closes, ChatGPT does not finish, or the provider asks for an extra interaction, the run is retained in local history and may require recovery. These are exception paths, not the intended normal workflow.

## Duplicate protection

- Re-importing an outline does not create a second active topic with the same normalized title.
- The same outline response can add genuinely new topics while skipping existing ones.
- Lesson generation excludes topics that already have a non-deleted lesson.
- The local run request keys are idempotent for retries of the same queued batch.

## Safety and limitations

- Automation starts only after the user explicitly starts the course workflow from the extension popup; it does not launch unsolicited prompts.
- Only the visible ChatGPT UI is used; no private API or hidden endpoint is called.
- DOM selectors may change, and automatic submission/capture cannot be guaranteed if ChatGPT changes its UI or requests additional user interaction.
- The local API remains loopback-only and the module is disabled by default.
- Browser integration is not yet a durable queue across browser crashes. If the tab closes or the browser crashes, the run remains in the local database, but automatic continuation after restart is not guaranteed.
- Lesson batches require explicit topic markers in ChatGPT's response. Ambiguous output is retained for recovery; no lesson is auto-published.
- Do not submit confidential content unless the provider's data handling terms are understood.
