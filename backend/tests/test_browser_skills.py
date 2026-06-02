"""Browser skill cache: normalization, distillation, record/find, persistence, redaction."""

import os
import tempfile

import pytest

from backend.apps.agents.browser import browser_skills as sk


@pytest.fixture(autouse=True)
def _isolated_skills(monkeypatch):
    # Persist to a throwaway dir so tests never touch the real DATA_ROOT.
    d = tempfile.mkdtemp(prefix="skills_test_")
    monkeypatch.setenv("OPENSWARM_BROWSER_SKILLS_DIR", d)
    sk.clear(wipe_disk=True)
    yield d
    sk.clear(wipe_disk=True)


def test_normalize_task_is_stable_across_rewordings():
    a = sk.normalize_task('Go to http://x.com/form and type "hi" into the box, then click Send.')
    b = sk.normalize_task('type "hi" into the box click Send')
    # urls, punctuation, and filler words drop out; core tokens remain
    assert "send" in a and "type" in a and "http" not in a
    assert a == b


def test_host_of():
    assert sk.host_of("http://localhost:8901/form.html") == "localhost:8901"
    assert sk.host_of("https://docs.google.com/x") == "docs.google.com"


def _log():
    return [
        {"tool": "BrowserScreenshot", "input": {}, "ok": False},
        {"tool": "BrowserNavigate", "input": {"url": "http://h/form"}, "ok": True},
        {"tool": "BrowserType", "input": {"selector": "#msg", "text": "hello world"}, "ok": True},
        {"tool": "BrowserGetText", "input": {}, "ok": True},
        {"tool": "BrowserClickIndex", "input": {"index": 3}, "ok": True,
         "clicked_role": "button", "clicked_name": "Send"},
    ]


def test_distill_builds_robust_steps():
    steps = sk.distill_steps(_log())
    tools = [s["tool"] for s in steps]
    # reads/screenshots dropped; click_index becomes a robust click-by-name
    assert tools == ["BrowserNavigate", "BrowserType", "BrowserClickByName"]
    cbn = steps[-1]
    assert cbn["params"] == {"role": "button", "name": "Send"}


def test_distill_refuses_click_without_resolved_name():
    log = [
        {"tool": "BrowserNavigate", "input": {"url": "http://h/"}, "ok": True},
        {"tool": "BrowserClickIndex", "input": {"index": 2}, "ok": True},  # no clicked_name
    ]
    # a click we can't make robust -> no skill at all (don't record a flaky one)
    assert sk.distill_steps(log) == []


def test_distill_skips_navigate_only():
    log = [{"tool": "BrowserNavigate", "input": {"url": "http://h/"}, "ok": True}]
    assert sk.distill_steps(log) == []


def test_distill_skips_failed_steps():
    log = [
        {"tool": "BrowserType", "input": {"selector": "#m", "text": "x"}, "ok": True},
        {"tool": "BrowserClick", "input": {"selector": ".gone"}, "ok": False},
    ]
    steps = sk.distill_steps(log)
    assert [s["tool"] for s in steps] == ["BrowserType"]


def test_distill_flattens_browser_batch():
    # the agent's efficient path bundles type+press_key into one BrowserBatch;
    # the recorder must flatten those into discrete robust steps.
    log = [
        {"tool": "BrowserNavigate", "input": {"url": "http://h/form"}, "ok": True},
        {"tool": "BrowserBatch", "ok": True, "input": {"actions": [
            {"type": "type", "params": {"selector": "#msg", "text": "hello world"}},
            {"type": "press_key", "params": {"key": "Enter"}},
        ]}},
    ]
    steps = sk.distill_steps(log)
    assert [s["tool"] for s in steps] == ["BrowserNavigate", "BrowserType", "BrowserPressKey"]
    assert steps[1]["params"]["text"] == "hello world"


def test_distill_bails_on_batched_click_index():
    # a batched click_index can't be made robust (resolved name not recoverable)
    log = [
        {"tool": "BrowserBatch", "ok": True, "input": {"actions": [
            {"type": "type", "params": {"selector": "#m", "text": "x"}},
            {"type": "click_index", "params": {"index": 2}},
        ]}},
    ]
    assert sk.distill_steps(log) == []


def test_record_and_find_roundtrip():
    assert sk.record_skill("localhost:8901", "type hello and click Send", _log()) is True
    found = sk.find_skill("localhost:8901", "Please type hello and click Send")
    assert found is not None
    assert [s["tool"] for s in found["steps"]] == ["BrowserNavigate", "BrowserType", "BrowserClickByName"]


def test_find_is_host_scoped():
    sk.record_skill("a.com", "do thing now", _log())
    assert sk.find_skill("b.com", "do thing now") is None


def test_record_refuses_unrecordable_run():
    # navigate-only -> nothing stored
    assert sk.record_skill("h", "just go", [{"tool": "BrowserNavigate", "input": {"url": "http://h/"}, "ok": True}]) is False
    assert sk.find_skill("h", "just go") is None


# --- persistence + redaction ----------------------------------------------
def test_skill_persists_across_restart(_isolated_skills):
    # record, then simulate a process restart by wiping ONLY the in-memory cache;
    # find must re-load it from disk.
    assert sk.record_skill("localhost:8901", "type hello and click Send", _log()) is True
    sk.clear(wipe_disk=False)            # in-memory gone, disk intact (== restart)
    assert not sk._skills                # cache truly empty
    found = sk.find_skill("localhost:8901", "type hello and click Send")
    assert found is not None and found.get("persisted") is True
    assert [s["tool"] for s in found["steps"]] == ["BrowserNavigate", "BrowserType", "BrowserClickByName"]


def test_sensitive_text_is_NOT_persisted(_isolated_skills):
    # a skill that types an email/password must stay in-memory only (no disk file)
    log = [
        {"tool": "BrowserType", "input": {"selector": "#email", "text": "eric@example.com"}, "ok": True},
        {"tool": "BrowserClickIndex", "input": {}, "ok": True, "clicked_role": "button", "clicked_name": "Submit"},
    ]
    assert sk.record_skill("site.com", "enter email and submit", log) is True   # stored in memory
    # nothing on disk for this skill
    path = sk._skill_path("site.com", sk.normalize_task("enter email and submit"))
    assert path is not None and not os.path.exists(path)
    # and after a "restart" it's gone (was never persisted)
    sk.clear(wipe_disk=False)
    assert sk.find_skill("site.com", "enter email and submit") is None


def test_password_field_selector_blocks_persistence(_isolated_skills):
    log = [
        {"tool": "BrowserType", "input": {"selector": "input#password", "text": "hunter2"}, "ok": True},
        {"tool": "BrowserClickIndex", "input": {}, "ok": True, "clicked_role": "button", "clicked_name": "Log in"},
    ]
    sk.record_skill("site.com", "log in", log)
    assert not os.path.exists(sk._skill_path("site.com", sk.normalize_task("log in")))


def test_sensitivity_detector():
    assert sk._looks_sensitive("eric@example.com")
    assert sk._looks_sensitive("4111 1111 1111 1111")          # card-shaped
    assert sk._looks_sensitive("123-45-6789")                  # ssn
    assert sk._looks_sensitive("sk-ant-api03-abc123")          # token prefix
    assert sk._looks_sensitive("anything", selector="#pwd")    # password field
    assert sk._looks_sensitive("aB3xK9mQ2pL7wR4tY8nZ")         # long high-entropy
    assert not sk._looks_sensitive("hello world")
    assert not sk._looks_sensitive("openswarm", selector="#search")


def test_navigate_url_userinfo_and_fragment_stripped_on_disk(_isolated_skills):
    log = [
        {"tool": "BrowserNavigate", "input": {"url": "https://user:pw@site.com/app?q=1#frag"}, "ok": True},
        {"tool": "BrowserType", "input": {"selector": "#q", "text": "shoes"}, "ok": True},
    ]
    # userinfo in the URL makes the whole skill non-persistable (credentialed URL)
    sk.record_skill("site.com", "search shoes", log)
    assert not os.path.exists(sk._skill_path("site.com", sk.normalize_task("search shoes")))
    # but a clean URL with a fragment persists with the fragment stripped
    log2 = [
        {"tool": "BrowserNavigate", "input": {"url": "https://site.com/app#section"}, "ok": True},
        {"tool": "BrowserType", "input": {"selector": "#q", "text": "shoes"}, "ok": True},
    ]
    assert sk.record_skill("site.com", "search for shoes here", log2) is True
    sk.clear(wipe_disk=False)
    found = sk.find_skill("site.com", "search for shoes here")
    assert found is not None
    nav = next(s for s in found["steps"] if s["tool"] == "BrowserNavigate")
    assert "#section" not in nav["params"]["url"]


def test_format_version_mismatch_is_ignored(_isolated_skills, monkeypatch):
    sk.record_skill("v.com", "do a thing now", _log())
    sk.clear(wipe_disk=False)
    monkeypatch.setattr(sk, "_SKILL_FORMAT_VERSION", 999)  # pretend the format moved on
    assert sk.find_skill("v.com", "do a thing now") is None


# --- parameterization: "same task, different input" -----------------------
def test_quoted_value_becomes_a_slot_and_reuses_across_inputs(_isolated_skills):
    # learn from a task with a quoted value
    log = [
        {"tool": "BrowserNavigate", "input": {"url": "https://shop.com/search"}, "ok": True},
        {"tool": "BrowserType", "input": {"selector": "#q", "text": "running shoes"}, "ok": True},
        {"tool": "BrowserClickIndex", "input": {}, "ok": True, "clicked_role": "button", "clicked_name": "Search"},
    ]
    assert sk.record_skill("shop.com", 'search for "running shoes"', log) is True
    # a DIFFERENT quoted input matches the SAME skill (templated key)
    found = sk.find_skill("shop.com", 'search for "winter boots"')
    assert found is not None
    concrete = sk.rehydrate(found, 'search for "winter boots"')
    type_step = next(s for s in concrete if s["tool"] == "BrowserType")
    assert type_step["params"]["text"] == "winter boots"   # filled from the NEW task


def test_parameterized_value_is_not_persisted(_isolated_skills):
    log = [
        {"tool": "BrowserType", "input": {"selector": "#q", "text": "running shoes"}, "ok": True},
        {"tool": "BrowserClickIndex", "input": {}, "ok": True, "clicked_role": "button", "clicked_name": "Search"},
    ]
    sk.record_skill("shop.com", 'search for "running shoes"', log)
    path = sk._skill_path("shop.com", sk._sig('search for "running shoes"'))
    blob = open(path).read()
    assert "running shoes" not in blob   # the quoted value never hits disk
    assert '"value_slot": 0' in blob or '"value_slot":0' in blob


def test_rehydrate_aborts_when_slot_cannot_be_filled(_isolated_skills):
    log = [
        {"tool": "BrowserType", "input": {"selector": "#q", "text": "shoes"}, "ok": True},
        {"tool": "BrowserClickIndex", "input": {}, "ok": True, "clicked_role": "button", "clicked_name": "Go"},
    ]
    sk.record_skill("shop.com", 'search for "shoes"', log)
    found = sk.find_skill("shop.com", "search for shoes")  # no quotes -> no value to fill
    # find still matches if signatures align; rehydrate must refuse (no ghost)
    if found is not None:
        assert sk.rehydrate(found, "search for shoes") is None


def test_unquoted_text_stays_literal_backward_compatible(_isolated_skills):
    # no quotes -> behaves exactly as before (literal text, exact-ish key)
    assert sk.record_skill("localhost:8901", "type hello and click Send", _log()) is True
    found = sk.find_skill("localhost:8901", "Please type hello and click Send")
    assert found is not None
    concrete = sk.rehydrate(found, "Please type hello and click Send")
    type_step = next(s for s in concrete if s["tool"] == "BrowserType")
    assert type_step["params"]["text"] == "hello world"   # literal, unchanged


# --- skill self-awareness (list / deprecate) ------------------------------
def test_list_skills_for_host(_isolated_skills):
    sk.record_skill("shop.com", "search for shoes now", _log())
    sk.record_skill("shop.com", "add item to the cart now", _log())
    sk.record_skill("other.com", "do a thing now", _log())
    listed = sk.list_skills("shop.com")
    tasks = {x["task"] for x in listed}
    assert len(listed) == 2 and all("steps" in x and "replays" in x for x in listed)
    assert not any(t for t in tasks if t in sk.list_skills("other.com"))  # host-scoped


def test_list_skills_reads_disk_after_restart(_isolated_skills):
    sk.record_skill("shop.com", "search for shoes now", _log())
    sk.clear(wipe_disk=False)               # restart: memory gone, disk intact
    assert len(sk.list_skills("shop.com")) == 1


def test_deprecate_removes_skill_from_memory_and_disk(_isolated_skills):
    sk.record_skill("shop.com", "search for shoes now", _log())
    sig = sk._sig("search for shoes now")
    assert os.path.exists(sk._skill_path("shop.com", sig))
    # deprecate using the task_sig as list_skills would surface it
    assert sk.deprecate_skill("shop.com", sig) is True
    assert not os.path.exists(sk._skill_path("shop.com", sig))
    assert sk.find_skill("shop.com", "search for shoes now") is None


def test_deprecate_unknown_is_false(_isolated_skills):
    assert sk.deprecate_skill("shop.com", "never recorded this") is False
