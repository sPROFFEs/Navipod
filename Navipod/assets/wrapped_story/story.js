/* global window, document, requestAnimationFrame, fetch, Audio */

(function () {
  const state = {
    year: Number(window.WRAPPED_STORY?.year || new Date().getFullYear()),
    username: window.WRAPPED_STORY?.username || '',
    slides: [],
    index: 0,
    paused: false,
    timer: null,
    audioTimer: null,
    bgFadeTimer: null,
    intervalMs: 10000,
    audio: new Audio(),
    preloadedSrc: '',
    topTrackReady: false,
    introAudio: new Audio('/assets/wrapped_story/audio/light-transition.mp3'),
    backgroundAudio: new Audio('/assets/wrapped_story/audio/wrapped-background.mp3')
  };
  const maxReasonableMinutes = 365 * 24 * 60;

  const root = document.getElementById('story-root');
  const bg = document.getElementById('story-bg');
  const stage = document.getElementById('story-stage');
  const progress = document.getElementById('story-progress');
  const prev = document.getElementById('story-prev');
  const next = document.getElementById('story-next');
  const pause = document.getElementById('story-pause');
  const close = document.getElementById('story-close');
  const resumeTop = document.getElementById('story-resume-top');

  state.backgroundAudio.loop = true;
  state.backgroundAudio.volume = 0;
  state.introAudio.volume = 0.54;

  function esc(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function number(value) {
    const parsed = Number(value || 0);
    if (!Number.isFinite(parsed) || parsed < 0) return '0';
    return parsed.toLocaleString();
  }

  function durationUnit(value, singular, plural) {
    const formatted = Number.isInteger(value)
      ? value.toLocaleString()
      : value.toLocaleString(undefined, { maximumFractionDigits: 1 });
    return {
      value: formatted,
      unit: value === 1 ? singular : plural,
      label: `${formatted} ${value === 1 ? singular : plural}`
    };
  }

  function listeningDuration(minutes) {
    const parsed = Number(minutes || 0);
    if (!Number.isFinite(parsed) || parsed <= 0) return durationUnit(0, 'second', 'seconds');

    const seconds = parsed * 60;
    if (seconds < 60) return durationUnit(Math.round(seconds), 'second', 'seconds');
    if (parsed < 60) return durationUnit(Math.round(parsed), 'minute', 'minutes');

    const hours = parsed / 60;
    if (hours < 24) return durationUnit(Number(hours.toFixed(hours >= 10 ? 0 : 1)), 'hour', 'hours');

    const days = hours / 24;
    if (days < 365) return durationUnit(Number(days.toFixed(days >= 10 ? 0 : 1)), 'day', 'days');

    const years = days / 365;
    return durationUnit(Number(years.toFixed(years >= 10 ? 0 : 1)), 'year', 'years');
  }

  function titleCase(value) {
    const text = String(value || '');
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : '';
  }

  function validMinutes(value) {
    const parsed = Number(value || 0);
    return Number.isFinite(parsed) && parsed > 0 && parsed <= maxReasonableMinutes;
  }

  function validCount(value) {
    const parsed = Number(value || 0);
    return Number.isFinite(parsed) && parsed > 0 && parsed < 1000000;
  }

  function avatarUrl(username) {
    return `/user/avatar/${encodeURIComponent(username || '?')}`;
  }

  function trackCover(track) {
    const id = track?.db_id || track?.id;
    return id ? `/api/cover/${encodeURIComponent(id)}` : '/static/img/default_cover.png';
  }

  function trackStream(track) {
    const id = track?.db_id || track?.id;
    return id ? `/api/stream/${encodeURIComponent(id)}` : '';
  }

  function initials(value) {
    return String(value || '?')
      .trim()
      .slice(0, 2)
      .toUpperCase();
  }

  function repeatIcon() {
    return `<svg class="story-title-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path d="m17 2 4 4-4 4"></path>
      <path d="M3 11V9a3 3 0 0 1 3-3h15"></path>
      <path d="m7 22-4-4 4-4"></path>
      <path d="M21 13v2a3 3 0 0 1-3 3H3"></path>
    </svg>`;
  }

  function icon(name) {
    const icons = {
      prev: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18 9 12l6-6"></path></svg>',
      next: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"></path></svg>',
      pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14"></path><path d="M16 5v14"></path></svg>',
      play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"></path></svg>'
    };
    return icons[name] || '';
  }

  function fadeAudio(audio, targetVolume, duration = 900) {
    window.clearInterval(state.bgFadeTimer);
    const startVolume = Number(audio.volume || 0);
    const startedAt = window.performance.now();
    state.bgFadeTimer = window.setInterval(() => {
      const pct = Math.min(1, (window.performance.now() - startedAt) / duration);
      audio.volume = Math.max(0, Math.min(1, startVolume + (targetVolume - startVolume) * pct));
      if (pct >= 1) window.clearInterval(state.bgFadeTimer);
    }, 50);
  }

  function startBackgroundAudio() {
    state.backgroundAudio
      .play()
      .then(() => fadeAudio(state.backgroundAudio, 0.11, 2200))
      .catch(showSoundPrompt);
  }

  function showSoundPrompt() {
    if (document.getElementById('story-sound-enable')) return;
    const button = document.createElement('button');
    button.id = 'story-sound-enable';
    button.className = 'story-sound-enable';
    button.type = 'button';
    button.textContent = 'Enable sound';
    button.addEventListener(
      'click',
      () => {
        button.remove();
        state.introAudio.play().catch(() => {});
        startBackgroundAudio();
      },
      { once: true }
    );
    root.appendChild(button);
  }

  function brushSpans(prefix, count) {
    return Array.from({ length: count }, (_, index) => {
      const variable = prefix === 'lamp' ? '--lamp-index' : '--fur-index';
      return `<span class="${prefix}-${index + 1}" style="${variable}:${index + 1}"></span>`;
    }).join('');
  }

  function introHelper(number, withLights = false) {
    return `<div class="story-intro-helper-${number}">
      <div class="story-intro-brush">${brushSpans('fur', 31)}</div>
      ${withLights ? `<div class="story-intro-lights">${brushSpans('lamp', 28)}</div>` : ''}
    </div>`;
  }

  function renderIntro() {
    const word = 'NAVIPOD';
    const letters = word
      .split('')
      .map((ch, i) => {
        const isLast = i === word.length - 1;
        const delay = (1.25 + i * 0.08).toFixed(2);
        return `<span class="intro-letter${isLast ? ' glow-pulse' : ''}" style="animation-delay:${delay}s">${ch}</span>`;
      })
      .join('');

    const intro = document.createElement('div');
    intro.className = 'story-intro';
    intro.innerHTML = `
      <div class="story-intro-container" aria-hidden="true">
        <div class="story-intro-logo" data-letter="N">
          ${introHelper(1, true)}
          ${introHelper(2)}
          ${introHelper(3)}
        </div>
      </div>
      <div class="story-intro-word" aria-label="Navipod">${letters}</div>`;
    root.appendChild(intro);
    state.introAudio.play().catch(showSoundPrompt);
    window.setTimeout(() => {
      intro.classList.add('leaving');
      startBackgroundAudio();
    }, 3100);
    window.setTimeout(() => intro.remove(), 4300);
  }

  /* ---- Background: GSAP panel animation (Spotify Wrapped style) ---- */
  const numberOfPanels = 12;
  const rotationCoef = 5;
  let bgTimeline = null;

  function createPanels() {
    let html = '';
    for (let i = 0; i < numberOfPanels; i++) html += `<div class="story-panel1"></div>`;
    for (let i = 0; i < numberOfPanels; i++) html += `<div class="story-panel2"></div>`;
    bg.innerHTML = html;
    requestAnimationFrame(() => {
      root.classList.add('ready');
      buildBgTimeline();
    });
  }

  function buildBgTimeline() {
    if (bgTimeline) bgTimeline.kill();

    const panels = bg.querySelectorAll('.story-panel1');
    const secondaryPanels = bg.querySelectorAll('.story-panel2');
    const elH = window.innerHeight / numberOfPanels;
    const elW = window.innerWidth / numberOfPanels;
    const grad90 = 'linear-gradient(90deg,rgba(255,180,200,1) 0%,rgba(255,89,226,1) 6%,rgba(255,0,211,1) 19%,rgba(255,0,0,1) 72%,rgba(0,0,0,1) 100%)';

    bgTimeline = gsap.timeline({ repeat: -1, paused: false });

    panels.forEach((panel, i) => {
      const stop = 100 - i;
      const wi = window.innerWidth - elW * (12 - i) + elW;
      const he = window.innerHeight - elH * (12 - i) + elH;
      const grad105 = `linear-gradient(105deg,rgba(255,149,236,1) 0%,rgba(255,89,226,1) 6%,rgba(255,0,211,1) 19%,rgba(255,0,0,1) 72%,rgba(0,0,0,1) ${stop}%)`;
      const gradStop = `linear-gradient(90deg,rgba(255,180,200,1) 0%,rgba(255,89,226,1) 6%,rgba(255,0,211,1) 19%,rgba(255,0,0,1) 72%,rgba(0,0,0,1) ${stop}%)`;

      // Initial rotation: unfold from center
      bgTimeline.fromTo(panel, {
        y: elH * 5.5, x: elW * 5.5, width: 0, height: 0, rotation: -360, background: grad105
      }, {
        width: wi, height: he, y: -elH / 1.33 + ((12 - i) * elH) / 1.33, x: 0,
        duration: 1 + 0.1 * (12 - i), ease: 'sine.inOut', rotation: 0, background: grad105
      }, 0);

      // Linear rotation
      bgTimeline.to(panel, {
        rotation: 12 * rotationCoef - (i + 1) * rotationCoef,
        duration: 3, background: gradStop, ease: 'linear'
      }, '>');

      // Reordering
      bgTimeline.to(panel, {
        rotation: 360, y: -elH / 6 + ((12 - i) * elH) / 6, x: -elW / 1.2 + ((12 - i) * elW) / 1.2,
        background: grad90, ease: 'sine.inOut', duration: 1
      }, '>');

      // Linear rotation 2
      bgTimeline.to(panel, {
        rotation: 12 * rotationCoef - (i + 1) * rotationCoef + 360,
        duration: 4, background: grad90, ease: 'linear'
      }, '>');

      if (i === 0) bgTimeline.addLabel('splitStart', '-=0.8');

      // Secondary panels
      secondaryPanels.forEach((twoPanel, index) => {
        const wi2 = window.innerWidth - elW * index + elW;
        bgTimeline.fromTo(twoPanel, {
          y: elH * 5.5, x: elW * 5.5, width: 0, height: 0, rotation: -360,
          background: 'linear-gradient(105deg,rgba(255,149,236,1) 0%,rgba(255,89,226,1) 6%,rgba(255,0,211,1) 19%,rgba(255,0,0,1) 72%,rgba(0,0,0,1) 100%)'
        }, {
          rotation: -90, y: (index * elH) / 4 - wi2, x: -elW / 2 + (index * elW) / 2,
          width: wi2, height: wi2, background: grad90, ease: 'sine.inOut', duration: 1
        }, 'splitStart+=' + (0.05 * index));

        bgTimeline.to(twoPanel, {
          rotation: 12 * rotationCoef - (12 - index) * rotationCoef - 90,
          duration: 5, background: grad90, ease: 'linear'
        }, '>');

        bgTimeline.to(twoPanel, {
          rotation: 300, y: (index * elH) / 2 - wi2, x: window.innerWidth * 1.1 - wi2 * 1.2,
          width: wi2, height: wi2, background: grad90, ease: 'sine.inOut', duration: 1
        }, '>');

        bgTimeline.to(twoPanel, {
          rotation: '+=15', duration: 5, background: grad90, ease: 'linear'
        }, '>');

        bgTimeline.to(twoPanel, {
          rotation: '+=360', y: '-=' + (wi2 * 2), x: '+=' + (wi2 * 2),
          width: wi2, height: wi2, background: grad90, ease: 'sine.inOut', duration: 1
        }, '>');
      });

      // Primary panel exit / continuation
      if (i === 0) {
        bgTimeline.to(panel, {
          rotation: 720 + 90, y: window.innerHeight - ((12 - i) * elH) / 4,
          x: -elW / 2 + ((12 - i) * elW) / 2, width: 0, height: 0, opacity: 0,
          background: grad90, ease: 'sine.inOut', duration: 1
        }, 'splitStart+=' + (0.05 * i));
      } else {
        bgTimeline.to(panel, {
          rotation: 720 + 90, y: window.innerHeight - ((12 - i) * elH) / 4,
          x: -elW / 2 + ((12 - i) * elW) / 2, width: wi, height: wi,
          background: grad90, ease: 'sine.inOut', duration: 1
        }, 'splitStart+=' + (0.05 * i));

        bgTimeline.to(panel, {
          rotation: (12 * rotationCoef - (i + 1) * rotationCoef) / 1.2 + 810,
          duration: 5, background: grad90, ease: 'linear'
        }, '>');

        bgTimeline.to(panel, {
          y: window.innerHeight - ((12 - i) * elH) / 2, x: -elW * 1.2,
          rotation: (12 * rotationCoef - (i + 1) * rotationCoef) / 1.2 + 1180,
          ease: 'sine.inOut', duration: 1, background: grad90
        }, '>');

        bgTimeline.to(panel, {
          rotation: (12 * rotationCoef - (i + 1) * rotationCoef) / 1.2 + 1200,
          duration: 5, background: grad90, ease: 'linear'
        }, '>');

        bgTimeline.to(panel, {
          y: '+=' + (elH * 4), x: '-=' + (elW * 4),
          rotation: (12 * rotationCoef - (i + 1) * rotationCoef) / 1.2 + 1500,
          ease: 'sine.inOut', duration: 1, background: grad90
        }, '>');
      }
    });
  }

  function setSlideTheme() {
    root.dataset.slide = String(state.index);
    bg.style.setProperty('--story-hue', `${(state.index * 18) % 360}deg`);
    // Smoothly advance the GSAP timeline to match the current slide position
    if (bgTimeline) {
      const total = Math.max(1, state.slides.length);
      const targetProgress = (state.index / total);
      gsap.to(bgTimeline, { progress: targetProgress, duration: 1.5, ease: 'power2.inOut', overwrite: true });
    }
  }

  function setBackgroundPaused(paused) {
    if (!bgTimeline) return;
    if (paused) {
      bgTimeline.pause();
    } else {
      bgTimeline.resume();
    }
  }

  async function fetchJson(url) {
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) return null;
    return res.json();
  }

  function listItems(items, mapper, className = '') {
    if (!items.length) return '<p class="story-copy">No data yet.</p>';
    return `<ol class="story-list ${className}">${items.map(mapper).join('')}</ol>`;
  }

  function trackRow(track, index) {
    return `<li class="story-list-with-art">
      <span class="story-rank">#${index + 1}</span>
      <img class="story-thumb" src="${trackCover(track)}" alt="">
      <strong>${esc(track.title)}</strong>
      <em>${number(track.stream_count)} plays</em>
    </li>`;
  }

  function userRow(item, index, value) {
    return `<li class="story-list-with-avatar">
      <span class="story-rank">#${index + 1}</span>
      <img class="story-avatar" src="${avatarUrl(item.username)}" alt="">
      <strong>${esc(item.username || 'Unknown')}</strong>
      <em>${esc(value)}</em>
    </li>`;
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
    const personalDuration = listeningDuration(wrapped.minutes_listened);

    return [
      {
        kicker: `Navipod Wrapped ${wrapped.year}`,
        title: `<span class="story-user-intro"><img src="${avatarUrl(state.username)}" alt="">${esc(state.username || 'Your year')}</span>`,
        copy: 'A fast pass through what actually played, repeated and survived the year.',
        className: 'story-slide-intro'
      },
      {
        kicker: `${titleCase(personalDuration.unit)} listened`,
        title: `<span class="story-duration"><span class="story-big-number">${esc(personalDuration.value)}</span><span>${esc(personalDuration.unit)}</span></span>`,
        copy: `${number(wrapped.event_count)} tracked listens across your library.`
      },
      {
        kicker: 'Your top song',
        title: topSong
          ? `<span class="story-feature-track"><img src="${trackCover(topSong)}" alt=""><span>${esc(topSong.title)}</span></span>`
          : 'No data',
        copy: topSong
          ? `${esc(topSong.artist || 'Unknown Artist')} - ${number(topSong.stream_count)} plays`
          : 'Play more tracks and this slide will fill itself.',
        className: 'story-slide-feature',
        audioSrc: trackStream(topSong),
        audioDuration: Number(topSong?.duration || 0)
      },
      {
        kicker: 'Top songs',
        title: `${repeatIcon()}<span>The repeat list</span>`,
        html: listItems(topSongs.slice(0, 5), trackRow, 'story-list-media'),
        className: 'story-slide-list'
      },
      {
        kicker: 'Top artists',
        title: esc(topArtist?.artist || 'No data'),
        html: listItems(topArtists.slice(0, 5), (artist, index) => {
          return `<li class="story-list-with-art">
            <span class="story-rank">#${index + 1}</span>
            <span class="story-artist-mark">${esc(initials(artist.artist))}</span>
            <strong>${esc(artist.artist)}</strong>
            <em>${number(artist.stream_count)} plays</em>
          </li>`;
        }),
        className: 'story-slide-list'
      },
      {
        kicker: 'Top artist sprint',
        title: 'Who led each month',
        html: listItems(sprint, (item) => {
          return `<li><span>${esc(item.month)}</span><strong>${esc(item.artist)}</strong><em>${number(item.plays)} plays</em></li>`;
        }),
        className: 'story-slide-list story-slide-sprint'
      },
      {
        kicker: 'Wrapped Party',
        title: 'Most listening time',
        html: listItems(
          partyMinutes.filter((item) => validMinutes(item.minutes_listened)).slice(0, 5),
          (item, index) => {
            return userRow(item, index, listeningDuration(item.minutes_listened).label);
          }
        ),
        className: 'story-slide-list'
      },
      {
        kicker: 'Wrapped Party',
        title: 'Biggest repeaters',
        copy: 'Users whose #1 song got the most repeated plays.',
        html: listItems(repeaters.filter((item) => validCount(item.stream_count)).slice(0, 5), (item, index) => {
          return userRow(item, index, `${number(item.stream_count)} plays`);
        }),
        className: 'story-slide-list'
      },
      {
        kicker: wrapped.artist_clip?.title || 'A message from Navipod',
        title: 'For the record',
        copy: wrapped.artist_clip?.message || '',
        className: 'story-slide-copy'
      },
      {
        kicker: 'Done',
        title: 'Keep the list',
        copy: 'Save your top songs playlist or open the full resume view.',
        html: `<div class="story-actions">
            <a class="story-action" href="#" id="story-save-playlist">Save Top Songs</a>
            <a class="story-action secondary" href="/wrapped/${state.year}">Resume</a>
        </div>`,
        className: 'story-slide-copy'
      }
    ];
  }

  function renderProgress() {
    root.style.setProperty('--story-interval', `${state.intervalMs}ms`);
    progress.innerHTML = state.slides
      .map((_, index) => {
        const cls = index < state.index ? 'done' : index === state.index ? 'active' : '';
        return `<div class="story-progress-segment ${cls}"><div class="story-progress-fill"></div></div>`;
      })
      .join('');
  }

  function renderSlide(direction = 1) {
    const slide = state.slides[state.index];
    if (!slide) return;

    // Clear the initial loading message on first render
    const loading = stage.querySelector('.story-loading');
    if (loading) loading.remove();

    stopAudio();
    renderProgress();

    // Build the new article off-DOM
    const article = document.createElement('article');
    article.className = `story-slide ${slide.className || ''}`;
    article.dataset.slide = String(state.index);
    article.innerHTML = `
        <div class="story-kicker">${esc(slide.kicker)}</div>
        <h1 class="story-title">${slide.title}</h1>
        ${slide.copy ? `<p class="story-copy">${esc(slide.copy)}</p>` : ''}
        ${slide.html || ''}`;

    const existingArticle = stage.querySelector('.story-slide');

    if (existingArticle) {
      // --- Sequenced transition: exit first, then enter ---
      state.transitioning = true;
      existingArticle.style.pointerEvents = 'none';
      const exitClass = direction < 0 ? 'slide-exit-right' : 'slide-exit';
      existingArticle.classList.add(exitClass);

      const enterDelay = 300; // ms — wait for exit to mostly finish
      window.setTimeout(() => {
        existingArticle.remove();
        article.classList.add(direction < 0 ? 'slide-enter-left' : 'slide-enter-right');
        stage.appendChild(article);
        // Sync background to new slide after exit is done
        setSlideTheme();
        bindSlideActions();
        playSlideAudio(slide);
        state.transitioning = false;
      }, enterDelay);
    } else {
      // First slide — no exit needed, enter immediately
      stage.appendChild(article);
      setSlideTheme();
      bindSlideActions();
      playSlideAudio(slide);
    }

    scheduleNext();
  }

  function stopAudio() {
    window.clearTimeout(state.audioTimer);
    state.audio.pause();
    state.audio.currentTime = 0;
    fadeAudio(state.backgroundAudio, 0.11, 600);
  }

  function playSlideAudio(slide) {
    if (!slide.audioSrc) return;
    fadeAudio(state.backgroundAudio, 0.025, 450);

    // If this is the pre-loaded top track, use it directly
    const isPreloaded = state.topTrackReady && state.preloadedSrc &&
      (slide.audioSrc === state.preloadedSrc || slide.audioSrc.endsWith(state.preloadedSrc) || state.preloadedSrc.endsWith(slide.audioSrc));

    if (!isPreloaded) {
      state.audio.src = slide.audioSrc;
      state.audio.load();
    }
    state.audio.volume = 0.18;

    const startPlayback = () => {
      const duration = Number(state.audio.duration || slide.audioDuration || 0);
      if (Number.isFinite(duration) && duration > 18) {
        const maxStart = Math.max(0, duration - state.intervalMs / 1000 - 2);
        state.audio.currentTime = Math.floor(Math.random() * maxStart);
      }
      state.audio.play().catch(() => {});
      state.audioTimer = window.setTimeout(() => {
        state.audio.pause();
      }, state.intervalMs - 400);
    };

    // readyState 4 = HAVE_ENOUGH_DATA (fully buffered)
    if (state.audio.readyState >= 3) {
      startPlayback();
    } else {
      state.audio.addEventListener('canplay', startPlayback, { once: true });
    }
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
    if (state.transitioning) return; // Prevent overlapping transitions
    const nextIndex = Math.min(Math.max(state.index + direction, 0), state.slides.length - 1);
    if (nextIndex === state.index) return;
    state.index = nextIndex;
    renderSlide(direction);
  }

  async function init() {
    createPanels();
    resumeTop.href = `/wrapped/${state.year}`;
    close.addEventListener('click', () => {
      window.location.href = `/wrapped/${state.year}`;
    });
    prev.addEventListener('click', () => go(-1));
    next.addEventListener('click', () => go(1));
    prev.innerHTML = icon('prev');
    next.innerHTML = icon('next');
    pause.innerHTML = icon('pause');
    pause.addEventListener('click', () => {
      state.paused = !state.paused;
      pause.innerHTML = icon(state.paused ? 'play' : 'pause');
      if (state.paused) {
        state.audio.pause();
        state.backgroundAudio.pause();
        setBackgroundPaused(true);
      } else if (state.audio.src) {
        state.audio.play().catch(() => {});
        state.backgroundAudio.play().catch(showSoundPrompt);
        setBackgroundPaused(false);
      } else {
        state.backgroundAudio.play().catch(showSoundPrompt);
        setBackgroundPaused(false);
      }
      scheduleNext();
    });
    window.addEventListener('resize', () => buildBgTimeline());
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

    // Aggressively pre-load the top song audio into a buffer
    const topSong = wrapped.top_songs_playlist?.tracks?.[0];
    const topSrc = trackStream(topSong);
    if (topSrc) {
      state.preloadedSrc = topSrc;
      state.audio.preload = 'auto';
      state.audio.src = topSrc;
      state.audio.load();
      // Wait for full buffer or timeout (3s max during intro)
      const bufferReady = new Promise((resolve) => {
        state.audio.addEventListener('canplaythrough', () => {
          state.topTrackReady = true;
          resolve();
        }, { once: true });
        window.setTimeout(resolve, 3000);
      });
      bufferReady.catch(() => {});
    }

    renderIntro();
    renderSlide(1);
  }

  init();
})();
