from pathlib import Path

ASSETS_ROOT = Path(__file__).resolve().parents[2] / "assets"
TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates"


def test_play_buttons_use_outline_icons_and_accessible_labels():
    css = (ASSETS_ROOT / "css" / "ui_player.css").read_text(encoding="utf-8")
    template = (TEMPLATE_ROOT / "base.html").read_text(encoding="utf-8")
    ui_javascript = (ASSETS_ROOT / "js" / "modules" / "ui.js").read_text(encoding="utf-8")

    assert ".player-footer .play-main svg" in css
    assert ".fullscreen-player .fs-play-main svg" in css
    assert "fill: none;" in css
    assert 'id="play-pause-btn" aria-label="Play" title="Play"' in template
    assert 'id="fs-play-pause-btn" aria-label="Play" title="Play"' in template
    assert "setAttribute('aria-label', state.isPlaying ? 'Pause' : 'Play')" in ui_javascript


def test_party_gradient_is_scoped_to_active_fullscreen_party_player():
    css = (ASSETS_ROOT / "css" / "ui_player.css").read_text(encoding="utf-8")
    party_javascript = (ASSETS_ROOT / "js" / "modules" / "party.js").read_text(encoding="utf-8")

    assert ".fullscreen-player.party-player .fs-background" in css
    assert "@keyframes party-player-ambient" in css
    assert "setFullscreenPartyMode(true);" in party_javascript
    assert "setFullscreenPartyMode(false);" in party_javascript
