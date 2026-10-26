# Chrome Learning Bridge

This is an optional, user-confirmed Manifest V3 extension. It connects the local learning module to the user's existing ChatGPT browser session without reading cookies, passwords or session tokens.

## Install locally

1. Start Personal-AI-assistant on localhost and enable the module with AI_LEARNING_ENABLED=1.
2. Open chrome://extensions.
3. Enable Developer mode and choose Load unpacked.
4. Select this directory: app/ai_learning/browser_extension.
5. After pulling newer code, return to chrome://extensions and click the extension's Reload icon; the manifest version is now 0.2.0.
6. Open the local module at http://127.0.0.1:8000/ai-learning and create a course.
7. Open the extension icon from any tab. Choose a course and either outline or one or more approved topics.
8. Click “Start; open ChatGPT and send automatically”. The extension opens the normal ChatGPT tab, inserts the prompt, and clicks the visible Send button only after this explicit user action and only when the button can be identified confidently.
9. The extension watches the visible page for a changed, stable assistant answer and saves it automatically. The dashboard tab is refreshed after successful capture. If the Send button or response completion cannot be identified, the extension shows a manual-recovery status; send the prompt yourself and use “Capture answer manually”.
10. In the dashboard, inspect the run history. If parsing needs attention, use “View and edit response” and retry processing. Outlines remain pending approval and lesson versions remain unpublished until reviewed.

## Safety and limitations

- The extension can click ChatGPT's visible Send button only after the user explicitly clicks Start in the extension popup. It does not run unsolicited prompts.
- It captures only the latest visible assistant response after checking for a visible generation/stop control.
- DOM selectors may change. If the composer or response cannot be identified confidently, the operation stops and reports an error.
- Only ChatGPT's supported visible UI is used; no private API or hidden endpoint is called.
- The local API remains loopback-only and the module is disabled by default.
- Browser extension integration is not yet a durable background queue. If the tab closes or the browser crashes, the run remains visible in the local database and may need manual recovery. The active run is stored locally, but automatic restart after a browser crash is not guaranteed.
- Batch lesson generation requires explicit topic markers in the response. Ambiguous output is retained for review and no lesson is auto-published.
- Do not use this bridge to submit confidential content unless the user understands the provider's data handling terms.
