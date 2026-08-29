# README Screenshot Guide

The new root README is already wired to these filenames. Replace the placeholder files in `.github/assets/` and keep the exact names — no Markdown edits are required.

## Visual rules

Use the real Navipod UI. The strongest README is one that looks like the product people will actually run.

- Prefer dark mode / the normal Navipod visual identity.
- Capture at 100% browser zoom unless a crop specifically needs more density.
- Use realistic album artwork and enough content to make the interface feel alive.
- Avoid test data such as `foo`, `test123`, empty shelves or error banners.
- Hide private domains, emails, usernames, tokens and IP addresses.
- Do not show browser bookmarks, desktop notifications or unrelated tabs.
- Crop cleanly to the app; small browser chrome is fine for the hero if it helps communicate "web app".
- Export to WebP at roughly 80–88 quality to keep the repository light.

## 1. Hero — `.github/assets/navipod-hero.webp`

**Purpose:** the first visual impression of the project.

**Recommended source:** 1600×900 or larger, 16:9.

**Capture:** Home page with the left navigation visible, multiple album covers/mixes, and the player visible if it looks good. Choose a state with visual variety and no modal open.

**Composition:** keep the important UI in the central 80% because GitHub can scale the image down significantly.

**README snippet:**

```html
<p align="center">
  <img src=".github/assets/navipod-hero.webp" alt="Navipod interface preview" width="100%">
</p>
```

## 2. Home — `.github/assets/screenshot-home.webp`

**Purpose:** show the day-to-day product experience.

**Recommended source:** 1400×875 or similar 16:10 landscape.

**Capture:** Home with personal mixes such as Repeat / Deep Cuts / Favorites / Rediscovery plus recommendation/library content. Avoid duplicating the exact hero crop — use a closer view.

**README snippet:**

```html
<img src=".github/assets/screenshot-home.webp" alt="Navipod home with mixes and recommendations">
```

## 3. Search — `.github/assets/screenshot-search.webp`

**Purpose:** immediately communicate the multi-source differentiator.

**Recommended source:** 1400×875 landscape.

**Capture:** run a recognizable music search that returns useful results. Make sure the local/remote source controls or provider chips are visible and, ideally, several result types are on screen.

**Do not:** use a query that exposes private filenames or a personal path.

**README snippet:**

```html
<img src=".github/assets/screenshot-search.webp" alt="Navipod multi-source search">
```

## 4. Party — `.github/assets/screenshot-party.webp`

**Purpose:** show the most social/novel feature.

**Recommended source:** 1400×875 landscape.

**Capture:** an active Party Room with a current track, several queued songs and at least two listeners if possible. Show enough controls to make synchronized playback obvious.

**Do not:** capture the empty room directory unless the room UI itself is much stronger than the active-room view.

**README snippet:**

```html
<img src=".github/assets/screenshot-party.webp" alt="Navipod Party Room">
```

## 5. Mobile — `.github/assets/screenshot-mobile.webp`

**Purpose:** show that Navipod is usable away from the desktop.

**Recommended source:** portrait capture from the Android app, ideally 1080×2160 or similar.

**Capture:** the player or a visually rich mobile Home view. If the media notification is a standout feature, use it in documentation or a second image rather than making the main mobile screenshot mostly notification shade.

**README snippet:**

```html
<img src=".github/assets/screenshot-mobile.webp" alt="Navipod on Android">
```

## 6. Admin — `.github/assets/screenshot-admin.webp` *(optional)*

**Purpose:** useful for the docs or future README expansion, not currently shown in the root screenshot grid.

**Recommended source:** 1400×875 landscape.

**Capture:** System Monitor or another admin page with useful health/update/backup information and no secrets.

**Snippet if you decide to add it:**

```html
<p align="center">
  <img src=".github/assets/screenshot-admin.webp" alt="Navipod administration and system monitor" width="90%">
</p>
```

## Existing 2×2 gallery snippet

The root README already contains this block:

```html
<table>
<tr>
<td width="50%">
  <img src=".github/assets/screenshot-home.webp" alt="Navipod home with mixes and recommendations">
</td>
<td width="50%">
  <img src=".github/assets/screenshot-search.webp" alt="Navipod multi-source search">
</td>
</tr>
<tr>
<td width="50%">
  <img src=".github/assets/screenshot-party.webp" alt="Navipod Party Room">
</td>
<td width="50%">
  <img src=".github/assets/screenshot-mobile.webp" alt="Navipod on Android">
</td>
</tr>
</table>
```

## Fast replacement workflow

After taking the captures:

```bash
# Example from the repository root
cp ~/Pictures/navipod-hero.webp .github/assets/navipod-hero.webp
cp ~/Pictures/navipod-home.webp .github/assets/screenshot-home.webp
cp ~/Pictures/navipod-search.webp .github/assets/screenshot-search.webp
cp ~/Pictures/navipod-party.webp .github/assets/screenshot-party.webp
cp ~/Pictures/navipod-mobile.webp .github/assets/screenshot-mobile.webp
```

Then preview the README on GitHub or in your editor before committing.

## Suggested capture order

1. Hero
2. Search
3. Party
4. Home detail
5. Mobile
6. Admin *(optional)*

The first three do most of the work of explaining why Navipod is different.
