"""用一段 arXiv API 格式的 Atom 响应，测试真实的 arxiv 库解析 + 我们的时间过滤。"""

from datetime import UTC, datetime

import requests

from robot_times.sources.arxiv_source import fetch_recent


def entry(aid: str, title: str, published: str) -> str:
    return f"""
  <entry>
    <id>http://arxiv.org/abs/{aid}v1</id>
    <updated>{published}</updated>
    <published>{published}</published>
    <title>{title}</title>
    <summary>  An abstract
      spread over lines.  </summary>
    <author><name>Junzhe He</name></author>
    <author><name>Marco Hutter</name></author>
    <arxiv:comment xmlns:arxiv="http://arxiv.org/schemas/atom">under review</arxiv:comment>
    <link href="http://arxiv.org/abs/{aid}v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/{aid}v1" rel="related" type="application/pdf"/>
    <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.RO" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.RO" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
  </entry>"""


MULTILINE_TITLE = "Humanoid Mobility via\n  Attention-based Contacts"

FEED = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title type="html">ArXiv Query</title>
  <id>http://arxiv.org/api/x</id>
  <updated>2026-10-25T00:00:00-04:00</updated>
  <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">3</opensearch:totalResults>
  <opensearch:startIndex xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">0</opensearch:startIndex>
  <opensearch:itemsPerPage xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">100</opensearch:itemsPerPage>
  {entry("2610.31608", MULTILINE_TITLE, "2026-10-24T17:00:00Z")}
  {entry("2610.30000", "Second recent paper", "2026-10-24T09:00:00Z")}
  {entry("2610.10000", "Old paper", "2026-10-10T09:00:00Z")}
</feed>"""


class FakeResponse:
    status_code = 200
    content = FEED.encode()


def test_fetch_recent_parses_feed_and_stops_at_old_papers(monkeypatch):
    urls = []

    def fake_get(self, url, **kw):
        urls.append(url)
        return FakeResponse()

    monkeypatch.setattr(requests.Session, "get", fake_get)
    papers = fetch_recent(
        ["cs.RO", "cs.LG"], days=2, now=datetime(2026, 10, 25, 12, tzinfo=UTC), max_results=50
    )
    assert [p.id for p in papers] == ["2610.31608", "2610.30000"]
    p = papers[0]
    assert p.title == "Humanoid Mobility via Attention-based Contacts"
    assert p.abstract == "An abstract spread over lines."
    assert p.authors == ["Junzhe He", "Marco Hutter"]
    assert p.categories[:2] == ["cs.RO", "cs.LG"]
    assert p.url == "https://arxiv.org/abs/2610.31608"
    assert "export.arxiv.org" in urls[0]
    assert "submittedDate" in urls[0]


RSS = """<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
  <channel><title>cs.RO updates on arXiv.org</title>
    <item>
      <title>RAIWAY: A Large-Wheeled Bipedal Robot</title>
      <link>https://arxiv.org/abs/2610.33172</link>
      <description>arXiv:2610.33172v1 Announce Type: new
Abstract: We present RAIWAY, an in-house wheeled
 bipedal robot.</description>
      <guid isPermaLink="false">oai:arXiv.org:2610.33172v1</guid>
      <category>cs.RO</category>
      <pubDate>Tue, 27 Oct 2026 00:00:00 -0400</pubDate>
      <arxiv:announce_type>new</arxiv:announce_type>
      <dc:creator>Kyungmin Nam, Jemin Hwangbo</dc:creator>
    </item>
    <item>
      <title>An old paper, new version</title>
      <link>https://arxiv.org/abs/2601.00001</link>
      <description>arXiv:2601.00001v3 Announce Type: replace
Abstract: Old.</description>
      <arxiv:announce_type>replace</arxiv:announce_type>
    </item>
  </channel>
</rss>"""


class Throttled(Exception):
    status = 429


def test_throttled_api_retries_then_falls_back_to_rss():
    import httpx

    calls, waits, urls = [], [], []

    def api(*a, **k):
        calls.append(1)
        raise Throttled("Page request resulted in HTTP 429")

    def rss(req):
        urls.append(str(req.url))
        return httpx.Response(200, content=RSS.encode())

    papers = fetch_recent(
        ["cs.RO", "cs.LG"], sleep=waits.append, api=api,
        http=httpx.Client(transport=httpx.MockTransport(rss)),
    )  # fmt: skip
    assert len(calls) == 3 and waits == [30, 90]
    assert urls == ["https://rss.arxiv.org/rss/cs.RO+cs.LG"]
    assert [p.id for p in papers] == ["2610.33172"]  # 旧论文的新版本不算
    p = papers[0]
    assert p.abstract == "We present RAIWAY, an in-house wheeled bipedal robot."
    assert p.authors == ["Kyungmin Nam", "Jemin Hwangbo"] and p.categories == ["cs.RO"]
    assert (
        p.pdf_url == "https://arxiv.org/pdf/2610.33172" and p.published.isoformat() == "2026-10-27"
    )


def test_other_errors_are_not_retried():
    import pytest

    def api(*a, **k):
        raise ValueError("bad query")

    with pytest.raises(ValueError):
        fetch_recent(["cs.RO"], sleep=lambda s: None, api=api)


def test_rss_fallback_respects_fetch_limit():
    import httpx

    def api(*a, **k):
        raise Throttled("Page request resulted in HTTP 429")

    feed = RSS.replace(
        "</channel>",
        """<item><title>Another new paper</title><link>https://arxiv.org/abs/2610.33001</link>
        <arxiv:announce_type>new</arxiv:announce_type></item></channel>""",
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=feed))
    with httpx.Client(transport=transport) as http:
        papers = fetch_recent(["cs.RO"], max_results=1, sleep=lambda s: None, api=api, http=http)

    assert [p.id for p in papers] == ["2610.33172"]


def test_unexpected_empty_page_retries_then_falls_back_to_rss():
    import arxiv
    import httpx

    calls, waits = [], []

    def api(*a, **k):
        calls.append(1)
        raise arxiv.UnexpectedEmptyPageError("https://export.arxiv.org/api/query", 3, None)

    transport = httpx.MockTransport(lambda r: httpx.Response(200, text=RSS))
    with httpx.Client(transport=transport) as http:
        papers = fetch_recent(["cs.RO"], sleep=waits.append, api=api, http=http)

    assert len(calls) == 3 and waits == [30, 90]
    assert [p.id for p in papers] == ["2610.33172"]
