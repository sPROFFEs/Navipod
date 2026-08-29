from pathlib import Path

ASSETS_ROOT = Path(__file__).resolve().parents[2] / "assets"


def test_mobile_shell_restores_gutters_for_party_views():
    css = (ASSETS_ROOT / "css" / "mobile_shell.css").read_text(encoding="utf-8")

    for selector in (
        "#view-container > .party-page-head",
        "#view-container > .party-room-hero",
        "#view-container > .party-owner-notice",
        "#view-container > .party-autoplay-banner",
        "#view-container > .party-room-list",
        "#view-container > .party-layout",
    ):
        assert selector in css

    assert "#view-container > .party-autoplay-banner {\n    width: auto;" in css


def test_party_mobile_controls_keep_touch_sized_targets():
    css = (ASSETS_ROOT / "css" / "ui_party.css").read_text(encoding="utf-8")

    assert ".party-owner-controls button,\n    .party-row-remove { min-width: 44px; min-height: 44px; }" in css
    assert ".party-search input { min-height: 44px; }" in css
    assert ".party-delete-btn { min-height: 44px; }" in css
