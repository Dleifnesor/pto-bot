import datetime as dt

import numpy as np
import pytest
from PIL import Image, ImageDraw

from pto import config
from pto.db import DB, now_iso
from pto.imaging import checks, cutout, poster, text
from pto.llm import concepts
from pto.llm.ollama import extract_json
from pto.trends import google, normalize, seasonal, sensitive_hit
from pto.uploaders.printify import fit_scale, retail_price

CFG = config.load()
FONTS = sorted(CFG.paths.fonts_dir.glob("*.ttf"))


def test_time_window_wraps_midnight():
    w = config.TimeWindow(start="23:00", end="06:00")
    assert w.contains(dt.time(23, 30)) and w.contains(dt.time(2, 0))
    assert not w.contains(dt.time(12, 0))
    night = config.TimeWindow(start="01:00", end="08:00")
    assert night.end_after(dt.datetime(2026, 10, 9, 2, 0)) == dt.datetime(2026, 10, 9, 8, 0)


def test_seasonal_dates():
    assert seasonal.easter(2027) == dt.date(2027, 3, 28)
    assert seasonal.nth_weekday(2026, 11, 3, 4) == dt.date(2026, 11, 26)  # US Thanksgiving
    names = [i.title for i in seasonal.upcoming(dt.date(2026, 10, 9), 60)]
    assert "Halloween 2026" in names and "Thanksgiving 2026" in names
    assert not any(n.startswith("Christmas") for n in names)  # 77 days out


def test_google_rss_parse_and_traffic():
    xml = """<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
      <item><title>capybara spa</title><ht:approx_traffic>20K+</ht:approx_traffic>
        <ht:news_item><ht:news_item_title>Capybaras relax in hot spring</ht:news_item_title>
        <ht:news_item_url>https://example.com/a</ht:news_item_url></ht:news_item></item></channel></rss>"""
    items = google.parse(xml, "US")
    assert items[0].title == "capybara spa" and items[0].traffic == 20000
    assert items[0].context == ["Capybaras relax in hot spring"]
    assert google.parse_traffic("200+") == 200 and google.parse_traffic("1.5M+") == 1_500_000


def test_skip_patterns_drop_fixtures_and_tv():
    from pto.trends import skip_hit

    pats = CFG.trends.skip_patterns
    for t in ("india vs west indies", "greece v england", "bbc iplayer live", "itv news", "powerball numbers"):
        assert skip_hit(t, pats), t
    for t in ("capybara spa", "horoscope", "hunger games", "vintage van life"):
        assert skip_hit(t, pats) is None, t


def test_sensitive_filter():
    assert sensitive_hit("costa rica earthquake", CFG.trends.sensitive_keywords) == "earthquake"
    assert sensitive_hit("Deadpool trailer", ["dead"]) is None  # whole words only
    assert normalize("  Hello,   World! ") == "hello world"


def test_sanitize_drops_ip_leaks_and_cleans_tags():
    raw = {
        "protected_terms": ["Stranger Things"],
        "theme": "retro 80s monster mystery",
        "designs": [
            {"style": "retro_badge", "subject": "kids on bikes under a neon sky with a shadowy monster", "slogan": "",
             "text_layout": "top", "title": "Retro 80s Monster Night", "tags": ["Retro 80s!", "stranger things", "synthwave",
             "a very long tag that has too many words"], "description": "Kids, bikes and Stranger Things vibes.",
             "poster_subject": "neon small town at night"},
            {"style": "meme_text", "subject": "Stranger Things logo with a waffle", "slogan": "Stranger Things fan",
             "text_layout": "bottom", "title": "Upside Down Fan", "tags": [], "description": "", "poster_subject": ""},
            {"style": "meme_text", "subject": "a sleepy frog holding a coffee mug", "slogan": "", "text_layout": "bottom",
             "title": "Sleepy Frog Coffee", "tags": ["frog"], "description": "", "poster_subject": ""},
        ],
    }
    designs, protected = concepts.sanitize(raw, {"title": "stranger things season 5", "context": []}, CFG)
    titles = [d["title"] for d in designs]
    assert "Upside Down Fan" not in titles  # slogan leaked the show name
    first = designs[0]
    assert "stranger things" not in first["tags"] and "retro 80s" in first["tags"]
    assert "Stranger Things" not in first["description"]
    assert first["text_layout"] == "none"  # no slogan -> no text
    frog = next(d for d in designs if d["title"] == "Sleepy Frog Coffee")
    assert frog["style"] == "flat_vector"  # meme_text needs a slogan
    assert "Stranger Things" in protected


def test_extract_json_handles_fences_and_think():
    assert extract_json('<think>hmm</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"b": [1, 2]} hope that helps') == {"b": [1, 2]}


def _blob_image(size=512, specks=True, hole=True, touch=False) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    box = (0, 100, 380, 420) if touch else (100, 100, 412, 412)
    d.ellipse(box, fill=(220, 60, 60, 255))
    if hole:
        d.ellipse((250, 250, 253, 253), fill=(0, 0, 0, 0))
    if specks:
        for x in (20, 480):
            d.rectangle((x, 20, x + 3, 23), fill=(255, 255, 255, 255))
    return im


def test_clean_alpha_removes_specks_and_fills_pinholes():
    im = _blob_image()
    alpha, m = cutout.clean_alpha(np.array(im.getchannel("A")), CFG.cutout)
    assert m["removed_specks"] == 2 and m["fragments"] == 1 and m["filled_holes"] == 1
    assert alpha[21, 21] == 0 and alpha[251, 251] == 255


def test_prepare_finalize_and_border_touch():
    art, metrics = cutout.prepare(_blob_image(touch=True), CFG.cutout)
    assert metrics["border_touch"] > 0
    final = cutout.finalize(art, CFG.cutout)
    assert max(final.size) == CFG.cutout.target_long_side
    a = np.array(final)
    assert (a[a[..., 3] == 0][:, :3] == 0).all()  # hidden pixels zeroed
    assert a[0, 0, 3] == 0  # padding is transparent


@pytest.mark.skipif(not FONTS, reason="fonts not downloaded")
def test_text_compose_adds_slogan_and_stays_connected():
    art, _ = cutout.prepare(_blob_image(specks=False), CFG.cutout)
    out = text.compose(art, "Spooky But Cute", "top_bottom", FONTS[:1], seed=1)
    assert out.height > art.height
    hard = (np.array(out.getchannel("A")) > 128).astype(np.uint8)
    import cv2

    n, _ = cv2.connectedComponents(hard)
    assert n - 1 <= 3  # letters overlap the art, so the silhouette stays nearly one piece
    assert text.split_balanced("one two three four") == ("one two", "three four")


def test_sticker_checks_flag_background_and_dupes():
    full = Image.new("RGBA", (512, 512), (200, 200, 200, 255))
    reasons, m = checks.sticker({"coverage": 1.0, "border_touch": 1.0, "fragments": 1}, full, CFG.qa, [])
    assert any("background" in r for r in reasons) and any("cut off" in r for r in reasons)
    art = cutout.finalize(cutout.prepare(_blob_image(specks=False), CFG.cutout)[0], CFG.cutout)
    h = checks.phash(art)
    reasons, _ = checks.sticker({"coverage": 0.4, "border_touch": 0.0, "fragments": 1}, art, CFG.qa, [h])
    assert any("duplicate" in r for r in reasons)


def test_poster_finalize_exact_size():
    out = poster.finalize(Image.new("RGB", (3328, 4864), (10, 20, 30)), CFG.poster)
    assert out.size == (CFG.poster.width, CFG.poster.height)


def test_printify_pricing_and_scale():
    assert retail_price(1000, 0.35) == 1599
    assert str(retail_price(250, 0.35)).endswith("99")
    assert fit_scale(1000, 1000, 2700, 1050) == pytest.approx(0.35, abs=0.01)  # square art on a wide mug area
    assert fit_scale(1000, 1000, 1000, 1200) == 0.9


def test_db_platform_queue(tmp_path):
    db = DB(tmp_path / "t.sqlite3")
    now = now_iso()
    base = {"kind": "sticker", "style": "kawaii", "status": "ready", "title": "x", "created_at": now, "updated_at": now,
            "ready_at": now, "files": {"final": "000001/sticker.png"}}
    a = db.insert("designs", base)
    b = db.insert("designs", {**base, "ready_at": "2099-01-01T00:00:00"})
    assert db.next_for_platform("redbubble", "sticker")["id"] == a
    db.insert("uploads", {"design_id": a, "platform": "redbubble", "status": "success", "created_at": now})
    assert db.next_for_platform("redbubble", "sticker")["id"] == b
    assert db.next_for_platform("teepublic", "sticker")["id"] == a
    assert db.uploads_today("redbubble") == 1
    assert db.ready_backlog(["redbubble", "teepublic"]) == 2
    assert db.ready_backlog(["redbubble"]) == 1
