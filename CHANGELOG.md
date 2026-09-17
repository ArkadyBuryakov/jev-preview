# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-18

First public release.

### Added

- Three-pane TUI for the TypeSafe System One API: context, a structured questions form,
  and responses rendered as probability bars over the raw JSON.
- API key onboarding on first run, stored with `0600` permissions in the user config
  directory; `ctrl+k` changes it later, and `TYPESAFE_API_KEY` overrides it without
  ever being written to disk.
- Saved requests in the user data directory, named after the first line of their context
  until renamed, each carrying its full response history.
- Requests catalogue (`o`) with live filtering and deletion, request deletion (`d`),
  copying (`c`), renaming (`r`), and model cycling (`m`).
- `$EDITOR` escape hatch (`E`) for both panes, keeping unparsed questions JSON verbatim
  rather than discarding it.
- `--version`, `--set-key`, `--show-config` and `--requests-dir` on the command line.
- Configurable base URL, model list and request timeout in `config.json`.

[Unreleased]: https://github.com/arkadyb/jev-preview/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/arkadyb/jev-preview/releases/tag/v0.1.0
