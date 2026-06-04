"""
Conversation-history cache for the browser sub-agent.

Caches conversation history per browser_id so successive BrowserAgent calls on
the same browser can resume rather than restart from scratch. Without this every
"swipe right" / "swipe left" call has to take a new screenshot and re-orient
itself, costing 30-60s per action.

The `_browser_history` mutable cache lives in EXACTLY this module; all reads and
writes route through here so there's a single source of truth.
"""

# browser_id -> cached Anthropic message list for resume.
_browser_history: dict[str, list[dict]] = {}
# Cap history to prevent unbounded growth on long-lived browsers.
_MAX_HISTORY_MESSAGES = 30

# Per-apex-domain advisory notes, distilled from the agent's own ReportProgress
# working_memory. Process-lifetime only (never written to disk); seeds a later
# agent on the same domain so it skips re-learning the same quirks. Advisory
# text only, never auto-executed.
_domain_notes: dict[str, str] = {}
_MAX_DOMAIN_NOTE_CHARS = 600


def get_domain_note(domain: str) -> str:
    """Return the advisory note for a domain, or empty string if none."""
    return _domain_notes.get(domain, "")


def set_domain_note(domain: str, note: str) -> None:
    """Store/overwrite the advisory note for a domain (trimmed + capped)."""
    if not domain or not note or not note.strip():
        return
    _domain_notes[domain] = note.strip()[:_MAX_DOMAIN_NOTE_CHARS]


def clear_browser_history(browser_id: str) -> None:
    """Drop cached conversation history for a browser (e.g. when it's closed)."""
    _browser_history.pop(browser_id, None)


_OMITTED_SCREENSHOT_STUB = "[earlier screenshot omitted to save context]"


def _iter_image_block_refs(messages: list[dict]):
    """Yield (container_list, index) for every image block, in document order.

    Screenshots live either directly in a message's content list or nested inside
    a tool_result block's content list; handle both so nothing slips through.
    """
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for i, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "image":
                yield (content, i)
            elif block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                for j, inner in enumerate(block["content"]):
                    if isinstance(inner, dict) and inner.get("type") == "image":
                        yield (block["content"], j)


def prune_old_screenshots(messages: list[dict], keep_first: bool = True, keep_recent: int = 2) -> int:
    """Collapse stale screenshot images to a one-line text stub, in place.

    A vision image is ~1.3-2k tokens and the model re-reads EVERY one on EVERY
    turn, so a task that screenshots a handful of times quietly re-prefills them
    all each loop (measured ~2.9x the image tokens, ~5x the bytes uploaded per
    turn). We keep only the orientation anchor (first) plus the `keep_recent` most
    recent shots (previous + current) and swap the rest for a marker; the URL and
    the agent's own ReportProgress already carry where/what, so only the pixels
    are dropped, not the memory. If the agent must re-see, it just re-screenshots.
    Returns how many images were collapsed.
    """
    refs = list(_iter_image_block_refs(messages))
    keep_count = keep_recent + (1 if keep_first else 0)
    if len(refs) <= keep_count:
        return 0
    keep: set[int] = set()
    if keep_first:
        keep.add(0)
    for k in range(1, keep_recent + 1):
        keep.add(len(refs) - k)
    collapsed = 0
    for idx, (container, i) in enumerate(refs):
        if idx in keep:
            continue
        container[i] = {"type": "text", "text": _OMITTED_SCREENSHOT_STUB}
        collapsed += 1
    return collapsed


def _validate_message_pairing(messages: list[dict]) -> bool:
    """Verify every tool_result references a tool_use_id from a prior assistant
    message in the same list. Returns False if there's an orphan, which means
    the cached history would 400 if sent to the API.

    This is the last line of defense against cache corruption; if it ever
    returns False on a resume, we drop the cache and start fresh rather than
    crash on the next API call.
    """
    declared_tool_use_ids: set[str] = set()
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tu_id = block.get("id")
                    if tu_id:
                        declared_tool_use_ids.add(tu_id)
        elif role == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tr_id = block.get("tool_use_id")
                    if tr_id and tr_id not in declared_tool_use_ids:
                        return False
    return True


def _is_fresh_user_message(msg: dict) -> bool:
    """A 'fresh' user message starts a new turn; string content or a list
    that contains no tool_result blocks. These are the only safe cut points
    because they don't reference any prior assistant tool_use blocks."""
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list) and not any(
        isinstance(c, dict) and c.get("type") == "tool_result" for c in content
    ):
        return True
    return False


def _summarize_messages(messages: list[dict]) -> str:
    """Build a programmatic summary of older browser-agent messages.

    Extracts the original user task, a count of tool calls by name with their
    key parameters, the last few ReportProgress brain states, and the most
    recent assistant text. No LLM call required; this is purely structural
    extraction from the existing message history.
    """
    if not messages:
        return ""

    # Find the original user task (first user-text message)
    initial_task = ""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                initial_task = content.strip()[:300]
                break

    # Count tool calls by name with key params
    tool_call_summary: dict[str, list[str]] = {}
    brain_states: list[str] = []
    last_assistant_text = ""

    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                name = block.get("name", "unknown")
                inp = block.get("input") or {}
                if name == "ReportProgress":
                    # Capture the brain state for inline summary
                    brain_states.append(
                        f"  • {inp.get('next_goal', '')[:120]}"
                    )
                    continue
                # Compact one-line description with key params
                key_param = ""
                for k in ("index", "key", "url", "selector", "direction", "text"):
                    if k in inp:
                        v = str(inp[k])[:40]
                        key_param = f"{k}={v}"
                        break
                desc = f"{name}({key_param})" if key_param else name
                tool_call_summary.setdefault(name, []).append(desc)
            elif btype == "text":
                txt = block.get("text", "").strip()
                if txt:
                    last_assistant_text = txt

    # Build the summary text
    parts = ["[Summary of earlier browser-agent activity]"]
    if initial_task:
        parts.append(f'Original task: "{initial_task}"')
    if tool_call_summary:
        total = sum(len(v) for v in tool_call_summary.values())
        parts.append(f"Actions taken ({total} total):")
        # Show count + a couple of representative examples per tool
        for name in sorted(tool_call_summary.keys()):
            calls = tool_call_summary[name]
            count = len(calls)
            sample = calls[-1]  # most recent example
            if count == 1:
                parts.append(f"  - {sample}")
            else:
                parts.append(f"  - {sample} (×{count})")
    if brain_states:
        parts.append("Recent intents:")
        parts.extend(brain_states[-5:])  # last 5 brain states
    if last_assistant_text:
        snippet = last_assistant_text[:400]
        parts.append(f"Last update from assistant: {snippet}")
    parts.append(
        "(Earlier turn-by-turn details have been compacted to keep the "
        "context window manageable. Continue from where you left off.)"
    )
    return "\n".join(parts)


def _trim_history_by_turns(messages: list[dict], max_messages: int) -> list[dict]:
    """Compact message history when it exceeds max_messages.

    The Anthropic API requires every `tool_result` block to reference a
    `tool_use_id` from a previous assistant message. Naive slicing can drop
    a tool_use while keeping its tool_result, causing 400 errors. This
    function avoids that by:

    1. Walking forward to find a clean turn boundary (a fresh user-text
       message that starts a new turn; no tool_result content).
    2. Summarizing everything BEFORE that boundary into a single user-text
       message and prepending it to the kept tail.
    3. If no clean boundary exists at all, returning the original history
       unchanged. Better to temporarily exceed the cap than to corrupt the
       conversation and 400 every subsequent request.

    The summary is built programmatically (no LLM call) from the message
    structure: original task, tool call counts, recent ReportProgress brain
    states, and last assistant text.
    """
    if len(messages) <= max_messages:
        return list(messages)

    target_tail_size = max_messages - 1  # leave room for the summary message
    cut_index: int | None = None

    # First pass: walk forward looking for the EARLIEST clean cut point that
    # gets us under the cap. This preserves the most recent detail.
    for i in range(1, len(messages)):
        if not _is_fresh_user_message(messages[i]):
            continue
        if len(messages) - i <= target_tail_size:
            cut_index = i
            break

    # Second pass: if no cut point gets us under the cap (e.g. the current
    # turn alone is bigger than max_messages), use the LATEST clean cut point
    # available. The tail will still exceed the cap, but it's the smallest
    # safe history we can produce; and any compaction is better than none.
    if cut_index is None:
        for i in range(len(messages) - 1, 0, -1):
            if _is_fresh_user_message(messages[i]):
                cut_index = i
                break

    if cut_index is None:
        # No clean cut anywhere in the history. Return original; better to
        # exceed the cap than to corrupt the conversation.
        return list(messages)

    # Compact: summarize messages[0..cut_index-1], prepend as a single
    # user-text message, then keep messages[cut_index..end] verbatim.
    summary_text = _summarize_messages(messages[:cut_index])
    summary_msg = {"role": "user", "content": summary_text}
    return [summary_msg] + list(messages[cut_index:])
