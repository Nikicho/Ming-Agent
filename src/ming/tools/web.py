"""Web search and fetch tools."""

import html
import json
import os
import re
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from ming.tools.base import Tool, ToolResult


class WebSearchTool(Tool):
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web and return structured title/url/snippet results. "
            "Use this instead of bash/curl for web research."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results. Default 5.",
                },
                "provider": {
                    "type": "string",
                    "description": "Search provider: auto, tavily, exa, duckduckgo.",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        max_results: int = 5,
        provider: str = "auto",
        **_: Any,
    ) -> ToolResult:
        max_results = max(1, min(max_results, 10))
        try:
            if provider in {"auto", "tavily"} and os.environ.get("TAVILY_API_KEY"):
                payload = await self._search_tavily(query, max_results)
            elif provider in {"auto", "exa"} and os.environ.get("EXA_API_KEY"):
                payload = await self._search_exa(query, max_results)
            else:
                payload = await self._search_duckduckgo(query, max_results)
        except Exception as exc:
            return ToolResult(
                output=f"web_search failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )

        return ToolResult(output=json.dumps(payload, ensure_ascii=False, indent=2))

    async def _search_tavily(self, query: str, max_results: int) -> dict[str, Any]:
        client = self.client or httpx.AsyncClient(timeout=20)
        response = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": os.environ["TAVILY_API_KEY"],
                "query": query,
                "max_results": max_results,
                "include_answer": False,
            },
        )
        response.raise_for_status()
        data = response.json()
        return {
            "query": query,
            "provider": "tavily",
            "results": [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("content", ""),
                    "score": item.get("score"),
                }
                for item in data.get("results", [])[:max_results]
            ],
        }

    async def _search_exa(self, query: str, max_results: int) -> dict[str, Any]:
        client = self.client or httpx.AsyncClient(timeout=20)
        response = await client.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": os.environ["EXA_API_KEY"]},
            json={"query": query, "numResults": max_results},
        )
        response.raise_for_status()
        data = response.json()
        return {
            "query": query,
            "provider": "exa",
            "results": [
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("text", "")[:500],
                    "score": item.get("score"),
                }
                for item in data.get("results", [])[:max_results]
            ],
        }

    async def _search_duckduckgo(self, query: str, max_results: int) -> dict[str, Any]:
        client = self.client or httpx.AsyncClient(timeout=20, follow_redirects=True)
        url = "https://lite.duckduckgo.com/lite/?" + urlencode({"q": query})
        response = await client.get(url, headers={"User-Agent": "MingBot/0.1"})
        response.raise_for_status()
        results = _parse_duckduckgo_lite(response.text, max_results)
        return {"query": query, "provider": "duckduckgo", "results": results}


class WebFetchTool(Tool):
    def __init__(self, client: httpx.AsyncClient | None = None):
        self.client = client

    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch a URL and extract readable text. Use after web_search to inspect "
            "specific sources."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch."},
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum extracted text characters. Default 20000.",
                },
            },
            "required": ["url"],
        }

    async def execute(self, url: str, max_chars: int = 20000, **_: Any) -> ToolResult:
        client = self.client or httpx.AsyncClient(timeout=30, follow_redirects=True)
        try:
            response = await client.get(url, headers={"User-Agent": "MingBot/0.1"})
            response.raise_for_status()
        except Exception as exc:
            return ToolResult(
                output=f"web_fetch failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )

        content_type = response.headers.get("content-type", "")
        if "html" in content_type or "<html" in response.text[:500].lower():
            title, text = _extract_html_text(response.text)
        else:
            title, text = "", response.text

        text = text[: max(1000, min(max_chars, 100000))]
        payload = {
            "url": str(response.url),
            "title": title,
            "content_type": content_type,
            "text": text,
            "text_chars": len(text),
        }
        return ToolResult(output=json.dumps(payload, ensure_ascii=False, indent=2))


class WebResearchTool(Tool):
    def __init__(
        self,
        search_tool: WebSearchTool | None = None,
        fetch_tool: WebFetchTool | None = None,
        cache_root: str | Path | None = None,
    ):
        self.search_tool = search_tool or WebSearchTool()
        self.fetch_tool = fetch_tool or WebFetchTool()
        self.cache_root = Path(cache_root) if cache_root else Path.cwd() / ".ming" / "scratch"
        self.cache_root.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "web_research"

    @property
    def description(self) -> str:
        return (
            "Search, select sources, fetch pages, and return an evidence pack with citations. "
            "Use for web research that needs sources."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "description": "Search result limit."},
                "max_sources": {"type": "integer", "description": "Fetch source limit."},
                "allow_domains": {"type": "array", "items": {"type": "string"}},
                "deny_domains": {"type": "array", "items": {"type": "string"}},
                "freshness_days": {"type": "integer"},
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        max_results: int = 5,
        max_sources: int = 3,
        allow_domains: list[str] | None = None,
        deny_domains: list[str] | None = None,
        freshness_days: int | None = None,
        today: str | None = None,
        **_: Any,
    ) -> ToolResult:
        search_payload = await self._search(query, max_results=max_results)
        results = self._filter_results(
            search_payload.get("results", []),
            allow_domains=allow_domains or [],
            deny_domains=deny_domains or [],
            freshness_days=freshness_days,
            today=today,
        )[: max(1, min(max_sources, 5))]

        citations = []
        evidence = []
        for index, result in enumerate(results, start=1):
            fetched = await self._fetch(result["url"], max_chars=12000)
            text = str(fetched.get("text", "")).strip()
            citations.append(
                {
                    "id": f"S{index}",
                    "title": fetched.get("title") or result.get("title", ""),
                    "url": fetched.get("url") or result.get("url", ""),
                    "snippet": result.get("snippet", ""),
                }
            )
            evidence.append(
                {
                    "citation_id": f"S{index}",
                    "quote": text[:500],
                    "source_url": fetched.get("url") or result.get("url", ""),
                }
            )

        payload = {
            "query": query,
            "citations": citations,
            "evidence": evidence,
            "source_count": len(citations),
        }
        cache_path = self.cache_root / f"web_research_{datetime.now():%Y%m%d_%H%M%S_%f}.json"
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["cache_path"] = str(cache_path)
        return ToolResult(output=json.dumps(payload, ensure_ascii=False, indent=2))

    async def _search(self, query: str, max_results: int = 5, provider: str = "auto", **kwargs):
        result = await self.search_tool.execute(
            query=query,
            max_results=max_results,
            provider=provider,
        )
        return json.loads(result.output)

    async def _fetch(self, url: str, max_chars: int = 12000, **kwargs):
        result = await self.fetch_tool.execute(url=url, max_chars=max_chars)
        return json.loads(result.output)

    def _filter_results(
        self,
        results: list[dict[str, Any]],
        allow_domains: list[str],
        deny_domains: list[str],
        freshness_days: int | None,
        today: str | None,
    ) -> list[dict[str, Any]]:
        allowed = [domain.lower() for domain in allow_domains]
        denied = [domain.lower() for domain in deny_domains]
        filtered = []
        for result in results:
            host = urlparse(result.get("url", "")).netloc.lower()
            if allowed and not any(host.endswith(domain) for domain in allowed):
                continue
            if denied and any(host.endswith(domain) for domain in denied):
                continue
            if freshness_days is not None and not _is_fresh(
                result.get("published"), freshness_days, today
            ):
                continue
            filtered.append(result)
        return filtered


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style", "svg", "noscript"}:
            self._skip_depth += 1
        if tag in {"p", "br", "h1", "h2", "h3", "li", "article", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "svg", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        cleaned = html.unescape(data).strip()
        if not cleaned:
            return
        if self._in_title:
            self.title += cleaned
            return
        if self._skip_depth:
            return
        self.parts.append(cleaned)


def _extract_html_text(source: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(source)
    text = "\n".join(_normalize_lines(" ".join(parser.parts)).splitlines())
    return parser.title.strip(), text


def _normalize_lines(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _parse_duckduckgo_lite(source: str, max_results: int) -> list[dict[str, Any]]:
    links = re.findall(
        r'<a[^>]+href="(?P<url>https?://[^"]+)"[^>]*>(?P<title>.*?)</a>',
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippets = re.findall(
        r'<td[^>]+class="result-snippet"[^>]*>(?P<snippet>.*?)</td>',
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )

    results: list[dict[str, Any]] = []
    for idx, (url, title) in enumerate(links[:max_results]):
        results.append(
            {
                "title": _clean_html(title),
                "url": html.unescape(url),
                "snippet": _clean_html(snippets[idx]) if idx < len(snippets) else "",
                "score": None,
            }
        )
    return results


def _clean_html(value: str) -> str:
    return html.unescape(re.sub(r"<.*?>", "", value, flags=re.DOTALL)).strip()


def _is_fresh(published: str | None, freshness_days: int, today: str | None) -> bool:
    if not published:
        return True
    try:
        published_date = date.fromisoformat(str(published)[:10])
        today_date = date.fromisoformat(today[:10]) if today else date.today()
    except ValueError:
        return True
    return (today_date - published_date).days <= freshness_days
