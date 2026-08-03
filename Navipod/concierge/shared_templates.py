import subprocess
import time

from fastapi.templating import Jinja2Templates


def _compute_static_version() -> str:
    """Return a short git hash, or epoch seconds as fallback.
    Called once at process start — result is stable for the life of the process
    so all CSS/JS links share the same cache-busting token and the browser can
    actually cache the assets between page loads.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return str(int(time.time()))


# Instancia compartida de plantillas
templates = Jinja2Templates(directory="templates")
# Expose static asset version as a Jinja2 global so every template can use
# {{ static_v }} without any Python-side boilerplate.
templates.env.globals["static_v"] = _compute_static_version()
