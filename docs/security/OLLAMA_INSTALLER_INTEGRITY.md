# Ollama installer integrity and provenance

## Release metadata and asset selection

The launcher reads the latest release metadata from the fixed GitHub API endpoint `api.github.com/repos/ollama/ollama/releases/latest`. It rejects drafts, prereleases, malformed tags, unexpected asset origins, missing SHA-256 digests, and invalid asset sizes. It selects only the official platform installer asset for the current OS.

## Download controls

- Release metadata and asset downloads bypass environment-configured proxies.
- Release metadata redirects are rejected.
- Release asset redirects and pinned manifest URLs reject any userinfo component (including empty username/password), explicit empty port delimiters, invalid ports, and malformed authority syntax before a download is followed or started.
- Asset redirects are accepted only over HTTPS to the GitHub release asset hosts `github.com`, `release-assets.githubusercontent.com`, or `objects.githubusercontent.com`.
- Downloaded bytes must match both the release API's SHA-256 digest and asset size before the file is promoted or executed/opened.
- A mismatched download is deleted; an existing previously cached installer is not replaced.
- Cached installers are re-hashed before reuse, rather than trusting a size/URL manifest alone.
- The Linux install script receives the exact version from the verified release tag, not an unpinned latest version.

## Regression coverage

`tests/test_download_resume.py` covers digest success/failure, preservation of a previously verified installer, cache re-verification, size enforcement, release asset origin/digest validation, and redirect host restrictions. Existing range-resume tests continue to cover interrupted downloads.

## Linux second-stage payload verification

PR [#620](https://github.com/cognitive-kernel/Personal-AI-assistant/pull/620), merged as `a85c7e85b93c322d6a093ee7ba0319b30a6f2147`, closes the previous Linux second-stage integrity gap:

- The Linux archive is selected from the same official GitHub release manifest as `install.sh`, with the exact release tag, official asset URL, SHA-256 digest, and declared byte size.
- The launcher patches the verified upstream script's `download_and_extract` function to call the bounded, digest-verifying asset helper instead of the original unverified `curl` pipeline.
- Unknown upstream script structure, missing/untrusted assets, and unsupported architectures fail closed.
- The verified archive is decompressed to a temporary tar file before extraction; decompression and extraction failures are surfaced rather than hidden by a shell pipeline.

Regression coverage in `tests/test_download_resume.py` includes `test_linux_installer_is_pinned_to_the_verified_release_tag`, `test_linux_installer_patch_replaces_second_stage_curl_with_verified_helper`, `test_linux_verified_asset_helper_downloads_only_pinned_release_asset`, `test_linux_verified_asset_helper_rejects_unpinned_or_untrusted_assets`, and `test_linux_installer_patch_fails_closed_when_upstream_function_changes`.

## Remaining operational limitation

The Linux payload integrity path is implemented and CI-tested, but a live Linux installation and service-start/recovery check on the intended target host have not been performed. Keep that target-host acceptance gate open; this document does not claim an end-to-end installation rehearsal.
