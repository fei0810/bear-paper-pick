"""Local settings shared by retrieval and Jev. Values are never logged."""
import os
import re
from pathlib import Path

class ConfigError(ValueError):
    pass

def read_dotenv(path):
    """Read a minimal .env file without exporting its values to subprocesses."""
    path = Path(path)
    if not path.exists():
        return {}
    values = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f".env 第 {number} 行不是 KEY=VALUE")
        key, value = (part.strip() for part in line.split("=", 1))
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigError(f".env 第 {number} 行的键名不合法")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif value.startswith("#"):
            value = ""  # KEY=  # note：值为空，只有注释
        else:
            # 未加引号的值：空白后的 # 起为行内注释，例如 KEY=abc  # note
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key] = value
    return values


def setting(name, default=""):
    file_values = read_dotenv(os.environ.get("BEAR_SIFT_ENV_FILE") or Path(__file__).resolve().parents[1] / ".env")
    return os.environ.get(name) or file_values.get(name) or default
