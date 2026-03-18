import os
import time
import docker
import httpx
from datetime import datetime, timedelta, timezone
import database
from sqlalchemy.orm import Session

# CONFIGURACIÓN
IDLE_THRESHOLD_MINUTES = int(os.getenv("IDLE_THRESHOLD_MINUTES", "30"))
NAVIDROME_PORT = 4533

client = docker.from_env()

def is_player_active(ip: str, username: str) -> bool:
    """
    Pregunta directamente al pod de Navidrome si hay algo reproduciéndose.
    Utiliza la API de Subsonic /rest/getNowPlaying
    """
    try:
        url = f"http://{ip}:{NAVIDROME_PORT}/rest/getNowPlaying"
        # Navidrome confía en el header x-navidrome-user si está configurado así
        headers = {"x-navidrome-user": username}
        params = {
            "u": username,
            "p": "enc:reaper", # Dummy, auth via header
            "v": "1.16.1",
            "c": "concierge_reaper",
            "f": "json"
        }
        
        # Timeout corto, si no responde rápido asumimos que no está streameando o está roto
        with httpx.Client(timeout=2.0) as http:
            resp = http.get(url, headers=headers, params=params)
            
            if resp.status_code == 200:
                data = resp.json()
                # La respuesta suele ser structure: {'subsonic-response': {'nowPlaying': {'entry': [...]}}}
                # Si no hay nada sonando, entry no existe o está vacío.
                sub_resp = data.get("subsonic-response", {})
                now_playing = sub_resp.get("nowPlaying", {})
                entries = now_playing.get("entry", [])
                
                if entries:
                    print(f"[REAPER] {username} is playing: {len(entries)} items.")
                    return True
    except Exception as e:
        print(f"[REAPER-WARN] Could not check activity on pod {ip}: {e}")
    
    return False

def reap_idle_containers():
    print(f"[{datetime.now()}] Starting idle container reaper...")
    
    db = database.SessionLocal()
    try:
        # 1. Obtener todos los usuarios
        users = db.query(database.User).all()
        now = datetime.now(timezone.utc)
        
        reaped_count = 0
        
        for user in users:
            # Si nunca entró, ignoramos (o lo matamos si existe? Asumamos que si existe y no tiene last_access, es muy viejo o error)
            # Pero mejor ser conservadores: si last_access es None, no tocamos (recién creado?)
            if not user.last_access:
                continue
            
            # Asegurarse de que last_access tenga timezone
            last_access = user.last_access
            if last_access.tzinfo is None:
                last_access = last_access.replace(tzinfo=timezone.utc)
            
            idle_duration = now - last_access
            
            # PRIMER FILTRO: Tiempo en DB (Navegación web)
            if idle_duration > timedelta(minutes=IDLE_THRESHOLD_MINUTES):
                container_name = f"navidrome-{user.username}"
                try:
                    container = client.containers.get(container_name)
                    if container.status == "running":
                        # Obtener IP para chequear streaming
                        networks = container.attrs['NetworkSettings']['Networks']
                        ip_address = None
                        if 'navipod-global' in networks:
                            ip_address = networks['navipod-global']['IPAddress']
                        elif networks:
                            ip_address = list(networks.values())[0]['IPAddress']
                        
                        # SEGUNDO FILTRO: ¿Está escuchando música?
                        if ip_address and is_player_active(ip_address, user.username):
                            print(f"[REAPER] SKIPPING {container_name}: user is idle in the web UI but still playing music.")
                            continue

                        print(f"[REAPER] Stopping idle container: {container_name} (Idle for {int(idle_duration.total_seconds()/60)} min)")
                        container.stop()
                        reaped_count += 1
                except docker.errors.NotFound:
                    pass
                except Exception as e:
                    print(f"[REAPER-ERROR] Error with {container_name}: {e}")
        
        if reaped_count > 0:
            print(f"[REAPER] Stopped {reaped_count} container(s).")
        # else:
            # print("[REAPER] No se encontraron contenedores para detener.")

    finally:
        db.close()

if __name__ == "__main__":
    reap_idle_containers()
