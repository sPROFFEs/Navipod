import asyncio
import yt_dlp
import os
import json
import time
from typing import List, Dict

CACHE_DIR = "/saas-data/cache"
YT_CACHE_PATH = f"{CACHE_DIR}/youtube_trending.json"

class YoutubeService:
    def __init__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)

    async def get_trending_music(self, country: str = "ES", limit: int = 12, cookie_path: str = None, query_override: str = None, cache_path: str = None) -> List[Dict]:
        # 1. Check Cache (12h for global, 7d for personalized)
        effective_cache = cache_path or YT_CACHE_PATH
        cache_key = f"{country}_{limit}_{cookie_path is not None}_{query_override}"
        
        if os.path.exists(effective_cache):
            try:
                with open(effective_cache, 'r') as f:
                    cache = json.load(f)
                    if cache.get("expires_at", 0) > time.time() and cache.get("cache_key") == cache_key:
                        return cache.get("items", [])
            except:
                pass

        # 2. Extract using yt-dlp with ROTATION
        if query_override:
            urls = [f"ytsearch{limit}:{query_override}"]
        else:
            urls = [
                f"ytsearch{limit}:trending music {country}",
                f"https://www.youtube.com/feed/trending?bp=4gINGgt5dG1hX2NoYXJ0cw%3D%3D"
            ]
        
        # CLIENT ROTATION STRATEGY
        CLIENT_STRATEGIES = [
             {'player_client': ['android', 'web'], 'skip': ['dash', 'hls']},
             {'player_client': ['ios', 'web'], 'skip': ['dash', 'hls']},
             {'player_client': ['web'], 'skip': ['dash', 'hls']},
             {'player_client': ['android_vr'], 'skip': ['dash', 'hls']}
        ]

        base_opts = {
            'quiet': True,
            'extract_flat': True,
            'force_ipv4': True,
            'skip_download': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'socket_timeout': 5,
            'retries': 0,
            'fragment_retries': 0,
            'no_warnings': True,
            'js_runtimes': {'deno': {}, 'node': {}},
            'remote_components': ['ejs:github'],
        }
        
        if cookie_path and os.path.exists(cookie_path):
            base_opts['cookiefile'] = cookie_path

        items = []
        for url in urls:
            # TRY ROTATION FOR EACH URL
            for i, strategy_config in enumerate(CLIENT_STRATEGIES):
                try:
                    loop = asyncio.get_event_loop()
                    def fetch():
                        opts = base_opts.copy()
                        opts['extractor_args'] = {'youtube': strategy_config}
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            return ydl.extract_info(url, download=False)
                    
                    result = await loop.run_in_executor(None, fetch)
                    
                    if result and 'entries' in result:
                        for entry in result['entries'][:limit]:
                            if not entry: continue
                            
                            # FILTER: Duration (Skip long mixes > 10 min)
                            duration = entry.get("duration", 0)
                            if duration and duration > 600: 
                                continue

                            # FILTER: Title checks
                            title = entry.get("title", "Unknown Title")
                            if "viva" in title.lower() or "live" in title.lower() and "auto-generated" in title.lower():
                                continue

                            items.append({
                                "id": entry.get("id"),
                                "title": title,
                                "artist": entry.get("uploader", "YouTube Music"),
                                "image": f"https://i.ytimg.com/vi/{entry.get('id')}/mqdefault.jpg",
                                "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                                "source": "youtube"
                            })
                    
                    if items: break # Success with this strategy
                except Exception as e:
                    # Log strategy failure but continue
                    # print(f"[YT-SERVICE] Strategy {i+1} failed for search: {str(e)[:50]}...")
                    continue
            
            if items: break # Success with this URL pattern

        # 3. Save to cache
        if items:
            expiration = (7 * 24 * 3600) if cache_path else (6 * 3600)
            os.makedirs(os.path.dirname(effective_cache), exist_ok=True)
            with open(effective_cache, 'w') as f:
                json.dump({
                    "items": items,
                    "expires_at": time.time() + expiration, 
                    "cache_key": cache_key
                }, f)
        
        return items

    async def search_videos(self, query: str, limit: int = 10, cookie_path: str = None) -> List[Dict]:
        """Busca vídeos en YouTube"""
        # Reutilizamos la lógica del trending pero forzando source
        return await self.get_trending_music(query_override=query, limit=limit, cookie_path=cookie_path)

    async def get_audio_stream_url(self, video_id: str) -> str:
        """Obtiene una URL temporal de audio directo con ROTACIÓN"""
        url = f"https://www.youtube.com/watch?v={video_id}"
        
        # CLIENT ROTATION STRATEGY
        CLIENT_STRATEGIES = [
             {'player_client': ['android', 'web'], 'skip': ['dash', 'hls']},
             {'player_client': ['ios', 'web'], 'skip': ['dash', 'hls']},
             {'player_client': ['web'], 'skip': ['dash', 'hls']},
             {'player_client': ['android_vr'], 'skip': ['dash', 'hls']}
        ]

        base_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best',
            'quiet': True,
            'extract_flat': False,
            'force_ipv4': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'js_runtimes': {'deno': {}, 'node': {}},
            'remote_components': ['ejs:github'],
            'no_warnings': True
        }
        
        for i, strategy_config in enumerate(CLIENT_STRATEGIES):
            try:
                loop = asyncio.get_event_loop()
                def fetch():
                    opts = base_opts.copy()
                    opts['extractor_args'] = {'youtube': strategy_config}
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(url, download=False)
                        return info.get('url')
                
                result = await loop.run_in_executor(None, fetch)
                if result:
                    return result
            except Exception as e:
                # print(f"[YT-SERVICE] Stream Strategy {i+1} failed: {str(e)[:50]}...")
                continue
                
        return None

youtube_service = YoutubeService()
