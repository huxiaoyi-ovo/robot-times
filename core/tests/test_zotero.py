from datetime import date

import httpx
import pytest
from conftest import FakeZotero

from robot_times.models import Paper
from robot_times.zotero.local_api import ZoteroError, ZoteroLocalClient


def client(fake: FakeZotero, **kw) -> ZoteroLocalClient:
    return ZoteroLocalClient(transport=httpx.MockTransport(fake), **kw)


def a_paper(pid="2610.31608") -> Paper:
    return Paper(
        id=pid,
        title="Humanoid Mobility",
        abstract="abs",
        authors=["Junzhe He", "Marco Hutter"],
        published=date(2026, 10, 22),
        url=f"https://arxiv.org/abs/{pid}",
    )


def test_reads_library_and_caches_server_id():
    fake = FakeZotero()
    z = client(fake)
    assert z.ping()
    assert z.server_id == "srv-1"
    assert len(z.top_items()) == 1


def test_add_papers_creates_items_then_notes():
    fake = FakeZotero()
    z = client(fake)
    keys = z.add_papers(
        [a_paper(), a_paper("2610.19825")], ["<p>摘要</p>", None], tags=["Robot Times"]
    )
    assert keys == ["K0", "K1"]
    parents, notes = fake.created[:2], fake.created[2:]
    assert parents[0]["itemType"] == "preprint"
    assert parents[0]["archiveID"] == "arXiv:2610.31608"
    assert parents[0]["creators"][1] == {
        "creatorType": "author",
        "firstName": "Marco",
        "lastName": "Hutter",
    }
    assert notes == [
        {
            "itemType": "note",
            "parentItem": "K0",
            "note": "<p>摘要</p>",
            "tags": [{"tag": "Robot Times"}],
        }
    ]
    assert fake.issued_keys == 1  # Always Allow 的 key 可以复用


def test_single_use_key_is_requested_again():
    fake = FakeZotero(remember=False)
    z = client(fake)
    z.add_papers([a_paper()], ["<p>x</p>"])
    assert fake.issued_keys == 2  # 条目一次、笔记一次


def test_401_triggers_reauthorization():
    fake = FakeZotero(reject_first_write=True)
    z = client(fake, api_key="stale")
    assert z.add_papers([a_paper()]) == ["K0"]
    assert fake.issued_keys == 1


def test_403_explains_how_to_enable_api():
    z = client(FakeZotero(enabled=False))
    assert not z.ping()
    with pytest.raises(ZoteroError, match="允许此计算机"):
        z.top_items()


def test_other_403_shows_zotero_message():
    def read_only(request):
        h = {"Zotero-Server-ID": "srv-1"}
        if request.method == "GET":
            return httpx.Response(200, headers=h)
        return httpx.Response(403, headers=h, text="Write access denied")

    z = ZoteroLocalClient(transport=httpx.MockTransport(read_only), api_key="k")
    with pytest.raises(ZoteroError, match="Write access denied"):
        z.add_papers([a_paper()])


def test_written_papers_show_up_in_library():
    fake = FakeZotero()
    z = client(fake)
    z.add_papers([a_paper()])
    titles = [it["data"]["title"] for it in z.top_items()]
    assert "Humanoid Mobility" in titles
