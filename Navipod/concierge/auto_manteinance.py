import os
import logging
import shutil
import subprocess
import time
import reaper
logger = logging.getLogger(__name__)

# CONFIGURACIÓN
USERS_ROOT = "/saas-data/users"
TRASH_EXTENSIONS = [".part", ".ytdl", ".tmp", ".cache"]
DIRECTORIES_TO_CLEAN = ["/tmp", "/app/temp"]

def purge_storage():
    logger.info("Starting disk purge at %s", time.ctime())
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
    
    logger.info("Disk purge completed; freed %.3f GB", freed / (1024**3))

def flush_ram():
    logger.info("Attempting RAM flush at %s", time.ctime())
    try:
        subprocess.run(["sync"], check=True)
        # Solo intentar si somos privilegiados, si no, ignorar silenciosamente
        if os.path.exists("/proc/sys/vm/drop_caches"):
            try:
                with open("/proc/sys/vm/drop_caches", "w") as f:
                    f.write("3")
                logger.info("RAM flush completed")
            except PermissionError:
                logger.info("RAM flush skipped: drop_caches permission denied")
    except Exception as e:
        logger.warning("RAM flush failed: %s", e)

if __name__ == "__main__":
    purge_storage()
    flush_ram()
    reaper.reap_idle_containers()
