import json

import httpx
import pytest

from ming.tools.web import WebFetchTool, WebSearchTool


@pytest.mark.asyncio
async def test_web_search_returns_structured_results_from_duckduckgo_lite():
    html = """
    <html><body>
      <a rel="nofollow" href="https://example.com/a">Agent Architecture</a>
      <td class="result-snippet">Planner, executor, memory.</td>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert "duckduckgo.com" in str(request.url)
        return httpx.Response(200, text=html)

    tool = WebSearchTool(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await tool.execute(query="agent architecture", max_results=3)
    payload = json.loads(result.output)

    assert not result.is_error
    assert payload["query"] == "agent architecture"
    assert payload["results"][0]["title"] == "Agent Architecture"
    assert payload["results"][0]["url"] == "https://example.com/a"
    assert payload["results"][0]["snippet"] == "Planner, executor, memory."


@pytest.mark.asyncio
async def test_web_fetch_extracts_title_and_readable_text():
    html = """
    <html>
      <head><title>Agent Tools</title><script>noise()</script></head>
      <body>
        <nav>menu</nav>
        <article><h1>Plan Execute</h1><p>Use observe and replan.</p></article>
      </body>
    </html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    tool = WebFetchTool(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await tool.execute(url="https://example.com/post", max_chars=1000)
    payload = json.loads(result.output)

    assert not result.is_error
    assert payload["title"] == "Agent Tools"
    assert "Plan Execute" in payload["text"]
    assert "Use observe and replan." in payload["text"]
    assert "noise()" not in payload["text"]
