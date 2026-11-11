import httpx
import pytest

from app.ingestion.http import PoliteClient, RobotsDisallowed, parse_retry_after

UA = "MLE-Sprint/0.1 (test)"


def make_client(handler, max_retries=2):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    c = PoliteClient(UA, max_retries=max_retries, min_interval=0, transport=httpx.MockTransport(handler), sleep=fake_sleep)
    return c, sleeps


async def test_robots_disallow_blocks_and_allow_passes():
    def handler(req: httpx.Request):
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, text="ok")

    c, _ = make_client(handler)
    assert (await c.get("https://a.test/public")).text == "ok"
    with pytest.raises(RobotsDisallowed):
        await c.get("https://a.test/private/x")
    # API-style calls can opt out explicitly
    assert (await c.get("https://a.test/private/x", respect_robots=False)).status_code == 200


async def test_robots_404_allows_and_5xx_disallows():
    c, _ = make_client(lambda r: httpx.Response(404 if r.url.host == "a.test" else 503))
    assert await c.allowed("https://a.test/x")
    assert not await c.allowed("https://b.test/x")


async def test_user_agent_sent():
    seen = []

    def handler(req):
        seen.append(req.headers["user-agent"])
        return httpx.Response(404 if req.url.path == "/robots.txt" else 200)

    c, _ = make_client(handler)
    await c.get("https://a.test/")
    assert set(seen) == {UA}


async def test_retries_503_then_succeeds_honouring_retry_after():
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "7"}) if calls["n"] == 1 else httpx.Response(200)

    c, sleeps = make_client(handler)
    assert (await c.get("https://a.test/")).status_code == 200
    assert calls["n"] == 2 and 7.0 in sleeps


async def test_gives_up_after_max_retries_and_returns_last_response():
    c, sleeps = make_client(lambda r: httpx.Response(404 if r.url.path == "/robots.txt" else 503), max_retries=2)
    assert (await c.get("https://a.test/")).status_code == 503
    assert len(sleeps) == 2


def test_parse_retry_after():
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None


async def test_slow_hosts_get_their_own_spacing():
    from app.ingestion.http import PoliteClient
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    c = PoliteClient("UA/1", min_interval=1.0, host_intervals={"medium.com": 6.0}, sleep=fake_sleep)
    await c._throttle("medium.com")
    await c._throttle("medium.com")
    await c._throttle("example.com")
    await c._throttle("example.com")
    await c.aclose()
    assert len(slept) == 2 and 5.5 < slept[0] <= 6.0 and 0.5 < slept[1] <= 1.0


def test_slow_hosts_setting_parses():
    from app.config import Settings
    assert Settings(slow_hosts="medium.com=6, substack.com = 2").host_intervals == {"medium.com": 6.0, "substack.com": 2.0}
