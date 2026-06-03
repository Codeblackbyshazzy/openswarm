"""End-to-end integration test of the real browser agent loop.

Drives run_browser_agent() with only the two external boundaries faked: the LLM
client (scripted tool calls) and the browser executor (scripted results). Proves
the four ported behaviors fire together in the actual loop, not just in isolation:
  - goal threading into BrowserListInteractives,
  - deterministic stagnation nudges,
  - exactly-once aux-LLM adjudication at exhaustion,
  - per-domain hints written, then seeded into the system prompt next run.
"""

import asyncio
import json
import uuid

from backend.apps.agents.browser import browser_agent as BA
from backend.apps.agents.browser import browser_history as BH


# --- fake Anthropic-shaped objects -----------------------------------------
class Blk:
    def __init__(self, type, text=None, id=None, name=None, input=None):
        self.type = type; self.text = text; self.id = id; self.name = name; self.input = input


class Resp:
    def __init__(self, content, stop_reason="tool_use"):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()


class FakeLLM:
    def __init__(self, scripted):
        self.scripted = scripted; self.turn = 0; self.calls = []
        self.messages = self

    async def create(self, **kw):
        self.calls.append(kw)
        i = min(self.turn, len(self.scripted) - 1)
        self.turn += 1
        return self.scripted[i]


class FakeAux:
    def __init__(self):
        self.calls = []
        self.messages = self

    async def create(self, **kw):
        self.calls.append(kw)
        return Resp([Blk("text", "Try BrowserListInteractives then BrowserClickIndex.")], stop_reason="end_turn")


def _tu(name, **inp):
    return Blk("tool_use", id="t" + uuid.uuid4().hex[:8], name=name, input=inp)


def _rp(goal, mem="Share dialog is a cross-origin iframe; use the index list."):
    return _tu("ReportProgress", evaluation_previous="prev", working_memory=mem, next_goal=goal)


DOC_URL = "https://docs.google.com/document/d/abc/edit"


def _install(monkeypatch, primary, aux):
    # local imports inside run_browser_agent resolve from these source modules
    import backend.apps.settings.settings as settings_mod
    import backend.apps.settings.credentials as cred_mod
    import backend.apps.agents.providers.registry as reg_mod
    import backend.apps.agents.agent_manager as am_mod

    monkeypatch.setattr(settings_mod, "load_settings", lambda: {"fake": True}, raising=True)
    monkeypatch.setattr(reg_mod, "_find_builtin_model", lambda m: object(), raising=True)
    monkeypatch.setattr(reg_mod, "resolve_model_id_for_sdk", lambda m, s: "primary-x", raising=True)

    async def _aux_resolve(s, preferred_tier="haiku"):
        return ("aux-x", None)
    monkeypatch.setattr(reg_mod, "resolve_aux_model", _aux_resolve, raising=True)

    def _client_for(s, model):
        return aux if model == "aux-x" else primary
    monkeypatch.setattr(cred_mod, "get_anthropic_client_for_model", _client_for, raising=True)

    monkeypatch.setattr(BA, "load_builtin_permissions", lambda: {}, raising=True)
    monkeypatch.setattr(am_mod.agent_manager, "_sync_session_close", lambda *a, **k: None, raising=False)

    # fake WS: record browser commands, script results by action
    sent = []

    async def _send_browser_command(request_id, action, browser_id, params, tab_id=""):
        sent.append({"action": action, "params": params})
        if action == "list_interactives":
            return {"text": '1 interactive elements:\n[1]<button "Submit">', "url": DOC_URL}
        if action == "click_index":
            # frontend surfaces the clicked element's role/name for skill recording
            return {"text": "Clicked index 1", "url": DOC_URL, "clickedRole": "button", "clickedName": "Submit"}
        if action == "click_by_name":
            return {"text": f'Clicked button "{params.get("name")}"', "url": DOC_URL}
        if action == "click":
            return {"error": "Element not found: '.submit'"}
        if action == "navigate":
            return {"text": "Navigated", "url": params.get("url", DOC_URL)}
        if action == "screenshot":
            return {"text": "shot"}
        if action == "detect_webmcp":
            return {"text": "No WebMCP on this page.", "url": DOC_URL}
        if action == "list_routes":
            return {"text": "Replayable API routes:\nGET https://docs.google.com/api/docs (x3)", "url": DOC_URL}
        if action == "replay_route":
            return {"text": f"GET {params.get('url')} -> HTTP 200\n{{\"docs\": []}}", "status": 200, "url": DOC_URL}
        return {"text": "ok", "url": DOC_URL}

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(BA.ws_manager, "send_browser_command", _send_browser_command, raising=False)
    monkeypatch.setattr(BA.ws_manager, "send_to_session", _noop, raising=False)
    return sent


def test_full_loop_goal_stagnation_adjudication_and_hint_write(monkeypatch):
    BH._browser_history.clear(); BH._domain_notes.clear()
    primary = FakeLLM([
        Resp([_rp("click the Submit button"), _tu("BrowserListInteractives")]),
        Resp([_rp("click submit"), _tu("BrowserClick", selector=".s1")]),
        Resp([_rp("retry"), _tu("BrowserClick", selector=".s2")]),
        Resp([_rp("retry"), _tu("BrowserClick", selector=".s3")]),
        Resp([_rp("retry"), _tu("BrowserClick", selector=".s4")]),
        Resp([_rp("retry"), _tu("BrowserClick", selector=".s5")]),
        Resp([Blk("text", "Giving up cleanly.")], stop_reason="end_turn"),
    ])
    aux = FakeAux()
    sent = _install(monkeypatch, primary, aux)

    result = asyncio.run(BA.run_browser_agent(
        task="Share the doc with someone", browser_id="b1", model="sonnet",
    ))
    assert result["browser_id"] == "b1"

    # 1) goal threaded into the real list_interactives call
    list_calls = [c for c in sent if c["action"] == "list_interactives"]
    assert list_calls and list_calls[0]["params"].get("goal") == "click the Submit button"

    # 2) stagnation nudge injected into a tool_result (seen by a later LLM turn)
    all_msgs = json.dumps([c["messages"] for c in primary.calls])
    assert "NO PROGRESS" in all_msgs

    # 3) aux adjudication fired EXACTLY once, at exhaustion, and was injected
    assert len(aux.calls) == 1
    assert "Suggested next step" in all_msgs

    # 4) per-domain hint written from working_memory
    assert "cross-origin iframe" in BH.get_domain_note("google.com")


def test_aux_adjudication_fires_even_when_loop_detector_trips(monkeypatch):
    # Repeated IDENTICAL failing clicks trip the exact-repeat loop detector AND
    # reach stagnation exhaustion on the same turn. The aux escape hatch must
    # still fire (it was previously suppressed by the `not is_loop` guard).
    BH._browser_history.clear(); BH._domain_notes.clear()
    primary = FakeLLM([
        Resp([_rp("click submit"), _tu("BrowserListInteractives")]),
        *[Resp([_rp("retry same"), _tu("BrowserClick", selector=".same")]) for _ in range(6)],
        Resp([Blk("text", "done")], stop_reason="end_turn"),
    ])
    aux = FakeAux()
    sent = _install(monkeypatch, primary, aux)

    asyncio.run(BA.run_browser_agent(
        task="Share the doc", browser_id="b3", model="sonnet",
    ))
    all_msgs = json.dumps([c["messages"] for c in primary.calls])
    # the loop detector definitely tripped (identical tool+input+result)
    assert "LOOP DETECTED" in all_msgs
    # ...and the aux adjudication STILL fired exactly once despite that
    assert len(aux.calls) == 1
    assert "Suggested next step" in all_msgs


def test_tier1_and_tier2_tools_drive_through_the_real_loop(monkeypatch):
    # The agent can call the new tier-1 (WebMCP detect) and tier-2 (list/replay)
    # tools through the actual run_browser_agent loop, and replay threads its url.
    BH._browser_history.clear(); BH._domain_notes.clear()
    primary = FakeLLM([
        Resp([_rp("check for a faster path"), _tu("BrowserDetectWebMCP")]),
        Resp([_rp("list captured routes"), _tu("BrowserListRoutes")]),
        Resp([_rp("replay the docs route"), _tu("BrowserReplayRoute", url="https://docs.google.com/api/docs")]),
        Resp([Blk("text", "Got the data via the API.")], stop_reason="end_turn"),
    ])
    aux = FakeAux()
    sent = _install(monkeypatch, primary, aux)

    asyncio.run(BA.run_browser_agent(
        task="Read my docs list", browser_id="b4", model="sonnet",
    ))
    actions = [c["action"] for c in sent]
    assert "detect_webmcp" in actions
    assert "list_routes" in actions
    replay = next(c for c in sent if c["action"] == "replay_route")
    assert replay["params"].get("url") == "https://docs.google.com/api/docs"
    # the API response was fed back to the model on a later turn
    all_msgs = json.dumps([c["messages"] for c in primary.calls])
    assert "HTTP 200" in all_msgs


def test_skill_is_recorded_then_replayed_with_zero_llm_calls(monkeypatch):
    # Run 1: full LLM agent completes a click task -> records a skill.
    # Run 2: same task/host -> replays via the no-LLM fast path (the speed win).
    import backend.apps.agents.browser.browser_skills as SK
    SK.clear()
    BH._browser_history.clear(); BH._domain_notes.clear()
    primary = FakeLLM([
        Resp([_rp("click submit"), _tu("BrowserListInteractives")]),
        Resp([_rp("click it"), _tu("BrowserClickIndex", index=1)]),
        Resp([Blk("text", "Done, clicked Submit.")], stop_reason="end_turn"),
    ])
    aux = FakeAux()
    sent = _install(monkeypatch, primary, aux)

    # Run 1 (learns). initial_url gives the host for record+replay keying.
    r1 = asyncio.run(BA.run_browser_agent(
        task="click the Submit button", browser_id="b1", model="sonnet", initial_url=DOC_URL,
    ))
    assert not r1.get("replayed")
    assert SK.find_skill("docs.google.com", "click the Submit button") is not None
    calls_after_run1 = len(primary.calls)
    assert calls_after_run1 > 0  # run 1 used the LLM

    # Run 2 (replays). Must NOT call the LLM at all, and must use click_by_name.
    sent.clear()
    r2 = asyncio.run(BA.run_browser_agent(
        task="Please click the Submit button", browser_id="b1", model="sonnet", initial_url=DOC_URL,
    ))
    assert r2.get("replayed") is True
    assert len(primary.calls) == calls_after_run1, "run 2 must make ZERO LLM calls"
    assert any(c["action"] == "click_by_name" for c in sent), "replay should re-resolve by name"


def test_replay_falls_back_to_full_agent_when_a_step_fails(monkeypatch):
    # If the page changed and a replay step errors, we must abort replay and run
    # the full LLM agent instead (never ghost-succeed on a stale skill).
    import backend.apps.agents.browser.browser_skills as SK
    SK.clear()
    BH._browser_history.clear()
    # Pre-seed a skill whose click target no longer exists on the page.
    SK.record_skill("docs.google.com", "click the Save button", [
        {"tool": "BrowserClickIndex", "input": {"index": 1}, "ok": True,
         "clicked_role": "button", "clicked_name": "Save"},
    ])
    primary = FakeLLM([Resp([Blk("text", "handled by full agent")], stop_reason="end_turn")])
    aux = FakeAux()
    sent = _install(monkeypatch, primary, aux)
    # make click_by_name FAIL (target gone) so replay must fall back
    orig = BA.ws_manager.send_browser_command
    async def _fail_cbn(request_id, action, browser_id, params, tab_id=""):
        if action == "click_by_name":
            sent.append({"action": action, "params": params})
            return {"error": 'No element matching name="Save" on this page.'}
        return await orig(request_id, action, browser_id, params, tab_id)
    monkeypatch.setattr(BA.ws_manager, "send_browser_command", _fail_cbn, raising=False)

    r = asyncio.run(BA.run_browser_agent(
        task="click the Save button", browser_id="b1", model="sonnet", initial_url=DOC_URL,
    ))
    assert not r.get("replayed"), "must NOT report a replayed success when a step failed"
    assert any(c["action"] == "click_by_name" for c in sent), "replay was attempted"
    assert len(primary.calls) > 0, "fell back to the full LLM agent"


def test_replay_success_promotes_skill_to_trusted_through_the_loop(monkeypatch):
    # The verify gate, end to end: run 1 learns a PROBATION skill; run 2 replays
    # it successfully, which must PROMOTE it to trusted (proven by a real replay).
    import backend.apps.agents.browser.browser_skills as SK
    SK.clear()
    BH._browser_history.clear(); BH._domain_notes.clear()
    primary = FakeLLM([
        Resp([_rp("click submit"), _tu("BrowserListInteractives")]),
        Resp([_rp("click it"), _tu("BrowserClickIndex", index=1)]),
        Resp([Blk("text", "Done.")], stop_reason="end_turn"),
    ])
    aux = FakeAux()
    _install(monkeypatch, primary, aux)
    asyncio.run(BA.run_browser_agent(
        task="click the Submit button", browser_id="b1", model="sonnet", initial_url=DOC_URL,
    ))
    assert SK.find_skill("docs.google.com", "click the Submit button")["state"] == SK._PROBATION
    r2 = asyncio.run(BA.run_browser_agent(
        task="click the Submit button", browser_id="b1", model="sonnet", initial_url=DOC_URL,
    ))
    assert r2.get("replayed") is True
    assert SK.find_skill("docs.google.com", "click the Submit button")["state"] == SK._TRUSTED


def test_unproven_skill_that_fails_is_quarantined_and_never_retried(monkeypatch):
    # The anti-ghost guard, end to end: an unproven skill that fails a replay must
    # be quarantined so the NEXT run does not even attempt the (known-bad) replay,
    # it goes straight to the pure-LLM baseline. A silent re-fail would be a ghost.
    import backend.apps.agents.browser.browser_skills as SK
    SK.clear()
    BH._browser_history.clear()
    SK.record_skill("docs.google.com", "click the Save button", [
        {"tool": "BrowserClickIndex", "input": {"index": 1}, "ok": True,
         "clicked_role": "button", "clicked_name": "Save"},
    ])  # probation, unproven
    primary = FakeLLM([Resp([Blk("text", "full agent handled it")], stop_reason="end_turn")])
    aux = FakeAux()
    sent = _install(monkeypatch, primary, aux)
    orig = BA.ws_manager.send_browser_command

    async def _fail_cbn(request_id, action, browser_id, params, tab_id=""):
        if action == "click_by_name":
            sent.append({"action": action, "params": params})
            return {"error": 'No element matching name="Save" on this page.'}
        return await orig(request_id, action, browser_id, params, tab_id)
    monkeypatch.setattr(BA.ws_manager, "send_browser_command", _fail_cbn, raising=False)

    # Run 1: replay is attempted, the step fails -> skill is quarantined.
    asyncio.run(BA.run_browser_agent(
        task="click the Save button", browser_id="b1", model="sonnet", initial_url=DOC_URL,
    ))
    assert any(c["action"] == "click_by_name" for c in sent), "run 1 DID attempt the replay"
    assert SK.list_skills("docs.google.com")[0]["state"] == SK._QUARANTINE

    # Run 2: the quarantined skill must NOT be replayed again.
    sent.clear()
    r2 = asyncio.run(BA.run_browser_agent(
        task="click the Save button", browser_id="b1", model="sonnet", initial_url=DOC_URL,
    ))
    assert not r2.get("replayed")
    assert not any(c["action"] == "click_by_name" for c in sent), \
        "a quarantined skill must never be replayed again (would be a ghost re-fail)"


def test_perception_is_frontloaded_into_first_turn(monkeypatch):
    # With a known start URL, the agent should prefetch the element list + page
    # text and put them in the FIRST user message, so the model can act on turn 1
    # instead of spending early turns orienting.
    BH._browser_history.clear(); BH._domain_notes.clear()
    primary = FakeLLM([Resp([Blk("text", "done")], stop_reason="end_turn")])
    aux = FakeAux()
    _install(monkeypatch, primary, aux)
    asyncio.run(BA.run_browser_agent(
        task="click submit", browser_id="bp", model="sonnet", initial_url=DOC_URL,
    ))
    first_user = primary.calls[0]["messages"][0]["content"]
    text = first_user if isinstance(first_user, str) else json.dumps(first_user)
    # the fake list_interactives returns a "[1]<button ...>" listing
    assert "Interactive elements already on the page" in text
    assert "act directly" in text


def test_prompt_caching_markers_present(monkeypatch):
    # The fixed system+tools prefix must carry cache_control so it's cached
    # across turns (the first-run speed/cost win). Without the marker the
    # ~4k-token prefix is reprocessed every turn.
    BH._browser_history.clear(); BH._domain_notes.clear()
    primary = FakeLLM([Resp([Blk("text", "done")], stop_reason="end_turn")])
    aux = FakeAux()
    _install(monkeypatch, primary, aux)
    asyncio.run(BA.run_browser_agent(task="hi", browser_id="bz", model="sonnet"))
    call = primary.calls[0]
    sys = call["system"]
    assert isinstance(sys, list) and sys[-1]["cache_control"]["type"] == "ephemeral"
    tools = call["tools"]
    assert tools[-1].get("cache_control", {}).get("type") == "ephemeral"
    # exactly one cache marker on the tools array (Anthropic allows <=4; we use 1)
    assert sum(1 for t in tools if t.get("cache_control")) == 1


def test_agent_can_list_and_deprecate_its_own_skills(monkeypatch):
    # The agent calls BrowserListSkills + BrowserDeprecateSkill inline (backend-
    # handled, never sent to the webview), giving it agency over its own memory.
    import backend.apps.agents.browser.browser_skills as SK
    SK.clear()
    BH._browser_history.clear()
    # pre-seed a skill on this host
    SK.record_skill("docs.google.com", "share the doc now", [
        {"tool": "BrowserClickIndex", "input": {}, "ok": True, "clicked_role": "button", "clicked_name": "Share"},
    ])
    primary = FakeLLM([
        Resp([_rp("check what i know here"), _tu("BrowserListSkills")]),
        Resp([_rp("that one is stale, drop it"), _tu("BrowserDeprecateSkill", task="share the doc now")]),
        Resp([Blk("text", "Pruned the stale shortcut.")], stop_reason="end_turn"),
    ])
    aux = FakeAux()
    sent = _install(monkeypatch, primary, aux)
    asyncio.run(BA.run_browser_agent(
        task="manage my shortcuts", browser_id="bm", model="sonnet", initial_url=DOC_URL,
    ))
    # neither inline tool is sent to the webview executor
    assert not any(c["action"] in ("list_skills", "deprecate_skill") for c in sent)
    # the LLM saw the skill listing, then the deprecate confirmation
    all_msgs = json.dumps([c["messages"] for c in primary.calls])
    assert "Learned shortcuts for docs.google.com" in all_msgs
    assert "Removed the stale shortcut" in all_msgs
    # and the skill is actually gone
    assert SK.find_skill("docs.google.com", "share the doc now") is None


def test_prior_domain_hint_is_seeded_into_system_prompt(monkeypatch):
    BH._browser_history.clear(); BH._domain_notes.clear()
    BH.set_domain_note("google.com", "REMEMBERED: Share button is index 43; Tab into the dialog.")
    primary = FakeLLM([Resp([Blk("text", "done")], stop_reason="end_turn")])
    aux = FakeAux()
    _install(monkeypatch, primary, aux)

    asyncio.run(BA.run_browser_agent(
        task="open the doc", browser_id="b2", model="sonnet", initial_url=DOC_URL,
    ))
    assert primary.calls, "LLM should have been called"
    # system is a cached content-block list (prompt caching); flatten its text
    system = primary.calls[0]["system"]
    system_text = system if isinstance(system, str) else " ".join(b.get("text", "") for b in system)
    assert "Notes from a previous visit" in system_text
    assert "REMEMBERED: Share button is index 43" in system_text
    # the cached system block carries the cache_control marker
    if isinstance(system, list):
        assert system[-1].get("cache_control", {}).get("type") == "ephemeral"
    assert len(aux.calls) == 0  # no exhaustion, no adjudication on a clean run
