"""Accueil and Médias keep a 2:3 poster box on a wide desktop and on a phone."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "frontend" / "src" / "styles.css"
FULL_CSS = CSS_PATH.read_text(encoding="utf-8")
DESKTOP_CSS = FULL_CSS.split("@media (max-width: 720px)", 1)[0]


def test_desktop_styles_give_posters_a_definite_box():
    frame = DESKTOP_CSS.split(".poster-frame {", 1)[1].split("}", 1)[0]
    assert "aspect-ratio: 2 / 3" in frame
    assert "flex: 0 0 auto" in frame
    filled = DESKTOP_CSS.split(".poster-frame > img,", 1)[1].split("}", 1)[0]
    assert "position: absolute" in filled
    assert "height: 100%" in filled
    images = FULL_CSS.split(".poster-card img,", 1)[1].split("}", 1)[0]
    assert "display: block" in images
    assert "flex: 0 0 auto" in images
    assert "aspect-ratio: 2 / 3" in images
    assert "var(--poster-card-w)" in FULL_CSS

    home_blocks = (ROOT / "frontend" / "src" / "components" / "HomePersonalBlocks.vue").read_text(encoding="utf-8")
    media = (ROOT / "frontend" / "src" / "views" / "MediaListView.vue").read_text(encoding="utf-8")
    progress = home_blocks.split('class="poster-grid pc-grid pc-grid--home progress-grid"', 1)[1].split("</div>", 1)[0]
    assert "PosterCard" in progress
    grid = media.split('class="poster-grid pc-grid media-library-grid"', 1)[1]
    assert "PosterCard" in grid


def test_poster_frames_keep_their_height_on_desktop_and_phone():
    import pytest

    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    css = CSS_PATH.read_text(encoding="utf-8")
    red = (
        "data:image/svg+xml,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='300'%3E"
        "%3Crect width='200' height='300' fill='%23c4483c'/%3E%3C/svg%3E"
    )
    titles = ("Alpha Vault", "Beta Harbor", "Gamma Lane", "Delta Ring")
    progress = "".join(
        "<button type='button' class='poster-card'>"
        f"<span class='poster-frame'><img src='{red}' alt='' loading='lazy'></span>"
        f"<strong>{title}</strong><span class='hint'>3/8</span>"
        "<span class='track'><span class='fill' style='width:40%'></span></span>"
        "</button>"
        for title in titles
    )
    broken = "http://127.0.0.1:9/missing-poster.png"
    medias = "".join(
        "<article class='poster-card'><button type='button' class='poster-hit'>"
        "<span class='poster-frame'>"
        f"<img src='{red if index else broken}' alt='{title}' loading='lazy'>"
        "</span>"
        f"<strong>{title}</strong><span class='hint'>2024 · Série</span>"
        "</button></article>"
        for index, title in enumerate(titles)
    )
    row = "".join(
        "<button type='button' class='poster-card'>"
        f"<span class='poster-frame'><img src='{red}' alt=''></span>"
        f"<strong>{title}</strong><span class='hint'>S01E01</span></button>"
        for title in titles
    )
    long_label = "12 épisodes non vus – S02E05→S03E01"
    home_cards = "".join(
        "<button type='button' class='poster-card'>"
        f"<span class='poster-frame'><img src='{red}' alt=''></span>"
        "<span class='hint'>Série</span>"
        f"<strong>{title}</strong>"
        f"<span class='hint'>{long_label}</span>"
        "</button>"
        for title in titles[:3]
    )
    html = (
        "<!doctype html><style>"
        + css
        + "</style><div class='shell'><aside class='sidebar'><nav>"
        "<a class='router-link-active'>Accueil</a></nav></aside><main class='content'>"
        "<h1>À voir</h1><article class='panel'><h2>À voir</h2>"
        f"<div class='poster-grid pc-grid pc-grid--home'>{home_cards}</div>"
        f"<div class='poster-row'>{row}</div></article>"
        "<article class='panel'><h2>Progression</h2>"
        f"<div class='poster-grid progress-grid'>{progress}</div></article>"
        f"<h1>Médias</h1><div class='poster-grid'>{medias}</div></main></div>"
    )
    script = """() => ({
      columns: getComputedStyle(document.querySelector('.progress-grid')).gridTemplateColumns.split(' ').length,
      frames: [...document.querySelectorAll('.poster-frame')].map((node) => node.offsetHeight),
      textOverflow: [...document.querySelectorAll('.pc-grid .poster-card')].map((card) => {
        const rect = card.getBoundingClientRect();
        return [...card.querySelectorAll('strong, .hint')].every((node) => {
          const box = node.getBoundingClientRect();
          return box.right <= rect.right + 1 && box.left >= rect.left - 1;
        });
      }),
    })"""
    try:
        browser = sync_playwright().start()
    except Exception as exc:  # pragma: no cover - local browsers only
        pytest.skip(f"playwright unavailable: {exc}")
    try:
        try:
            chrome = browser.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"chrome unavailable: {exc}")
        try:
            for width, columns in ((1440, 4), (390, 3)):
                page = chrome.new_page(viewport={"width": width, "height": 900})
                page.set_content(html, wait_until="domcontentloaded")
                measured = page.evaluate(script)
                assert measured["columns"] >= columns if width == 1440 else measured["columns"] == columns
                assert measured["frames"]
                assert min(measured["frames"]) >= 100
                if width == 390:
                    assert measured["textOverflow"]
                    assert all(measured["textOverflow"])
                page.close()
        finally:
            chrome.close()
    finally:
        browser.stop()

