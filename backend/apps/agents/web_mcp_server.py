#!/usr/bin/env python3
"""
Stdio MCP server exposing `WebSearch` and `WebFetch` backed by the
OpenSwarm backend's free DuckDuckGo + trafilatura implementation.

Purpose: the Claude Code CLI's built-in `WebSearch` / `WebFetch` tools
wrap Anthropic's server-side `web_search_20250305` / `web_fetch_20250807`
which require a Claude credential somewhere (Claude subscription on
9Router, openswarm-pro cloud proxy, or direct Anthropic API key). Users
who only connect ChatGPT Plus or Gemini Advanced and don't have any
Claude-backed credential get "No credentials for provider: claude" from
the CLI and either see hallucinated or empty results.

This server is registered by `agent_manager.py` only in that gap case.
When it is registered, the built-in `WebSearch` / `WebFetch` are added
to `disallowed_tools` so the model picks our MCP-prefixed versions.

Proxies tool calls to the backend at /api/web/search and /api/web/fetch
so the DDG / trafilatura logic lives in one place
(`backend/apps/agents/tools/web.py`) and can be evolved without
restarting the MCP subprocess.
"""

import json
import os
import sys
import urllib.error
import urllib.request

BACKEND_PORT = os.environ.get("OPENSWARM_PORT", "8324")
SEARCH_URL = f"http://127.0.0.1:{BACKEND_PORT}/api/web/search"
FETCH_URL = f"http://127.0.0.1:{BACKEND_PORT}/api/web/fetch"

# Primary-provider hint set by agent_manager at spawn time. Lets the
# backend pick the corresponding native search tool (googleSearch for
# Gemini, web_search_preview for OpenAI) — so searches come out of the
# budget the user is already paying for.
PRIMARY_HINT = os.environ.get("OPENSWARM_PRIMARY_API", "") or None

TOOLS = [
    {
        "name": "WebSearch",
        "description": (
            "Search the web using DuckDuckGo and return titles, URLs, and "
            "snippets for the top results. Works on any model primary, "
            "requires no subscription. Use this for up-to-date information "
            "that may not be in the model's training data."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (1-10, default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "WebFetch",
        "description": (
            "Fetch a URL and return its main content as plain text. "
            "For HTML pages, the primary article / main-content region is "
            "extracted (nav, footer, ads stripped). Non-HTML responses "
            "are returned verbatim. Output capped at ~250 KB."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch. Must start with http:// or https://.",
                },
                "prompt": {
                    "type": "string",
                    "description": "Optional context hint describing what to look for.",
                },
            },
            "required": ["url"],
        },
    },
]


def send_response(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _post(url: str, body: dict, timeout: float = 60.0) -> dict:
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace") if e.fp else str(e)
        return {"error": f"HTTP {e.code}: {body_txt[:500]}"}
    except Exception as e:
        return {"error": str(e)}


def handle_tool_call(tool_name: str, arguments: dict) -> dict:
    if tool_name == "WebSearch":
        query = str(arguments.get("query", "")).strip()
        if not query:
            return {"content": [{"type": "text", "text": "Error: query is required"}], "isError": True}
        num = int(arguments.get("num_results", 5))
        num = max(1, min(num, 10))
        body = {"query": query, "num_results": num}
        if PRIMARY_HINT:
            body["primary"] = PRIMARY_HINT
        r = _post(SEARCH_URL, body, timeout=45.0)
        if "error" in r:
            return {"content": [{"type": "text", "text": f"Search failed: {r['error']}"}], "isError": True}
        results = r.get("results", "")
        if not results:
            results = f"No results for: {query}"
        return {"content": [{"type": "text", "text": results}]}

    if tool_name == "WebFetch":
        url = str(arguments.get("url", "")).strip()
        if not url:
            return {"content": [{"type": "text", "text": "Error: url is required"}], "isError": True}
        if not url.startswith(("http://", "https://")):
            return {"content": [{"type": "text", "text": f"Error: url must start with http:// or https:// (got {url!r})"}], "isError": True}
        prompt = arguments.get("prompt") or None
        body = {"url": url}
        if prompt:
            body["prompt"] = str(prompt)
        if PRIMARY_HINT:
            body["primary"] = PRIMARY_HINT
        r = _post(FETCH_URL, body, timeout=45.0)
        if "error" in r:
            return {"content": [{"type": "text", "text": f"Fetch failed: {r['error']}"}], "isError": True}
        content = r.get("content", "")
        if not content:
            content = f"No content returned from {url}"
        return {"content": [{"type": "text", "text": content}]}

    return {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}], "isError": True}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        id_ = msg.get("id")
        params = msg.get("params", {}) or {}

        if method == "initialize":
            send_response(id_, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "openswarm-web",
                    "version": "1.0.0",
                },
            })
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send_response(id_, {"tools": TOOLS})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            result = handle_tool_call(tool_name, arguments)
            send_response(id_, result)
        elif method == "ping":
            send_response(id_, {})
        elif id_ is not None:
            send_response(id_, error={"code": -32601, "message": f"Method not found: {method}"})


if __name__ == "__main__":
    main()
