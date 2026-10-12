# Repository Guidelines

## Project Structure & Module Organization

Music TUI uses distribution name `music-tui` and import package `music_tui`. QQ Music and NetEase Cloud Music are supported.

Source lives in `src/music_tui/`: `app.py` owns the Textual interface and playback queue, `screens.py` contains feature dialogs, `session.py` saves platform preferences and account-scoped playback sessions, `models.py` defines shared values, `sources/qqmusic.py` and `sources/netease.py` own platform access and credentials, and `player.py` controls mpv through a Unix socket. Tests live in `tests/`, grouped by behavior. `docs/tui-preview.png` shows the interface. `pyproject.toml` declares dependencies; `uv.lock` pins versions. Consult `docs/python-project-structure.md` when changing module boundaries.

## Build, Test, and Development Commands

Use Python 3.13+, uv, and mpv. On macOS: `brew install uv mpv`.

- `uv sync --locked`: install locked dependencies.
- `uv run music-tui`: start the last used platform, or QQ Music on first use.
- `uv run music-tui --source netease`: start NetEase with the local API service described in `README.md`.
- `uv build`: build distribution archives with Hatchling.
- `uv run black src tests`: format Python after every code change.
- `uv run black --check src tests`: verify formatting before delivery.
- `uv run ruff check src tests`: check configured lint rules.
- `uv run pytest -q`: run all tests.
- `uv run pytest -q tests/test_app.py`: run interface tests.

## Coding Style & Naming Conventions

Use four-space indentation and a 100-character line limit. Use `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_CASE` for constants. Follow adjacent annotations and async patterns. Keep UI, music access, and mpv lifecycle responsibilities separate.

Black is the formatter; Ruff is the linter. After each Python change, run Black. Before delivery, check formatting and lint. Every Python file needs a module docstring. Every function and method, including test helpers, needs a concise docstring explaining purpose and relevant behavior.

## Testing Guidelines

Use pytest and pytest-asyncio with automatic asyncio mode. Name files `test_*.py` and functions `test_<behavior>`. Exercise UI through Textual's `app.run_test()` and pilot controls. Use fake music responses for offline tests. Player integration tests require mpv and generate local silent WAV files. Cover changed behavior and important failures; no numeric coverage threshold is configured.

## Commit & Pull Request Guidelines

Keep `main` as the stable integration branch. Use `release-v<major>.<minor>.<patch>` for new features and release preparation for that version, for example `release-v0.2.0`. Create a separate `bugfix-<short-kebab-case-description>` branch from `main` for each defect, for example `bugfix-playback-retry`. Integrate completed fixes into `main` and the active release branch when applicable. Check the current branch before editing code and keep each branch's changes focused on its purpose.

Use concise, imperative commit subjects. PRs should describe resulting behavior, link relevant issues, and report checks. Include 80×24 terminal screenshots for layout changes.

## Credentials & Configuration

QQ credentials remain at `~/.local/share/qqmusic-tui/credential.json`, respecting `XDG_DATA_HOME`. Preserve `0600` permissions. NetEase cookies live at `~/.local/share/music-tui/netease/netease-credential.json` with `0600` permissions. The NetEase API service must run with `ENABLE_GENERAL_UNBLOCK=false`. Keep account data out of commits and fixtures.

## Agent Collaboration

Delegate independent parallel tasks to sub-agents with explicit inputs, outputs, file ownership, and completion criteria. The main agent integrates and verifies results. Keep tasks with shared state or sequential decisions together.
