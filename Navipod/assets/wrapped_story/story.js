/* global window, document, requestAnimationFrame, fetch */

(function () {
  const state = {
    year: Number(window.WRAPPED_STORY?.year || new Date().getFullYear()),
    username: window.WRAPPED_STORY?.username || '',
    slides: [],
    index: 0,
    paused: false,
    timer: null,
    intervalMs: 7600
  };

  const root = document.getElementById('story-root');
  const bg = document.getElementById('story-bg');
  const stage = document.getElementById('story-stage');
  const progress = document.getElementById('story-progress');
  const prev = document.getElementById('story-prev');
  const next = document.getElementById('story-next');
  const pause = document.getElementById('story-pause');
  const close = document.getElementById('story-close');
  const resumeTop = document.getElementById('story-resume-top');

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function number(value) {
    return Number(value || 0).toLocaleString();
  }

  function createPanels() {
    bg.innerHTML = Array.from({ length: 12 }, (_, index) => {
      const hue = index * 6;
      return `<div class="story-panel" style="--panel-index:${index}; filter:hue-rotate(${hue}deg); z-index:${12 - index};"></div>`;
    }).join('');
    requestAnimationFrame(() => root.classList.add('ready'));
  }

  async function fetchJson(url) {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) return null;
    return res.json();
  }

  function listItems(items, mapper) {
    if (!items.length) return '<p class="story-copy">No data yet.</p>';
    return `<ol class="story-list">${items.map(mapper).join('')}</ol>`;
  }

  function sprintLeader(sprint) {
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return (sprint || [])
      .filter((month) => month.artists?.length)
      .slice(-6)
      .map((month) => ({
        month: months[(Number(month.month) || 1) - 1] || month.month,
        artist: month.artists[0].artist,
        plays: Number(month.artists[0].stream_count || 0)
      }));
  }

  function buildSlides(wrapped, party) {
    const topSongs = wrapped.top_songs_playlist?.tracks || [];
    const topArtists = wrapped.top_artists || [];
    const topSong = topSongs[0];
    const topArtist = topArtists[0];
    const sprint = sprintLeader(wrapped.top_artist_sprint);
    const partyMinutes = party?.most_minutes_listened || [];
    const repeaters = party?.biggest_repeaters || [];

    return [
      {
        kicker: `Navipod Wrapped ${wrapped.year}`,
        title: esc(state.username || 'Your year'),
        copy: 'A fast pass through what actually played, repeated and survived the year.'
      },
      {
        kicker: 'Minutes listened',
        title: `<span class="story-big-number">${number(wrapped.minutes_listened)}</span>`,
        copy: `${number(wrapped.event_count)} tracked listens across your library.`
      },
      {
        kicker: 'Your top song',
        title: esc(topSong?.title || 'No data'),
        copy: topSong
          ? `${esc(topSong.artist || 'Unknown Artist')} - ${number(topSong.stream_count)} plays`
          : 'Play more tracks and this slide will fill itself.'
      },
      {
        kicker: 'Top songs',
        title: 'The repeat list',
        html: listItems(topSongs.slice(0, 5), (track, index) => {
          return `<li><span>#${index + 1}</span><strong>${esc(track.title)}</strong><em>${number(track.stream_count)} plays</em></li>`;
        })
      },
      {
        kicker: 'Top artists',
        title: esc(topArtist?.artist || 'No data'),
        html: listItems(topArtists.slice(0, 5), (artist, index) => {
          return `<li><span>#${index + 1}</span><strong>${esc(artist.artist)}</strong><em>${number(artist.stream_count)} plays</em></li>`;
        })
      },
      {
        kicker: 'Top artist sprint',
        title: 'Who led each month',
        html: listItems(sprint, (item) => {
          return `<li><span>${esc(item.month)}</span><strong>${esc(item.artist)}</strong><em>${number(item.plays)} plays</em></li>`;
        })
      },
      {
        kicker: 'Wrapped Party',
        title: 'Most minutes',
        html: listItems(partyMinutes.slice(0, 5), (item) => {
          return `<li><span>#${item.rank}</span><strong>${esc(item.username)}</strong><em>${number(item.minutes_listened)} min</em></li>`;
        })
      },
      {
        kicker: 'Wrapped Party',
        title: 'Biggest repeaters',
        html: listItems(repeaters.slice(0, 5), (item, index) => {
          return `<li><span>#${index + 1}</span><strong>${esc(item.username)}</strong><em>${number(item.stream_count)} plays</em></li>`;
        })
      },
      {
        kicker: wrapped.artist_clip?.title || 'A message from Navipod',
        title: 'For the record',
        copy: wrapped.artist_clip?.message || ''
      },
      {
        kicker: 'Done',
        title: 'Keep the list',
        copy: 'Save your top songs playlist or open the full resume view.',
        html: `<div class="story-actions">
            <a class="story-action" href="#" id="story-save-playlist">Save Top Songs</a>
            <a class="story-action secondary" href="/wrapped/${state.year}">Resume</a>
        </div>`
      }
    ];
  }

  function renderProgress() {
    progress.innerHTML = state.slides
      .map((_, index) => {
        const cls = index < state.index ? 'done' : index === state.index ? 'active' : '';
        return `<div class="story-progress-segment ${cls}"><div class="story-progress-fill"></div></div>`;
      })
      .join('');
  }

  function renderSlide() {
    const slide = state.slides[state.index];
    if (!slide) return;
    renderProgress();
    stage.innerHTML = `<article class="story-slide" data-slide="${state.index}">
        <div class="story-kicker">${esc(slide.kicker)}</div>
        <h1 class="story-title">${slide.title}</h1>
        ${slide.copy ? `<p class="story-copy">${esc(slide.copy)}</p>` : ''}
        ${slide.html || ''}
    </article>`;
    bindSlideActions();
    scheduleNext();
  }

  function bindSlideActions() {
    const save = document.getElementById('story-save-playlist');
    if (!save) return;
    save.addEventListener('click', async (event) => {
      event.preventDefault();
      save.textContent = 'Saving...';
      const res = await fetch(`/api/wrapped/${encodeURIComponent(state.year)}/top-songs/playlist`, {
        method: 'POST',
        credentials: 'same-origin'
      });
      if (!res.ok) {
        save.textContent = 'Could not save';
        return;
      }
      const payload = await res.json();
      window.location.href = `/wrapped/${state.year}`;
      window.sessionStorage.setItem('navipod:lastSavedWrappedPlaylist', String(payload.id || ''));
    });
  }

  function scheduleNext() {
    window.clearTimeout(state.timer);
    if (state.paused || state.index >= state.slides.length - 1) return;
    state.timer = window.setTimeout(() => go(1), state.intervalMs);
  }

  function go(direction) {
    const nextIndex = Math.min(Math.max(state.index + direction, 0), state.slides.length - 1);
    if (nextIndex === state.index) return;
    state.index = nextIndex;
    renderSlide();
  }

  async function init() {
    createPanels();
    resumeTop.href = `/wrapped/${state.year}`;
    close.addEventListener('click', () => {
      window.location.href = `/wrapped/${state.year}`;
    });
    prev.addEventListener('click', () => go(-1));
    next.addEventListener('click', () => go(1));
    pause.addEventListener('click', () => {
      state.paused = !state.paused;
      pause.textContent = state.paused ? 'Play' : 'Pause';
      scheduleNext();
    });
    window.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowLeft') go(-1);
      if (event.key === 'ArrowRight' || event.key === ' ') go(1);
      if (event.key === 'Escape') window.location.href = `/wrapped/${state.year}`;
    });

    const [wrapped, party] = await Promise.all([
      fetchJson(`/api/wrapped/${encodeURIComponent(state.year)}`),
      fetchJson(`/api/wrapped/${encodeURIComponent(state.year)}/party`)
    ]);

    if (!wrapped || wrapped.visible === false || wrapped.enabled === false || wrapped.error) {
      stage.innerHTML = '<div class="story-error">Wrapped is not available right now.</div>';
      return;
    }

    state.year = Number(wrapped.year || state.year);
    state.slides = buildSlides(wrapped, party);
    renderSlide();
  }

  init();
})();
