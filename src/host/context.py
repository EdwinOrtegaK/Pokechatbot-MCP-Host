# Contexto de conversación (persistente y truncado)
import json
from pathlib import Path
from typing import List, Dict, Any

class ContextStore:
    def __init__(self, path: Path, max_turns: int = 12):
        self.path = path
        self.max_turns = max_turns

    def load(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
        # Aseguramos máximo de turnos recientes
        return out[-self.max_turns:]

    def append(self, role: str, content: str, extra: Dict[str, Any] | None = None):
        payload = {"role": role, "content": content}
        if extra: payload.update(extra)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
