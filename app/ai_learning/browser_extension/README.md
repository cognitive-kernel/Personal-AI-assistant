# Chrome Learning Bridge

This is an optional, user-confirmed Manifest V3 extension. It connects the local learning module to the user's existing ChatGPT browser session without reading cookies, passwords or session tokens.

## Install locally

1. Start Personal-AI-assistant on localhost and enable the module with AI_LEARNING_ENABLED=1.
2. Open chrome://extensions.
3. Enable Developer mode and choose Load unpacked.
4. Select this directory: app/ai_learning/browser_extension.
5. Open the local module at http://127.0.0.1:8000/ai-learning and create a course.
6. Click the extension icon while on ChatGPT. Choose a course and either outline or approved topic(s).
7. The extension opens ChatGPT and inserts the prepared prompt into the composer. Review the prompt and click ChatGPT's Send button manually.
8. After ChatGPT finishes, open the extension popup and click Capture latest answer. The captured answer is saved as an unreviewed run; outlines remain pending approval and lesson versions remain unpublished.

## Safety and limitations

- The extension never clicks ChatGPT's Send button automatically.
- It captures only the latest visible assistant response after checking for a visible generation/stop control.
- DOM selectors may change. If the composer or response cannot be identified confidently, the operation stops and reports an error.
- Only ChatGPT's supported visible UI is used; no private API or hidden endpoint is called.
- The local API remains loopback-only and the module is disabled by default.
- Browser extension integration is not yet a durable background queue. If the tab closes or the browser crashes, the run remains visible in the local database and may need manual recovery.
- Batch lesson generation requires explicit topic markers in the response. Ambiguous output is retained for review and no lesson is auto-published.
- Do not use this bridge to submit confidential content unless the user understands the provider's data handling terms.
