import json
import threading
from pathlib import Path
from paths import DATA_DIR
LOCK = threading.RLock()

def read_json(path, default):
    with LOCK:
        if not Path(path).exists(): return default
        return json.loads(Path(path).read_text(encoding='utf-8'))

def write_json(path, value):
    with LOCK:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)
