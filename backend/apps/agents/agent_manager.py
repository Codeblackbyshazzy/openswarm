import asyncio
import json
import logging
import os
import sys
from typing import Dict, List, Optional
from typeguard import typechecked

from backend.apps.agents.core.models import (
    AgentSession, Message,
)
from backend.apps.agents.core.ws_manager import ws_manager
from backend.apps.settings.settings import load_settings
from backend.apps.tools_lib.tools_lib import (
    load_all_tools as load_all_tools,
    sanitize_server_name as sanitize_server_name,
    load_builtin_permissions,
)
# SESSIONS_DIR is re-exported on purpose: session_store reads agent_manager.SESSIONS_DIR at
# call time (dodging a circular import), and the disk-resilience test monkeypatches it here.
from backend.config.paths import SESSIONS_DIR
from backend.apps.agents.manager.session.session_store import (
    save_session,
    load_session_data,
)
from backend.apps.agents.manager.streaming.state import ThinkingState, TurnState
from backend.apps.agents.manager.streaming.hook_context import HookContext
from backend.apps.agents.manager.streaming import tool_result_hook
from backend.apps.agents.manager.streaming import stop_hook as stop_hook_mod
from backend.apps.agents.manager.streaming.LivePartial import LivePartial
from backend.apps.agents.manager.prompt.system_prompt import compose_turn_system_prompt
from backend.apps.agents.tools.web import should_register_web_mcp
from backend.apps.agents.manager.permissions.effective_tools import build_effective_tool_lists
from backend.apps.agents.manager.builtin_mcp_servers import register_builtin_mcp_servers
from backend.apps.agents.manager.provider_env import configure_provider_env
from backend.apps.agents.manager.session.SessionLifecycleMixin import SessionLifecycleMixin
from backend.apps.agents.manager.session.SessionPersistenceMixin import SessionPersistenceMixin
from backend.apps.agents.manager.MessagingMixin import MessagingMixin
from backend.apps.agents.manager.SessionControlMixin import SessionControlMixin
from backend.apps.agents.manager.AgentLaunchMixin import AgentLaunchMixin
from backend.apps.agents.manager.MockAgentMixin import MockAgentMixin
from backend.apps.agents.manager.RunSupportMixin import RunSupportMixin
from backend.apps.agents.manager.permissions import gate_hooks
from backend.apps.agents.manager.run.error_cards import handle_run_error
from backend.apps.agents.manager.run.turn_runner import TurnRunnerMixin
from backend.apps.agents.manager.session.workspace_git import ensure_cwd_git_repo
from backend.apps.agents.manager.prompt.tool_catalog import (
    get_all_tool_names,
)
from backend.apps.agents.manager.session.history_compaction import (
    build_history_prefix,
    estimate_post_compact_input,
    get_branch_messages,
)
from backend.apps.agents.manager.prompt.prompt_context import resolve_mode

logger = logging.getLogger(__name__)

os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "3600000")


class AgentManager(SessionLifecycleMixin, SessionPersistenceMixin, MessagingMixin, SessionControlMixin, AgentLaunchMixin, MockAgentMixin, TurnRunnerMixin, RunSupportMixin):
    @typechecked
    def __init__(self):
        self.sessions: Dict[str, AgentSession] = {}
        self.tasks: Dict[str, asyncio.Task] = {}
        # Live mirror of the in-flight streamed assistant text per session, so a
        # stop can persist the partial reply instantly instead of waiting out the
        # multi-second SDK teardown the cancel handler sits behind.
        self.live_partial: Dict[str, LivePartial] = {}
        # Per-session cancel signal: the loop stashes its asyncio.Event here so a
        # stop/close can set it. Lives on the manager, not the AgentSession model,
        # so it stays out of serialization (an Event can't be model_dump'd).
        self.cancel_events: Dict[str, asyncio.Event] = {}




    # ------------------------------------------------------------------
    # Compaction & token guard (Phase 2)
    #
    # Triggered by *live* context-usage ratio, not turn count. The signal
    # is the same `ctx_used_pct` we already broadcast to the UI on every
    # turn: input_tokens / context_window. Three escalating thresholds:
    #   - compact_threshold_pct (default 0.65): summarize stale tool_results
    #     and old user/assistant pairs before the next query() call
    #   - context_soft_cap_pct (default 0.90): pre-send hard guard. After
    #     compaction, if still over, LRU-trim active_mcps
    #   - >= 1.0 hits the proxy/Anthropic 200K ceiling, friendly card
    #     surfaces from the catch-all
    # ------------------------------------------------------------------






    @typechecked
    async def run_agent_loop(self, session_id: str, prompt: str, images: Optional[List] = None, context_paths: Optional[List] = None, forced_tools: Optional[List[str]] = None, attached_skills: Optional[List] = None, fork_session: bool = False, selected_browser_ids: Optional[List[str]] = None, selected_app_output_ids: Optional[List[str]] = None, selected_setting_ids: Optional[List[str]] = None):
        """Run the Claude Agent SDK query loop for a session."""
        session = self.sessions.get(session_id)
        if not session:
            return
        
        from backend.apps.agents.providers.registry import get_api_type as p_get_api_type
        p_api = p_get_api_type(session.model)
        prompt_content = self.build_prompt_content(
            prompt, images, context_paths, forced_tools, attached_skills,
            api_type=p_api, model=session.model,
        )

        try:
            from claude_agent_sdk import ClaudeAgentOptions
            from claude_agent_sdk.types import HookMatcher
        except ImportError:
            logger.warning("claude_agent_sdk not installed, running in mock mode")
            await self.run_mock_agent(session_id, prompt)
            return

        session.status = "running"

        # Resolve the model id now so every closure (approval hook, tool
        # executed handler, etc.) has both the short name and the
        # 9Router-prefixed id available without re-resolving. The short
        # name is what the user sees; the router id is what 9Router
        # reports its per-model counters under.
        from backend.apps.agents.providers.registry import (
            resolve_model_id_for_sdk as p_resolve_model_id_early,
            get_api_type as p_get_api_type_early,
        )
        p_router_model_id = p_resolve_model_id_early(session.model, load_settings())
        p_api_type_for_session = p_get_api_type_early(session.model)

        builtin_perms = load_builtin_permissions()

        # Per-tool DEFAULT policy (overridden by anything the user has set
        # explicitly in builtin_permissions.json). Bash defaults to
        # always_allow like every other builtin, for a frictionless run.
        # Three guards in path_gate STILL force a prompt even on always_allow:
        # the catastrophic-pattern match (rm -rf and friends), OS-scheduling
        # (cron/launchd persistence), and the sensitive-path gate. So the
        # poisoned-email -> destructive-command case is still caught; what
        # this trades away is the prompt on ordinary shell commands. Users
        # who want a prompt on every command can flip Bash to "ask" in the UI.
        hook_ctx = HookContext(
            session=session,
            session_id=session_id,
            prompt=prompt,
            builtin_perms=builtin_perms,
            policy_defaults={},
            sessions=self.sessions,
        )

        async def can_use_tool(tool_name, input_data, context):
            return await gate_hooks.can_use_tool(hook_ctx, tool_name, input_data, context)

        async def pre_tool_hook(input_data, tool_use_id, context):
            return await gate_hooks.pre_tool_hook(hook_ctx, input_data, tool_use_id, context)

        async def post_tool_hook(input_data, tool_use_id, context):
            return await tool_result_hook.post_tool_hook(hook_ctx, input_data, tool_use_id, context)

        try:
            _, mode_sys_prompt, _ = resolve_mode(session.mode, get_all_tool_names)

            # Reconcile active_mcps against currently-enabled tools (Phase 3).
            # If the user toggled a server off in the Tools page mid-session,
            # drop it from active_mcps automatically so the model isn't told
            # "X is active" while build_mcp_servers silently filters it out.
            # Emit a context_status event so the model and UI both know.
            try:
                p_enabled = {
                    sanitize_server_name(t.name)
                    for t in load_all_tools()
                    if t.mcp_config and t.enabled and t.auth_status in ("configured", "connected")
                }
                p_stale = [s for s in session.active_mcps if s not in p_enabled]
                if p_stale:
                    session.active_mcps = [s for s in session.active_mcps if s in p_enabled]
                    session.needs_fork = True
                    await ws_manager.send_to_session(session_id, "agent:context_status", {
                        "session_id": session_id,
                        "reason": "mcp_disabled_externally",
                        "deactivated": p_stale,
                    })
                    logger.info(f"Reconciled stale active_mcps for session {session_id}: dropped {p_stale}")
            except Exception:
                logger.exception("active_mcps reconciliation failed; proceeding")

            global_settings = load_settings()
            composed_prompt = compose_turn_system_prompt(
                session,
                mode_sys_prompt,
                global_settings.default_system_prompt,
                selected_browser_ids,
                selected_app_output_ids,
                selected_setting_ids,
            )

            # Per-turn estimate of framework overhead (subtracted from displayed
            # input). Conservative on purpose so honest over-shows beat lies.
            # 16K Claude Code preset, 12K base+deferred tools, ~3K/MCP (real
            # MCP tool definitions range 1-10K depending on server; 3K is a
            # rough median that keeps the meter honest without over-trimming),
            # char/4 of composed prompt.
            p_PRESET_OVERHEAD = 16_000
            p_TOOL_DEFS_OVERHEAD = 12_000
            p_PER_MCP_OVERHEAD = 3_000
            p_composed_tokens = len(composed_prompt or "") // 4
            p_mcp_tokens = len(session.active_mcps) * p_PER_MCP_OVERHEAD
            session.framework_overhead_tokens = (
                p_PRESET_OVERHEAD + p_TOOL_DEFS_OVERHEAD + p_composed_tokens + p_mcp_tokens
            )

            # Pass session.active_mcps as the activation filter. Empty list ⇒
            # no MCP tools shipped to the SDK; the model must MCPSearch and
            # MCPActivate first. The product invariant lives here at the
            # dispatch layer (see build_mcp_servers docstring).
            mcp_servers = await self.build_mcp_servers(session.allowed_tools, session.active_mcps)

            browser_delegation_tools, invoke_agent_tools = register_builtin_mcp_servers(
                mcp_servers, session, builtin_perms, selected_browser_ids, os.path.dirname(__file__)
            )


            # Register the DDG-backed openswarm-web MCP only when the primary has no reliable
            # native Anthropic web path (decided in tools/web.py); p_m feeds the registration log
            # + provider branch just below, so it stays a loop local.
            p_m = p_router_model_id if isinstance(p_router_model_id, str) else ""
            need_web_mcp = should_register_web_mcp(
                model=session.model,
                router_model_id=p_router_model_id,
                api_type=p_api_type_for_session,
                anthropic_api_key=getattr(global_settings, "anthropic_api_key", None),
                connection_mode=getattr(global_settings, "connection_mode", "own_key"),
            )
            if need_web_mcp:
                web_mcp_server_path = os.path.join(
                    os.path.dirname(__file__), "web_mcp_server.py"
                )
                # Tell the MCP which primary the session is using so it
                # can route to that provider's native search tool.
                if p_m.startswith(("gc/", "gemini/", "ag/")):
                    p_primary_hint = "gemini"
                elif p_m.startswith("cx/"):
                    p_primary_hint = "openai"
                else:
                    p_primary_hint = ""
                from backend.auth import get_auth_token as p_get_auth_token3
                mcp_servers["openswarm-web"] = {
                    "command": sys.executable,
                    "args": [web_mcp_server_path],
                    "env": {
                        "OPENSWARM_PORT": os.environ.get("OPENSWARM_PORT", "8324"),
                        "OPENSWARM_AUTH_TOKEN": p_get_auth_token3(),
                        "OPENSWARM_PRIMARY_API": p_primary_hint,
                    },
                    "type": "stdio",
                }
                logger.info(
                    f"[MCP-DEBUG] Primary {p_m} has no reliable native web search, "
                    f"registering openswarm-web (DDG search + trafilatura fetch, free)"
                )

            effective_allowed, effective_disallowed = build_effective_tool_lists(
                session, mcp_servers, builtin_perms, need_web_mcp,
                browser_delegation_tools, invoke_agent_tools,
            )

            # Tell the model directly which web tools work for this session.
            # The Claude Code CLI's deferred-tool registry still advertises bare
            # `WebSearch` and `WebFetch` even when we've stripped them above;
            # frontier models (Claude/GPT-5/Gemini Pro) intuit the namespaced
            # MCP variant from context, but smaller open-source models (gpt-oss
            # via Ollama, smaller Llama/Qwen, etc.) thrash on the deferred-tool
            # handshake (saw 2+ minutes of repeated `ToolSearch(select:WebSearch)`
            # → empty matches → retry). Naming the working tool here cuts that
            # to a single direct call. Only injected when (a) we registered the
            # web MCP, AND (b) the user hasn't disabled the policy, matches
            # the same gate the MCP allowlist uses, so disabling WebSearch in
            # Settings still wins.
            p_web_tools_available = need_web_mcp and (
                "mcp__openswarm-web__WebSearch" in effective_allowed
                or "mcp__openswarm-web__WebFetch" in effective_allowed
            )
            if p_web_tools_available:
                p_hint_lines = ["<web_tools>"]
                p_hint_lines.append(
                    "This session does NOT have the built-in `WebSearch` / "
                    "`WebFetch` tools (they delegate to Anthropic Haiku, which "
                    "isn't reachable on this primary). Use the MCP-backed "
                    "equivalents instead, call them DIRECTLY, no ToolSearch "
                    "step needed:"
                )
                if "mcp__openswarm-web__WebSearch" in effective_allowed:
                    p_hint_lines.append(
                        "- `mcp__openswarm-web__WebSearch(query: str, "
                        "num_results?: int)`, DuckDuckGo search."
                    )
                if "mcp__openswarm-web__WebFetch" in effective_allowed:
                    p_hint_lines.append(
                        "- `mcp__openswarm-web__WebFetch(url: str, prompt?: "
                        "str)`, fetch a URL and return readable text."
                    )
                p_hint_lines.append(
                    "Do not call `ToolSearch(select:WebSearch)`, bare "
                    "`WebSearch` is unavailable on this session and that path "
                    "will return empty matches."
                )
                p_hint_lines.append("</web_tools>")
                p_web_hint = "\n".join(p_hint_lines)
                composed_prompt = (
                    f"{composed_prompt}\n\n{p_web_hint}" if composed_prompt else p_web_hint
                )

            # Log effective tool lists
            google_allowed = [t for t in effective_allowed if "google-workspace" in t]
            reddit_allowed = [t for t in effective_allowed if "reddit" in t]
            builtin_allowed = [t for t in effective_allowed if not t.startswith("mcp__")]
            logger.info(f"[MCP-DEBUG] effective_allowed: {len(effective_allowed)} total "
                        f"(builtins={len(builtin_allowed)}, google={len(google_allowed)}, reddit={len(reddit_allowed)})")
            if effective_disallowed:
                logger.info(f"[MCP-DEBUG] effective_disallowed: {effective_disallowed}")

            # `p_router_model_id` and `p_api_type_for_session` were resolved
            # at the top of run_agent_loop (before any closures were
            # defined) so analytics closures could tag events with them.
            # Reuse those values here and keep session.provider in sync.
            resolved_model = p_router_model_id
            api_type = p_api_type_for_session
            session.provider = api_type

            # Capture the Claude CLI's stderr into a buffer so the retry
            # classifier can see the real cause of a process crash (e.g.
            # "No pool capacity available" from the OpenSwarm proxy, or the
            # Anthropic SDK's 429/overloaded error body). Without this the
            # SDK's ProcessError only stringifies to "Command failed with
            # exit code 1 / Check stderr output for details", which masks
            # transient capacity issues.
            p_stderr_buffer: list[str] = []

            def p_stderr_cb(line: str) -> None:
                p_stderr_buffer.append(line)
                # Cap the buffer so a runaway subprocess can't balloon RAM.
                if len(p_stderr_buffer) > 500:
                    del p_stderr_buffer[:250]

            async def stop_hook(input_data, tool_use_id, context):
                return await stop_hook_mod.stop_hook(hook_ctx, input_data, tool_use_id, context)

            options_kwargs = {
                "model": resolved_model,
                # 64 MB ceiling on the SDK <-> CLI JSON-RPC channel. The
                # default 5 MB blocked any base64'd PDF over ~3.5 MB; we
                # now route PDFs/images as native content blocks, which
                # base64-expand by ~33%. 64 MB clears the largest single
                # Anthropic PDF (32 MB raw) with headroom for prompt +
                # tool results sharing the same frame.
                "max_buffer_size": 64 * 1024 * 1024,
                "permission_mode": "default",
                "can_use_tool": can_use_tool,
                "stderr": p_stderr_cb,
                "hooks": {
                    "PreToolUse": [HookMatcher(matcher=None, hooks=[pre_tool_hook])],
                    "PostToolUse": [HookMatcher(matcher=None, hooks=[post_tool_hook])],
                    "Stop": [HookMatcher(matcher=None, hooks=[stop_hook])],
                },
                "allowed_tools": effective_allowed,
                "disallowed_tools": effective_disallowed,
                "include_partial_messages": True,
            }
            # cc/cx/gc/ag/gemini/openrouter prefixes force 9Router; route="api"
            # bypasses to the provider's host directly; otherwise Pro proxy or key.
            await configure_provider_env(
                options_kwargs, session, resolved_model, api_type, global_settings, []
            )
            if mcp_servers:
                options_kwargs["mcp_servers"] = mcp_servers
                mcp_json_len = len(json.dumps({"mcpServers": mcp_servers}))
                logger.info(f"[MCP-DEBUG] mcp_servers passed to SDK: {list(mcp_servers.keys())}, JSON length={mcp_json_len}")
            # claude_code preset for BOTH system_prompt and tools so the CLI's
            # deferred-tools scaffolding survives. Raw string would replace it.
            options_kwargs["tools"] = {
                "type": "preset",
                "preset": "claude_code",
            }
            # exclude_dynamic_sections=True moves cwd/git/OS grounding out of
            # the cached prefix and into the first user message, unlocks
            # Anthropic prompt cache (~80% input-token cut, 13-31% faster TTFT).
            # Trade-off: grounding freezes at turn 1.
            if composed_prompt:
                options_kwargs["system_prompt"] = {
                    "type": "preset",
                    "preset": "claude_code",
                    "append": composed_prompt,
                    "exclude_dynamic_sections": True,
                }
            else:
                options_kwargs["system_prompt"] = {
                    "type": "preset",
                    "preset": "claude_code",
                    "exclude_dynamic_sections": True,
                }
            if session.max_turns:
                options_kwargs["max_turns"] = session.max_turns

            # The claude_code preset auto-attaches the user's claude.ai-
            # connected partner MCPs (`mcp__claude_ai_*`). Those bypass our
            # MCPActivate gate, don't share OAuth state with the OpenSwarm
            # Gmail/Calendar/Drive connectors the user actually configured
            # here, and confuse the model into picking the partner shim
            # instead of our vetted server. Hard-block them at the SDK
            # layer so the model can't even attempt the call.
            options_kwargs["disallowed_tools"] = [
                "mcp__claude_ai_*",
            ]

            if session.cwd:
                # Pre-existing sessions may have workspaces that predate
                # the git-init block in launch_agent, leaving them
                # without a valid HEAD. Ensure it here so subagent
                # worktree-add always works.
                ensure_cwd_git_repo(session.cwd)
                options_kwargs["cwd"] = session.cwd

            try:
                level = getattr(session, "thinking_level", "auto") or "auto"
                # Trivially short prompts ("hi", "thanks") don't benefit from
                # 5-30s of hidden reasoning. Override per-turn only, session
                # setting is untouched so the UI pill keeps reflecting the
                # user's choice.
                p_prompt_len = len((prompt or "").strip())
                if 0 < p_prompt_len < 50 and level != "off":
                    level = "off"
                # gc/gemini-3* without Antigravity 400s every multi-step turn
                # on thoughtSignature continuity. Force-disable thinking.
                if (
                    isinstance(resolved_model, str)
                    and resolved_model.startswith("gc/gemini-3")
                    and level != "off"
                ):
                    logger.info(
                        "Forcing thinking_level=off for %s (gc/ thoughtSignature isn't roundtrippable; connect Antigravity for reasoning).",
                        resolved_model,
                    )
                    level = "off"
                if api_type == "anthropic":
                    if level == "off":
                        # Fable 5 400s on an explicit thinking:disabled; you turn it
                        # off there by omitting the param (off is Fable's default).
                        if not (isinstance(resolved_model, str) and "fable" in resolved_model):
                            options_kwargs["thinking"] = {"type": "disabled"}
                    elif level in ("low", "medium", "high"):
                        options_kwargs["effort"] = level
                elif api_type in ("openai", "codex"):
                    # GPT-5 family + Codex take reasoning_effort; 9Router carries
                    # the Anthropic-shaped `effort` across to it, so the slider
                    # works for OpenAI too, not just Claude. Every OpenAI/Codex
                    # model we expose is reasoning-capable (registry has no
                    # non-reasoning ones), so no per-model gate. No "disabled"
                    # form on these, so "off" just omits the param.
                    if level in ("low", "medium", "high"):
                        options_kwargs["effort"] = level
            except Exception as e:
                logger.debug(f"thinking_level param injection skipped: {e}")

            # Fresh-restart path: some session changes must not reuse the
            # CLI's resume transcript. MCPActivate needs a new transport so
            # tool schemas are reread; branch edits/switches need the model
            # to see only get_branch_messages(session), not facts from the
            # old branch's SDK transcript. Soft restart: drop resume +
            # sdk_session_id, replay local history via the prompt, let the
            # SDK build a clean session from the current app state.
            if session.needs_fresh_session:
                if session.sdk_session_id:
                    logger.info(
                        f"Fresh-session restart for {session_id}: dropping "
                        f"sdk_session_id={session.sdk_session_id}; active_mcps={session.active_mcps}"
                    )
                    session.sdk_session_id = None
                session.needs_fresh_session = False
                session.needs_fork = False  # superseded by the fresh restart

            if session.sdk_session_id:
                options_kwargs["resume"] = session.sdk_session_id
                if fork_session or session.needs_fork:
                    options_kwargs["fork_session"] = True
                if session.needs_fork:
                    session.needs_fork = False
            elif len(session.messages) > 1:
                history = build_history_prefix(
                    get_branch_messages(session),
                    cutoff_msg_id=session.compacted_through_msg_id,
                )
                if history:
                    if isinstance(prompt_content, str):
                        prompt_content = history + "\n\n" + prompt_content
                    elif isinstance(prompt_content, list):
                        prompt_content.insert(0, {"type": "text", "text": history})

            # Compaction trigger (Phase 2). Driven by live ctx_used ratio
            # rather than turn count, fires when input_tokens/context_window
            # crosses session.compact_threshold_pct (default 0.65). Cheap,
            # programmatic summarization (no aux LLM call) so this adds
            # zero latency on the user's turn.
            try:
                if self.maybe_compact(session):
                    new_input = estimate_post_compact_input(session)
                    await ws_manager.send_to_session(session_id, "agent:context_status", {
                        "session_id": session_id,
                        "reason": "compacted",
                        "compacted_through_msg_id": session.compacted_through_msg_id,
                    })
                    await self.emit_context_update(
                        session_id,
                        session,
                        input_tokens=new_input,
                        output_tokens=session.tokens.get("output", 0),
                    )
            except Exception:
                logger.exception("compaction failed; proceeding without it")

            # Pre-send hard guard (Phase 2). After compaction, if the
            # session is still over context_soft_cap_pct of the window,
            # LRU-trim oldest active_mcps. Stops the 429 from ever
            # firing on predictable overflow paths.
            try:
                # Use the most recent measurement (the prior turn's
                # input_tokens) as the estimate. Conservative because the
                # current turn's user prompt + any new history adds on top
                #, but the first turn of a fresh session has tokens=0 so
                # we only act once we've seen real numbers.
                p_est_tokens = session.tokens.get("input", 0)
                p_hard_cap = int(session.context_window * session.context_soft_cap_pct)
                if p_est_tokens >= p_hard_cap:
                    trimmed: list[str] = []
                    while p_est_tokens >= p_hard_cap and len(session.active_mcps) > 1:
                        # Keep at least one MCP active so the model can
                        # finish whatever it was doing; trim from oldest
                        # which is FIFO order in the list.
                        trimmed.append(f"mcp:{session.active_mcps.pop(0)}")
                        p_est_tokens -= 8_000  # rough per-MCP schema cost
                    if trimmed:
                        await ws_manager.send_to_session(session_id, "agent:context_status", {
                            "session_id": session_id,
                            "reason": "trimmed",
                            "trimmed": trimmed,
                            "estimate_after": p_est_tokens,
                        })
                        # Surface a visible system breadcrumb in the chat so
                        # the user (and the model on the next turn) know
                        # which MCPs got dropped. Without this, the model
                        # may keep trying to call a now-missing tool and
                        # the user has no idea why.
                        try:
                            p_names = ", ".join(t.replace("mcp:", "") for t in trimmed)
                            p_trim_msg = Message(
                                role="system",
                                content=(
                                    f"Trimmed {len(trimmed)} app{'s' if len(trimmed) != 1 else ''} from this session to fit "
                                    f"the model's context: {p_names}. Re-activate via MCPSearch + MCPActivate "
                                    "if you still need them."
                                ),
                                branch_id=session.active_branch_id,
                            )
                            session.messages.append(p_trim_msg)
                            await ws_manager.send_to_session(session_id, "agent:message", {
                                "session_id": session_id,
                                "message": p_trim_msg.model_dump(mode="json"),
                            })
                        except Exception:
                            logger.exception("failed to emit MCP-trimmed breadcrumb")
                        # Trimming changes mcp_servers / outputs context →
                        # rebuild options. The cheapest correct path is
                        # to flag for fork on next turn via needs_fork
                        # and let the existing fork path handle it.
                        session.needs_fork = True
            except Exception:
                logger.exception("pre-send token guard failed; proceeding")

            logger.info(f"[MCP-DEBUG] Creating ClaudeAgentOptions short={session.model} resolved={resolved_model} api_type={api_type}")
            options = ClaudeAgentOptions(**options_kwargs)
            logger.info("[MCP-DEBUG] ClaudeAgentOptions created. Starting query...")

            turn = TurnState()
            thinking = ThinkingState()
            await self.p_run_turn_with_retry(
                session, session_id, prompt_content, options, options_kwargs,
                turn, thinking, p_stderr_buffer, resolved_model, api_type, global_settings,
            )
            session.status = "completed"

            # Auto-continuation hook (Phase 3). If MCPActivate (or any
            # analogous flow) flagged pending_continuation during this
            # turn, kick off a follow-up turn immediately with the
            # captured prompt. We dispatch as a fire-and-forget task so
            # the current run_agent_loop frame can unwind cleanly
            # before the next turn's options + history rebuild kicks in.
            # The follow-up is `hidden=True` so it doesn't add a user
            # bubble to the visible chat; the model sees it as a
            # synthetic prompt to keep working.
            try:
                if getattr(session, "pending_continuation", False):
                    p_continuation_prompt = session.pending_continuation_prompt or "Continue."
                    session.pending_continuation = False
                    session.pending_continuation_prompt = None
                    asyncio.create_task(self.send_message(
                        session_id,
                        p_continuation_prompt,
                        hidden=True,
                    ))
                    logger.info(f"Auto-continuing session {session_id} with hidden prompt")
            except Exception:
                logger.exception("auto-continuation dispatch failed")
        except asyncio.CancelledError:
            # Only act if we're still the session's live task. A user stop pops
            # this task (stop_agent already finalized status + partial), and a
            # follow-up message may have started a newer turn; either way this
            # dying task must NOT clobber the live status or pop the new turn's
            # in-flight partial mirror.
            if self.tasks.get(session_id) is asyncio.current_task():
                session.status = "stopped"
                # A cancelled turn desyncs the CLI's resume transcript from
                # session.messages (the SDK never recorded the interrupted
                # turn), so force the next turn to rebuild history from
                # session.messages, else resume/follow-ups replay a transcript
                # with no trace of the stopped reply ("nothing to continue").
                session.needs_fresh_session = True
                # Persist whatever streamed before the cancel (edit / branch
                # switch paths; the user-stop path already did this in stop_agent).
                await self.commit_partial_now(session)
            turn.stream_text_msg_id = None
            turn.stream_text_accum = ""
        except Exception as e:
            await handle_run_error(e, session, session_id, turn, p_stderr_buffer)
        except BaseException as e:
            # Catch BaseExceptionGroup from anyio task groups (e.g. concurrent
            # CLI crash + pending approval cancellation) so it doesn't escape
            # and kill the uvicorn process.
            logger.exception(f"Agent {session_id} fatal error: {e}")
            session.status = "error"
            error_msg = Message(role="system", content=f"Error: {str(e)}", branch_id=session.active_branch_id)
            session.messages.append(error_msg)
            await ws_manager.send_to_session(session_id, "agent:message", {
                "session_id": session_id,
                "message": error_msg.model_dump(mode="json"),
            })
        finally:
            # Only the session's live task finalizes. A stopped task (popped by
            # stop_agent, which already finalized status + saved) or one
            # superseded by a newer turn must not pop the new turn's partial
            # mirror, broadcast a stale terminal status, or overwrite the
            # snapshot the live turn is writing.
            p_is_live_task = self.tasks.get(session_id) is asyncio.current_task()
            if p_is_live_task:
                self.live_partial.pop(session_id, None)
            if session_id in self.sessions and p_is_live_task:
                # For canvas-launched App Builder sessions, the workspace
                # folder IS the session_id (see launch_agent), so meta.json
                # lives at outputs_workspace/<session_id>/meta.json. Read it
                # and propagate name/description into the Output row before
                # the terminal status fires; without this, the row stays
                # "Untitled App" forever because no React component polls
                # the file on the canvas path. Best-effort, only acts when
                # the row's name is still the default placeholder.
                if session.mode == "view-builder":
                    try:
                        from backend.apps.outputs.outputs import sync_output_from_meta_json, _load_all as load_outputs
                        if sync_output_from_meta_json(session_id, fallback_name=session.name):
                            # Broadcast the renamed row so the sidebar
                            # flips from "Untitled App" to the real name
                            # without waiting for the next mount.
                            try:
                                matching = [o for o in load_outputs() if o.workspace_id == session_id]
                                if matching:
                                    await ws_manager.broadcast_global("agent:output_upserted", {
                                        "output": matching[0].model_dump(mode="json"),
                                    })
                            except Exception:
                                logger.exception("post-sync output_upserted broadcast failed")
                    except Exception:
                        logger.exception("post-session meta sync failed")
                await ws_manager.send_to_session(session_id, "agent:status", {
                    "session_id": session_id,
                    "status": session.status,
                    "session": session.model_dump(mode="json"),
                })
                try:
                    save_session(session_id, session.model_dump(mode="json"))
                except Exception as e:
                    logger.warning(f"Failed to snapshot session {session_id}: {e}")

















agent_manager = AgentManager()
