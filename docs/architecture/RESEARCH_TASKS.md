# Persistent research tasks

## API

- `POST /api/research/tasks` queues a durable `research.answer` task and returns its task record.
- `GET /api/tasks/{id}` returns status and the final result; the local UI polls this endpoint.
- The existing synchronous `POST /api/chat` endpoint remains available for API compatibility.
- Both the research queue route and task-control routes require a loopback client.

## Execution and checkpoints

The worker validates the message/history payload, stores only a SHA-256 digest in checkpoints, and invokes the same source-aware research pipeline used by synchronous chat. The pipeline emits checkpoints before and after memory lookup, source discovery, draft generation, claim review, repair, and experience capture.

Pause/cancel requests are observed at the next checkpoint. A currently executing network or local-model call is not forcibly interrupted; the worker settles the task after that call returns and the next checkpoint is reached. Research tasks have one execution attempt to avoid repeating expensive or side-effecting work automatically after an uncertain failure. Users can submit a new task to retry.

The persistent task result contains the final reply and message digest. The raw question and history are stored in the task payload to allow execution after the HTTP request ends; checkpoints do not store raw user text.
