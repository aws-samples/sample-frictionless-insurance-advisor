"""Sources for the Sources footer under Text Assistant answers.

The footer lists what an answer actually rests on, as links the advisor can
open. Two kinds:

* an Awesome reference document, opened in Data Explorer
* a web page returned by the web-search tool, opened in a new browser tab

Nothing here trusts the model to produce a link. Candidates come from the
tool results of the current turn (the document headers a tool returned, the
results a web search returned), and a candidate only becomes a footer entry
when the answer text names it: the document's path, or the page's URL. So the
model cannot add a document that was not retrieved or a URL that was not
returned, and a retrieved document the answer did not use stays off.

The footer travels as one typed `app_event` of type "sources" after the text.
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

MAX_SOURCES = 8

# Gateway tool -> Data-tab folder holding the documents it returns. The
# documents are named by "=== <key> ===" headers inside the tool result.
_DOC_FOLDER_BY_TOOL = {
    "get_portfolio": "portfolio",
    "get_company_info": "company",
    "get_competitive_info": "competitive",
    "get_competitor_products": "competitors",
    "get_promotions": "promotion",
}
# Tools that return one file named by their product_type argument.
_PRODUCT_FILE_BY_TOOL = {
    "get_form_schema": "forms",
    "get_pricing_table": "pricing",
    "estimate_premium": "pricing",
}
# The file for each product type is named after the product's portfolio file.
_PRODUCT_SLUGS = {
    "term_life": "awesome-term-life",
    "whole_life": "awesome-whole-life",
    "universal_life": "awesome-universal-life",
    "variable_life": "awesome-variable-life",
    "critical_illness": "awesome-critical-illness",
    "health": "awesome-health",
    "home": "awesome-home",
    "auto": "awesome-motor",
    "travel": "awesome-travel",
    "disability": "awesome-disability",
}
_PRODUCT_TYPES = set(_PRODUCT_SLUGS)
_FILE_SUFFIX_BY_FOLDER = {"forms": "-form.json", "pricing": "-pricing.json"}
_WEB_TOOL = "WebSearch"

_HEADER_RE = re.compile(r"=== ([A-Za-z0-9][A-Za-z0-9._/\-]{0,200}) ===")
# The Data Explorer shows only these.
_BROWSABLE = (".md", ".json")


def tool_basename(name: str) -> str:
    """`PortfolioService___get_portfolio` -> `get_portfolio`."""
    return name.rsplit("___", 1)[-1] if name else ""


def _result_text(result: Any) -> str:
    """All the text in a Strands ToolResult, as one string."""
    if not isinstance(result, dict):
        return ""
    parts: list[str] = []
    for block in result.get("content") or []:
        if isinstance(block, dict):
            if isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif "json" in block:
                parts.append(json.dumps(block["json"], default=str))
    return "\n".join(parts)


def _safe_http_url(url: Any) -> str | None:
    """The URL if it is a plain http(s) link, else None."""
    if not isinstance(url, str) or len(url) > 2000:
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return url.strip()


def parse_web_results(text: str) -> list[dict]:
    """Title and URL of each result in a web-search tool result."""
    found: list[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            url = _safe_http_url(node.get("url"))
            if url:
                title = node.get("title")
                found.append({
                    "url": url,
                    "title": title.strip()[:200] if isinstance(title, str) and title.strip() else urlparse(url).netloc,
                })
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
                elif isinstance(v, str) and v[:1] in "{[":
                    # The tool wraps its JSON document in a text field.
                    try:
                        walk(json.loads(v))
                    except ValueError:
                        pass
        elif isinstance(node, list):
            for item in node:
                walk(item)

    try:
        walk(json.loads(text))
    except ValueError:
        pass
    return found


def _norm_url(url: str) -> str:
    """Compare URLs without scheme, www, trailing slash or fragment."""
    u = url.strip().split("#", 1)[0].rstrip("/").lower()
    u = re.sub(r"^https?://", "", u)
    return u[4:] if u.startswith("www.") else u


def _title_before(text: str, needle: str) -> str | None:
    """Readable title the answer gave for a source, e.g. the words before
    "(portfolio/products/awesome-term-life.md)"."""
    m = re.search(r"([^;()\n*_|\[\]]{3,90}?)\s*\(\s*" + re.escape(needle) + r"\s*\)", text, re.IGNORECASE)
    if not m:
        return None
    title = re.sub(r"^\s*(sources?|source|see)\s*:\s*", "", m.group(1), flags=re.IGNORECASE).strip(" ,:-")
    return title or None


def _web_title_in_answer(text: str, url: str) -> str | None:
    """The words the answer put before a URL, e.g. "Page title, https://...".
    Looks for the URL as written, with or without scheme and "www."."""
    bare = re.sub(r"^https?://(www\.)?", "", url).rstrip("/")
    m = re.search(r"([^;\n\[\]()*_|]{3,120}?)[\s,:(]*\(?\s*(?:https?://)?(?:www\.)?" + re.escape(bare), text, re.IGNORECASE)
    if not m:
        return None
    title = re.sub(r"^\s*(sources?|source)\s*:\s*", "", m.group(1), flags=re.IGNORECASE).strip(" ,:-")
    return title if 3 <= len(title) <= 120 else None


def _humanise(key: str) -> str:
    base = key.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return base.replace("_", " ").replace("-", " ").strip().capitalize()


class SourceCollector:
    """Gathers candidate sources from tool results and builds the footer."""

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}   # "folder/key" (lower) -> {folder, key}
        self._web: dict[str, dict] = {}    # normalised url -> {url, title}
        self._text: list[str] = []

    # --- feeding ---------------------------------------------------------
    def add_tool_result(self, tool_name: str, tool_input: Any, result: Any) -> None:
        """Record what one finished tool call returned. Never raises."""
        try:
            name = tool_basename(tool_name)
            if isinstance(result, dict) and result.get("status") == "error":
                return
            args = tool_input if isinstance(tool_input, dict) else {}
            if name in _DOC_FOLDER_BY_TOOL:
                folder = _DOC_FOLDER_BY_TOOL[name]
                for key in _HEADER_RE.findall(_result_text(result)):
                    self._add_doc(folder, key)
            elif name in _PRODUCT_FILE_BY_TOOL:
                product = args.get("product_type")
                if isinstance(product, str) and product in _PRODUCT_TYPES:
                    folder = _PRODUCT_FILE_BY_TOOL[name]
                    self._add_doc(folder, f"{_PRODUCT_SLUGS[product]}{_FILE_SUFFIX_BY_FOLDER[folder]}")
            elif name == _WEB_TOOL:
                for item in parse_web_results(_result_text(result)):
                    self._web.setdefault(_norm_url(item["url"]), item)
        except Exception:  # noqa: BLE001 - sources are a nicety, never break the answer
            return

    def add_history(self, messages: Any) -> None:
        """Record the tool results of earlier turns of this conversation.

        The agent is rebuilt for every request and its history is restored from
        memory, so on a later turn the model can answer from documents it
        retrieved in an earlier turn without calling the tool again. Those
        documents are still ones a tool returned in this conversation, so they
        stay valid candidates; they only reach the footer if the answer names
        them. Never raises.
        """
        try:
            calls: dict[str, tuple[str, Any]] = {}
            for message in messages or []:
                content = message.get("content") if isinstance(message, dict) else None
                for block in content or []:
                    if not isinstance(block, dict):
                        continue
                    tool_use = block.get("toolUse")
                    if isinstance(tool_use, dict) and tool_use.get("toolUseId"):
                        calls[tool_use["toolUseId"]] = (str(tool_use.get("name", "")), tool_use.get("input"))
                    tool_result = block.get("toolResult")
                    if isinstance(tool_result, dict):
                        name, args = calls.get(tool_result.get("toolUseId"), ("", None))
                        if name:
                            self.add_tool_result(name, args, tool_result)
        except Exception:  # noqa: BLE001 - sources are a nicety, never break the answer
            return

    def _add_doc(self, folder: str, key: str) -> None:
        if not key.lower().endswith(_BROWSABLE) or ".." in key:
            return
        self._docs.setdefault(f"{folder}/{key}".lower(), {"folder": folder, "key": key})

    def add_text(self, chunk: str) -> None:
        if isinstance(chunk, str):
            self._text.append(chunk)

    # --- building --------------------------------------------------------
    @property
    def used_web_search(self) -> bool:
        return bool(self._web)

    def build(self) -> list[dict]:
        """Sources the answer names, files first, in the order retrieved."""
        text = "".join(self._text)
        lowered = text.lower()
        out: list[dict] = []
        for path_l, doc in self._docs.items():
            if path_l in lowered:
                path = f"{doc['folder']}/{doc['key']}"
                out.append({
                    "kind": "file",
                    "folder": doc["folder"],
                    "key": doc["key"],
                    "title": _title_before(text, path) or _humanise(doc["key"]),
                })
        # Match web pages on the URL as the answer wrote it.
        cited_urls = {_norm_url(u) for u in re.findall(r"https?://[^\s)\]>\"']+", text)}
        cited_urls = {u.rstrip(".,;:") for u in cited_urls}
        for norm, item in self._web.items():
            if norm in cited_urls or any(norm.startswith(c) or c.startswith(norm) for c in cited_urls if len(c) > 12):
                title = item["title"]
                # A result with no real title comes back as its hostname; the
                # answer usually names the page properly.
                if " " not in title:
                    title = _web_title_in_answer(text, item["url"]) or title
                out.append({"kind": "web", "title": title, "url": item["url"]})
        return out[:MAX_SOURCES]


def attach_to_agent(agent: Any, collector: SourceCollector) -> None:
    """Register the collector on a Strands agent's tool-call hook."""
    from strands.hooks import AfterToolCallEvent

    def on_tool(event: Any) -> None:
        tool_use = getattr(event, "tool_use", None) or {}
        collector.add_tool_result(
            str(tool_use.get("name", "")), tool_use.get("input"), getattr(event, "result", None)
        )

    agent.hooks.add_callback(AfterToolCallEvent, on_tool)
