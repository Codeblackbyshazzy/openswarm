# backend/CLAUDE.md

FastAPI orchestrator. Entry: `backend/main.py` (uvicorn `:8324`, REST `/api/*`, WS `/ws/*`, Swagger `/docs`). See root `CLAUDE.md` for repo-wide constraints.

## Coding precedences

Full precedences live in root [CLAUDE.md](../.claude/CLAUDE.md). Always: **understand the end goal before coding** (what does the user actually need?); **reuse before you write** (grep existing routes / SubApps / helpers — most needs already have one); ~300 LOC/file ceiling; downward-tree imports; no comments except WHY-non-obvious; test after meaningful changes; weigh speed, efficiency, robustness, UX, security on every change.

## Run / test

- Dev: `bash backend/run.sh` (creates `.venv/`, installs `requirements.txt`, runs uvicorn with `--reload`).
- Tests: `pip install -r requirements-dev.txt && pytest tests/`.
- `requirements-dev.txt` is deliberately kept out of `requirements.txt` so `pytest` etc. don't ship in the production DMG. Sync both files when adding deps that need to exist in either place.

## Layout

- `apps/agents/` — agent orchestration, WS manager, MCP plumbing.
  - `providers/registry.py` — resolves primary + aux models across Anthropic / OpenAI / Google / OpenRouter / custom OpenAI-compatible providers. **Always go through here; never hardcode a model ID.**
  - `mcp_preflight.py` — vague-prompt classifier that surfaces the one-click MCP-connect modal.
  - `mcp_meta_server.py`, `mcp_registry.py` — MCP discovery + registry.
  - `9router_gpt5_patch.js` — patch loaded into 9router to translate OpenAI `max_tokens` semantics.
- `apps/nine_router.py` — supervises the 9router subprocess on `:20128`.
- `apps/subscription/router.py` — OAuth + Stripe callbacks for openswarm-pro signup.
- `apps/outputs/` — view renderer (HTML/JS/CSS iframes, sandboxed Python execution).
- `auth.py` — per-install bearer token. **Generated BEFORE the HTTP bind** so the Electron shell can read it from disk; don't reorder.

## MCP gate

- Dispatch flows through `_build_mcp_servers`. This is the only place MCP tools become reachable.
- `session.active_mcps` defaults to empty; the user opts in via `MCPSearch` + `MCPActivate` (HITL).
- New MCP-related code path? It must respect this gate. Don't add side channels.
- Suggestions surfaced to users must come from the vetted/default set — not the full upstream registry.

## Providers / models

- Primary model: per-session user choice, resolved by `providers.registry`.
- Aux model (preflight, classifier, summarizers): pick the **cheap tier of the user's configured provider** — Haiku for Anthropic, GPT-5-mini for OpenAI, Gemini Flash for Google, etc. Never hardcode Haiku.

## Common pitfalls

- New endpoint? Use a pydantic request/response model and `@typechecked`.
- Pinning matters — `requirements.txt` is fully pinned for reproducibility.
- Token middleware already scrubs bearer tokens from logs; don't re-add raw logging.
- MCP bundles in `mcp-bundles/` are esbuild output; regenerate via the bundle script rather than editing.
