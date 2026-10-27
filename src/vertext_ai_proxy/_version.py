from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re

try:
    __version__ = version("vertext-ai-proxy")
except PackageNotFoundError:
    try:
        pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if pyproject_path.is_file():
            match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', pyproject_path.read_text(encoding="utf-8"))
            __version__ = match.group(1) if match else "0.0.0.dev0"
        else:
            __version__ = "0.0.0.dev0"
    except Exception:
        __version__ = "0.0.0.dev0"
