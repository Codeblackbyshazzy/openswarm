"""Screenshot pruning: keep first + previous + current, collapse the rest.

Vision images are ~1.3-2k tokens each and the model re-reads every one on every
turn; pruning to 3 anchors (first/previous/current) and stubbing the rest cuts the
re-prefilled image tokens without losing the agent's memory (URL + ReportProgress
text stay). These pin the keep-set, the in-place mutation, and tool_result safety.
"""

from backend.apps.agents.browser.browser_history import (
    prune_old_screenshots,
    _OMITTED_SCREENSHOT_STUB,
)


def _img(tag):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": tag}}


def _shot_turn(tag, url):
    # mirrors _format_tool_result for BrowserScreenshot: [image, text(url)]
    return {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": f"t_{tag}",
        "content": [_img(tag), {"type": "text", "text": f"Screenshot captured. URL: {url}"}],
    }]}


def _count_images(messages):
    n = 0
    for m in messages:
        for b in m.get("content", []):
            if isinstance(b, dict):
                if b.get("type") == "image":
                    n += 1
                elif b.get("type") == "tool_result":
                    n += sum(1 for x in b.get("content", []) if isinstance(x, dict) and x.get("type") == "image")
    return n


def test_keeps_first_and_last_two_collapses_middle():
    msgs = [_shot_turn(str(i), f"https://site/{i}") for i in range(5)]  # images 0..4
    collapsed = prune_old_screenshots(msgs)
    assert collapsed == 2  # 5 images - (first + last 2) = 2 stubbed
    assert _count_images(msgs) == 3
    # image tags that survive are 0 (first), 3 and 4 (last two)
    surviving = [b["source"]["data"] for m in msgs for tr in m["content"]
                 for b in tr["content"] if b.get("type") == "image"]
    assert surviving == ["0", "3", "4"]


def test_three_or_fewer_is_a_noop():
    msgs = [_shot_turn(str(i), f"u{i}") for i in range(3)]
    assert prune_old_screenshots(msgs) == 0
    assert _count_images(msgs) == 3


def test_stub_preserves_the_url_text_block():
    msgs = [_shot_turn(str(i), f"https://site/{i}") for i in range(4)]
    prune_old_screenshots(msgs)
    # the collapsed shot (#1) keeps its "URL:" text, only the image became a stub
    collapsed_tr = msgs[1]["content"][0]["content"]
    assert any(b.get("text") == _OMITTED_SCREENSHOT_STUB for b in collapsed_tr)
    assert any("URL: https://site/1" in b.get("text", "") for b in collapsed_tr)


def test_handles_direct_image_blocks_too():
    msgs = [
        {"role": "user", "content": [_img("a"), {"type": "text", "text": "hi"}]},
        {"role": "user", "content": [_img("b")]},
        {"role": "user", "content": [_img("c")]},
        {"role": "user", "content": [_img("d")]},
    ]
    collapsed = prune_old_screenshots(msgs)
    assert collapsed == 1  # keep a (first), c+d (last two); stub b
    assert msgs[1]["content"][0] == {"type": "text", "text": _OMITTED_SCREENSHOT_STUB}


def test_keep_recent_is_tunable():
    msgs = [_shot_turn(str(i), f"u{i}") for i in range(6)]
    prune_old_screenshots(msgs, keep_first=False, keep_recent=1)
    # only the most recent survives
    assert _count_images(msgs) == 1
