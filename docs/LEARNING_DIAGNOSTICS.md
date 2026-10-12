# Learning diagnostics log

The application creates a local log at `data/logs/learning.log` when the server starts. It records failures in the learning job and experience loop, including web-research and model-revision errors. Each line is a JSON record; tracebacks are included when available.

## Get the log

1. Start the app normally.
2. Open `http://127.0.0.1:8000/api/learning/diagnostics` to see the active path and file size.
3. Open `http://127.0.0.1:8000/api/learning/diagnostics/log` to download the current log, or attach `data/logs/learning.log` from the project folder.

The file rotates at 2 MB and retains three backups (`learning.log.1`, etc.). Set the `LEARNING_LOG_PATH` environment variable before starting the app to choose a different location.

## Privacy

The logger avoids storing full prompts and source documents and redacts common credential fields. Review the file before sharing it because exception messages and local paths may still contain private details. Never share `.env`, database files, passwords, or API credentials.
