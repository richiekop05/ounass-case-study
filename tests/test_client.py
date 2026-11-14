"""Client behaviour with a fake transport and a fake clock: no network access."""
import httpx
import pytest

from scraper.client import Client, DisallowedByRobots, FetchError, RobotsRules, StopScrape

UA = "OunassCaseStudyScraper/0.1 (test; contact: test@test.invalid)"
PAGE = b"<html><script>window.initialState = {\"plp\": {}};</script></html>"


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def make_client(tmp_path, handler, robots="User-agent: *\nDisallow: /*?*\n", **kw):
    calls = []

    def route(request: httpx.Request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return handler(request)

    t = FakeTime()
    c = Client(UA, cache_dir=tmp_path / "cache", transport=httpx.MockTransport(route),
               clock=t.clock, sleep=t.sleep, **kw)
    return c, calls, t


def ok_page(request):
    return httpx.Response(200, content=PAGE, headers={"content-type": "text/html"})


def test_no_request_faster_than_one_second(tmp_path):
    c, calls, t = make_client(tmp_path, ok_page)
    for i in range(10):
        c.get(f"https://www.ounass.ae/women/p{i}")
    times = c.stats.request_times
    assert len(times) == 11  # robots + 10 pages
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= 1.0
    assert min(gaps) >= 1.5 - 1e-9  # 1 s floor + at least 0.5 s jitter


def test_crawl_delay_is_honoured_when_longer(tmp_path):
    c, _, _ = make_client(tmp_path, ok_page, robots="User-agent: *\nCrawl-delay: 7\n")
    c.get("https://www.ounass.ae/a")
    c.get("https://www.ounass.ae/b")
    times = c.stats.request_times
    assert times[-1] - times[-2] >= 7


def test_cache_means_second_get_makes_no_request(tmp_path):
    c, calls, _ = make_client(tmp_path, ok_page)
    c.get("https://www.ounass.ae/women/dresses")
    n = len(calls)
    c2, calls2, _ = make_client(tmp_path, ok_page)  # fresh client, same cache dir: a resumed run
    c2.get("https://www.ounass.ae/women/dresses")
    assert len(calls) == n and calls2 == []
    assert c2.stats.requests == 0 and c2.stats.cache_hits == 2  # robots + page


def test_refresh_bypasses_cache(tmp_path):
    c, _, _ = make_client(tmp_path, ok_page)
    c.get("https://www.ounass.ae/x")
    c2, calls2, _ = make_client(tmp_path, ok_page, refresh=True)
    c2.get("https://www.ounass.ae/x")
    assert "https://www.ounass.ae/x" in calls2


def test_robots_disallowed_url_is_refused(tmp_path):
    c, calls, _ = make_client(tmp_path, ok_page)
    with pytest.raises(DisallowedByRobots):
        c.get("https://www.ounass.ae/women/clothing/dresses?fh_start_index=24")
    assert not any("fh_start_index" in u for u in calls)


def test_403_stops_the_run(tmp_path):
    c, _, _ = make_client(tmp_path, lambda r: httpx.Response(403))
    with pytest.raises(StopScrape):
        c.get("https://www.ounass.ae/x")


def test_captcha_page_stops_the_run(tmp_path):
    c, _, _ = make_client(tmp_path, lambda r: httpx.Response(
        200, text="<html>Please solve this CAPTCHA</html>", headers={"content-type": "text/html"}))
    with pytest.raises(StopScrape):
        c.get("https://www.ounass.ae/x")


def test_page_without_app_state_stops_the_run(tmp_path):
    c, _, _ = make_client(tmp_path, lambda r: httpx.Response(
        200, text="<html>checking your browser</html>", headers={"content-type": "text/html"}))
    with pytest.raises(StopScrape):
        c.get("https://www.ounass.ae/x")


def test_429_retries_with_retry_after_then_succeeds(tmp_path):
    state = {"n": 0}

    def handler(r):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "12"})
        return ok_page(r)

    c, _, t = make_client(tmp_path, handler)
    c.get("https://www.ounass.ae/x")
    assert 12 in t.sleeps


def test_5xx_backoff_is_exponential_from_5s(tmp_path):
    state = {"n": 0}

    def handler(r):
        state["n"] += 1
        return httpx.Response(503) if state["n"] <= 2 else ok_page(r)

    c, _, t = make_client(tmp_path, handler)
    c.get("https://www.ounass.ae/x")
    assert 5.0 in t.sleeps and 10.0 in t.sleeps


def test_three_consecutive_failures_stop(tmp_path):
    c, _, _ = make_client(tmp_path, lambda r: httpx.Response(500))
    with pytest.raises(StopScrape):
        c.get("https://www.ounass.ae/x")


def test_404_is_a_per_url_error_and_is_cached(tmp_path):
    c, calls, _ = make_client(tmp_path, lambda r: httpx.Response(404, text="nope"))
    with pytest.raises(FetchError):
        c.get("https://www.ounass.ae/gone.html")
    n = len(calls)
    with pytest.raises(FetchError):
        c.get("https://www.ounass.ae/gone.html")
    assert len(calls) == n


def test_placeholder_user_agent_is_refused(tmp_path):
    with pytest.raises(ValueError):
        Client("Bot (contact: you@example.com)", cache_dir=tmp_path)


def test_robots_rules_match_ounass_file(fixture_text):
    rules = RobotsRules.parse(fixture_text("robots.txt"), UA)
    assert rules.crawl_delay is None
    assert rules.allowed("https://www.ounass.ae/women/clothing/dresses")
    assert rules.allowed("https://www.ounass.ae/shop-zimmermann-dress-for-women-220163574_195.html")
    assert not rules.allowed("https://www.ounass.ae/women/clothing/dresses?fh_start_index=24")
    assert not rules.allowed("https://www.ounass.ae/women/clothing/dresses?ajax=true")
    assert not rules.allowed("https://www.ounass.ae/cart")
    assert not rules.allowed("https://www.ounass.ae/women/staff-sale-now")
    assert not rules.allowed("https://www.ounass.ae/get-mega-nav")


def test_robots_longest_match_and_allow():
    rules = RobotsRules.parse("User-agent: *\nDisallow: /a\nAllow: /a/b\nDisallow: /*.pdf$\n", UA)
    assert not rules.allowed("https://x/a/c")
    assert rules.allowed("https://x/a/b/c")
    assert not rules.allowed("https://x/file.pdf")
    assert rules.allowed("https://x/file.pdf.html")


def test_robots_named_bot_group_does_not_apply_to_us(fixture_text):
    rules = RobotsRules.parse(fixture_text("robots.txt"), "AhrefsBot/7.0")
    assert rules.crawl_delay == 10
