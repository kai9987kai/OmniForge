from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path
from typing import Any


class SupermixChatAdapter:
    def __init__(self, runtime_dir: Path):
        self.runtime_dir = Path(runtime_dir).resolve()
        self.weights_path = self.runtime_dir / "champion_model_chat_supermix_v27_500k_ft.pth"
        self.meta_path = self.runtime_dir / "chat_model_meta_supermix_v27_500k.json"
        self._lock = threading.RLock()
        self._engine = None
        self._chat_web_app = None
        self._device_utils = None
        self._device_info: dict[str, Any] | None = None

    def _ensure_imports(self) -> None:
        if self._chat_web_app is not None and self._device_utils is not None:
            return
        runtime_str = str(self.runtime_dir)
        if runtime_str not in sys.path:
            sys.path.insert(0, runtime_str)
        self._chat_web_app = importlib.import_module("chat_web_app")
        self._device_utils = importlib.import_module("device_utils")

    def _ensure_engine(self) -> None:
        with self._lock:
            if self._engine is not None:
                return
            self._ensure_imports()
            self._device_utils.configure_torch_runtime(
                torch_num_threads=0,
                torch_interop_threads=0,
                allow_tf32=True,
                matmul_precision="high",
            )
            device, device_info = self._device_utils.resolve_device(
                "auto", preference="cuda,npu,xpu,dml,mps,cpu"
            )
            self._device_info = dict(device_info or {})
            self._engine = self._chat_web_app.Engine(
                device,
                self._device_info,
                {
                    "model_size": "auto",
                    "max_turns": 2,
                    "top_labels": 3,
                    "pool_mode": "all",
                    "response_temperature": 0.08,
                    "temperature": 0.0,
                    "style_mode": "auto",
                    "creativity": 0.2,
                },
            )

    def load(self) -> dict[str, Any]:
        self._ensure_engine()
        with self._lock:
            return dict(self._engine.load(str(self.weights_path), str(self.meta_path)))

    def status(self) -> dict[str, Any]:
        self._ensure_engine()
        with self._lock:
            status = dict(self._engine.status())
        if self._device_info:
            status["device_info"] = dict(self._device_info)
        status["weights_exists"] = self.weights_path.exists()
        status["meta_exists"] = self.meta_path.exists()
        return status

    def chat(self, message: str, session_id: str = "omniforge", *, autoload: bool = True) -> dict[str, Any]:
        self._ensure_engine()
        with self._lock:
            if autoload and not self._engine.status().get("loaded"):
                self._engine.load(str(self.weights_path), str(self.meta_path))
            return dict(self._engine.chat(session_id=session_id, user_text=message, show_top_responses=3))

