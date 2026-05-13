# frontend/CLAUDE.md

React 18 + TypeScript + webpack 5 + Redux. Entry: `src/app/Main.tsx`. Dev server on `:3000` proxies REST and WebSocket to backend on `:8324`. See root `CLAUDE.md` for repo-wide constraints.

## Coding precedences

Full precedences live in root [CLAUDE.md](../.claude/CLAUDE.md). Always: **understand the end goal before coding** (what does the user actually need?); **reuse before you write** (grep existing components / hooks / Redux slices — most needs already have one); ~300 LOC/file ceiling; downward-tree imports (`shared/` → `app/components/` → `pages/`); no comments except WHY-non-obvious; manually exercise the UI after meaningful changes; weigh speed (no double renders), efficiency, robustness, UX (loading/error/animation states), security on every change.

## Run

- Dev (full stack): `bash run.sh`.
- Dev (frontend only): `bash frontend/run.sh` — runs `npm install` then `npm run dev`.
- No JS/TS test runner is wired up. Changes must be manually exercised in the running app before merging.

## Key concepts

- **Spatial dashboard** — agents are draggable nodes on a canvas; layout + selection state lives in Redux.
- **Settings draft persistence** — `AppSettings.dismissed_mcp_suggestions` is a map of MCP id → ISO timestamp; preserve this shape when modifying settings serialization.
- **Onboarding wizard** (`src/app/components/Onboarding/`) — 8-step agentic cursor walkthrough. Cursor offsets, fit-to-view, AC popup timing, and group-meta dedup were each delicate to land; verify visually after touching this code. Note: steps 3/5/6 launch real agent sessions that hit the cloud's analytics ingest — don't treat them as visual-only.
- **SignInGate** (`src/app/components/SignInGate.tsx`, mounted in `Main.tsx`) — first-launch gate that captures `user_id` + email via Google OAuth or email magic link, hitting the cloud's `/api/auth/{google,email}/*`. Auto-dismisses for users with a valid bearer.
- **Custom providers** — `AppSettings.custom_providers: CustomProvider[]` supports any OpenAI-compatible endpoint (e.g. LM Studio).

## Conventions

- TS only; no PropTypes.
- No eslint/prettier config — match nearby files.
- Onboarding-copy placeholders shaped like real API keys (`sk-ant-api03-…`) are already allowlisted in `.gitleaks.toml`. Reuse the existing placeholder rather than introducing new "example" tokens.
- MCP suggestion UI must surface only the vetted/default set — never expose the full upstream registry to users.

## Pitfalls

- Direct LLM calls from the frontend bypass the backend's provider routing and MCP gate. Don't add them; route through `/api/*` instead.
- Webpack-dev-server hot reload occasionally loses WS state — full page reload after backend restarts.
