from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


CIFAR10_LABELS = [
    "airplane",
    "automobile",
    "bird",
    "cat",
    "deer",
    "dog",
    "frog",
    "horse",
    "ship",
    "truck",
]


class VisionNPUAdapter:
    def __init__(self, npu_easy_repo: Path, model_path: Path):
        self.npu_easy_repo = Path(npu_easy_repo).resolve()
        self.model_path = Path(model_path).resolve()
        self._model = None
        self._npu_easy = None

    def _ensure_imports(self) -> None:
        if self._npu_easy is not None:
            return
        repo_str = str(self.npu_easy_repo)
        if repo_str not in sys.path:
            sys.path.insert(0, repo_str)
        try:
            import onnxruntime as ort  # type: ignore

            # Reduce noisy ORT warnings in redirected Windows logs (especially EP assignment notices).
            severity = int(os.environ.get("OMNIFORGE_ONNXRUNTIME_LOG_SEVERITY", "3"))
            try:
                ort.set_default_logger_severity(max(0, min(4, severity)))
            except Exception:
                pass
        except Exception:
            pass
        import npu_easy  # type: ignore

        self._npu_easy = npu_easy

    def _choose_provider(self) -> str | None:
        try:
            import onnxruntime as ort

            available = ort.get_available_providers()
        except Exception:
            return None
        for provider in (
            "DmlExecutionProvider",
            "OpenVINOExecutionProvider",
            "QNNExecutionProvider",
            "CPUExecutionProvider",
        ):
            if provider in available:
                return provider
        return None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        self._ensure_imports()
        provider = self._choose_provider()
        if provider:
            self._model = self._npu_easy.NPUModel(str(self.model_path), provider=provider)
        else:
            self._model = self._npu_easy.NPUModel(str(self.model_path))

    @staticmethod
    def _preprocess(image_bytes: bytes) -> np.ndarray:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB").resize((224, 224))
        arr = np.asarray(image).astype(np.float32) / 255.0
        arr = arr.transpose(2, 0, 1)
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)[:, None, None]
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)[:, None, None]
        arr = (arr - mean) / std
        return arr[None, ...].astype(np.float32)

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "model_path": str(self.model_path),
            "model_exists": self.model_path.exists(),
            "loaded": self._model is not None,
            "onnxruntime_log_severity": int(os.environ.get("OMNIFORGE_ONNXRUNTIME_LOG_SEVERITY", "3")),
        }
        try:
            import onnxruntime as ort

            out["onnxruntime_providers"] = ort.get_available_providers()
        except Exception as exc:
            out["onnxruntime_error"] = str(exc)
        if self._model is not None:
            try:
                out["session"] = self._model.get_info()
            except Exception as exc:
                out["session_error"] = str(exc)
        return out

    def classify_bytes(self, image_bytes: bytes, top_k: int = 3) -> dict[str, Any]:
        self._ensure_model()
        x = self._preprocess(image_bytes)
        t0 = time.perf_counter()
        outputs = self._model.run(x)
        infer_ms = round((time.perf_counter() - t0) * 1000, 1)
        logits = np.asarray(outputs[0])[0]
        probs = np.exp(logits - np.max(logits))
        probs = probs / np.sum(probs)
        order = np.argsort(probs)[::-1][: max(1, int(top_k))]
        preds = []
        for idx in order:
            idx_int = int(idx)
            preds.append(
                {
                    "class_id": idx_int,
                    "class_name": CIFAR10_LABELS[idx_int] if idx_int < len(CIFAR10_LABELS) else f"class_{idx_int}",
                    "probability": float(probs[idx_int]),
                }
            )
        return {
            "ok": True,
            "provider": getattr(self._model, "provider", None),
            "infer_ms": infer_ms,
            "predictions": preds,
        }

    def classify_path(self, image_path: str | Path, top_k: int = 3) -> dict[str, Any]:
        path = Path(image_path)
        data = path.read_bytes()
        result = self.classify_bytes(data, top_k=top_k)
        result["image_path"] = str(path.resolve())
        return result

    def benchmark_random(self) -> dict[str, Any]:
        self._ensure_model()
        x = np.random.randn(1, 3, 224, 224).astype(np.float32)
        t0 = time.perf_counter()
        outputs = self._model.run(x)
        return {
            "ok": True,
            "provider": getattr(self._model, "provider", None),
            "infer_ms": round((time.perf_counter() - t0) * 1000, 1),
            "output_shape": [list(np.asarray(outputs[0]).shape)],
        }
