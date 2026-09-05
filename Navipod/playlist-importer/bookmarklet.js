(async () => {
  try {
    const url = prompt('URL de la playlist de YouTube, YouTube Music o Spotify:');
    if (!url) return;

    const mode = prompt(
      'Destino:\n' +
        '1 = Playlist nueva\n' +
        '2 = Liked Songs\n' +
        '3 = Playlist nueva + Liked Songs\n' +
        '4 = Playlist existente\n' +
        '5 = Playlist existente + Liked Songs'
    );

    const choice = {
      1: { destination: 'playlist', existing: false },
      2: { destination: 'liked', existing: false },
      3: { destination: 'both', existing: false },
      4: { destination: 'playlist', existing: true },
      5: { destination: 'both', existing: true }
    }[mode];

    if (!choice) throw new Error('Modo no válido');

    const body = { url, destination: choice.destination };

    if (choice.existing) {
      const playlistId = prompt('ID numérico de la playlist existente:');
      if (!playlistId || !/^\d+$/.test(playlistId)) {
        throw new Error('El ID de playlist debe ser numérico');
      }
      body.playlist_id = Number(playlistId);
    } else if (choice.destination !== 'liked') {
      const playlistName = prompt('Nombre de la nueva playlist:');
      if (!playlistName || !playlistName.trim()) {
        throw new Error('La playlist necesita un nombre');
      }
      body.playlist_name = playlistName.trim();
    }

    const response = await fetch('/importer/api/imports', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${(await response.text()).slice(0, 500)}`);
    }

    const created = await response.json();
    location.href = `/importer/imports/${encodeURIComponent(created.id)}`;
  } catch (error) {
    alert(`Navipod importer: ${error.message || error}`);
  }
})();
