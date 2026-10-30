# Ollama installer integrity and provenance

## Release metadata and asset selection

The launcher reads the latest release metadata from the fixed GitHub API endpoint `api.github.com/repos/ollama/ollama/releases/latest`. It rejects drafts, prereleases, malformed tags, unexpected asset origins, missing SHA-256 digests, and invalid asset sizes. It selects only the official platform installer asset for the current OS.

## Download controls

- Release metadata and asset downloads bypass environment-configured proxies.
- Release metadata redirects are rejected.
- Asset redirects are accepted only over HTTPS to the GitHub release asset hosts `github.com`, `release-assets.githubusercontent.com`, or `objects.githubusercontent.com`.
- Downloaded bytes must match both the release API's SHA-256 digest and asset size before the file is promoted or executed/opened.
- A mismatched download is deleted; an existing previously cached installer is not replaced.
- Cached installers are re-hashed before reuse, rather than trusting a size/URL manifest alone.
- The Linux install script receives the exact version from the verified release tag, not an unpinned latest version.

## Regression coverage

`tests/test_download_resume.py` covers digest success/failure, preservation of a previously verified installer, cache re-verification, size enforcement, release asset origin/digest validation, and redirect host restrictions. Existing range-resume tests continue to cover interrupted downloads.

## Remaining limitation

On Linux, the launcher verifies the official versioned install script before executing it and pins `OLLAMA_VERSION` to that release. The script itself downloads the platform payload from Ollama's official download endpoint; the launcher does not independently re-hash that second-stage payload. The verified Windows installer and macOS archive are direct release assets. A fully verified Linux installation would require replacing the upstream script execution with a reviewed platform-specific extraction/service-install procedure or adding an independently trusted payload digest check.
