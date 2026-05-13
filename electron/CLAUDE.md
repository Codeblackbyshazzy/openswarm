# electron/CLAUDE.md

Electron 40.x (CastLabs DRM build) desktop shell + auto-updater via GitHub Releases. Entry: `main.js`. Version is in `package.json`. See root `CLAUDE.md` for repo-wide constraints.

## Coding precedences

Full precedences live in root [CLAUDE.md](../.claude/CLAUDE.md). Always: **understand the end goal before coding** (what does the user actually need?); **reuse before you write** (grep existing IPC handlers / helpers in `main.js` — most needs already have one); ~300 LOC/file ceiling; downward-tree imports; no comments except WHY-non-obvious; test the packaged build path after meaningful changes (not just dev); weigh speed (startup time), efficiency (memory), robustness (auto-updater, OAuth windows), UX, security (signed binaries, no plaintext secrets) on every change.

## Build / release

- Local build: `npm run build` → produces `build-staging/` containing the frontend dist, backend bundle, standalone Python 3.13, and the 9router binary. Build artifacts are ephemeral; not git-tracked.
- macOS release: requires Apple ID, app-specific password, and team ID env vars. App is signed + notarized.
- Windows release: signed via Azure code signing in CI (`.github/workflows/release-windows.yml`); triggers on `v*` tags.

## Bundling

- Python 3.13 is bundled via python-build-standalone — users do not need a system Python.
- 9router binary is pulled at build time by `scripts/fetch-router.sh` / `fetch-router.ps1`. The version pin (`0.3.60`) is load-bearing for cross-provider WebSearch — see root `CLAUDE.md`.

## Versioning

- Source of truth: `electron/package.json` `version`. Bump alongside any user-facing release; CI tags off it.
- Bump only when cutting a release — coordinate with the publish flow rather than landing version bumps speculatively.

## Pitfalls

- `build-staging/` is regenerated on every build; never commit it.
- Auto-updater reads the latest release feed from GitHub; staging/test builds should use a separate channel to avoid pushing unsigned bits to users.
