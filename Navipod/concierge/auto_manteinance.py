import os
import shutil
import subprocess
import time
import reaper

# CONFIGURACIÓN
USERS_ROOT = "/saas-data/users"
TRASH_EXTENSIONS = [".part", ".ytdl", ".tmp", ".cache"]
DIRECTORIES_TO_CLEAN = ["/tmp", "/app/temp"]

def purge_storage():
    print(f"[{time.ctime()}] Iniciando purga de disco...")
    freed = 0
    # 1. Limpiar carpetas temporales
    for path in DIRECTORIES_TO_CLEAN:
        if os.path.exists(path):
            for root, dirs, files in os.walk(path):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        freed += os.path.getsize(fp)
                        os.remove(fp)
                    except: pass

    # 2. Buscar basura en usuarios
    for root, dirs, files in os.walk(USERS_ROOT):
        for f in files:
            if any(f.endswith(ext) for ext in TRASH_EXTENSIONS):
                fp = os.path.join(root, f)
                try:
                    freed += os.path.getsize(fp)
                    os.remove(fp)
                except: pass
        
        for d in dirs:
            if d == ".spotdl-cache":
                dp = os.path.join(root, d)
                try:
                    shutil.rmtree(dp)
                except: pass
    
    print(f"Purge completed. Freed: {round(freed / (1024**3), 3)} GB")

def flush_ram():
    print(f"[{time.ctime()}] Attempting RAM flush...")
    try:
        subprocess.run(["sync"], check=True)
        # Solo intentar si somos privilegiados, si no, ignorar silenciosamente
        if os.path.exists("/proc/sys/vm/drop_caches"):
            try:
                with open("/proc/sys/vm/drop_caches", "w") as f:
                    f.write("3")
                print("RAM flush completed.")
            except PermissionError:
                print("RAM flush (drop_caches) skipped: permission denied (normal in Docker).")
    except Exception as e:
        print(f"RAM flush failed: {e}")

if __name__ == "__main__":
    purge_storage()
    flush_ram()
    reaper.reap_idle_containers()
