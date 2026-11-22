"""Shared autonomous-engineering execution primitives and CLI entrypoints.

This package is included with the application because selected runtime modules
reuse the same cross-platform lock and state helpers as the standalone scripts.
Importing the package performs no setup and starts no background work.
