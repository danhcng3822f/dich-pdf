import abc
import ast
import logging
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Union

import numpy as np
import httpx
from PIL import Image

try:
    import onnx
except ImportError:
    onnx = None

try:
    import onnxruntime
except ImportError:
    onnxruntime = None

try:
    import cv2
except ImportError:
    cv2 = None

from app.config import settings

logger = logging.getLogger(__name__)

_BACKEND_PROVIDERS = {
    "cpu": ["CPUExecutionProvider"],
    "cuda": ["CUDAExecutionProvider", "CPUExecutionProvider"],
    "dml": ["DmlExecutionProvider", "CPUExecutionProvider"],
}

_preferred_backend: Optional[str] = None


def set_backend(name: str) -> None:
    """Set preferred ONNX Runtime execution backend ('auto', 'cpu', 'cuda', 'dml')."""
    global _preferred_backend
    _preferred_backend = None if name == "auto" else name


def _download_model_file(model_name: str, target_path: Path) -> Path:
    """Download doclayout ONNX model file from mirrors."""
    urls = [
        f"https://huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/main/{model_name}?download=true",
        f"https://hf-mirror.com/wybxc/DocLayout-YOLO-DocStructBench-onnx/resolve/main/{model_name}?download=true",
    ]
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target_path.with_suffix(".tmp")
    for url in urls:
        try:
            logger.info("Downloading model %s from %s...", model_name, url)
            with httpx.Client(follow_redirects=True, timeout=120.0) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with open(temp_target, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            f.write(chunk)
            temp_target.replace(target_path)
            logger.info("Successfully downloaded model %s to %s", model_name, target_path)
            return target_path
        except Exception as e:
            logger.warning("Failed downloading model from %s: %s", url, e)
            if temp_target.exists():
                try:
                    temp_target.unlink()
                except OSError:
                    pass
    raise RuntimeError(f"Could not download model {model_name} from available sources.")


class DocLayoutModel(abc.ABC):
    @staticmethod
    def load_onnx(model_path: Optional[Union[str, Path]] = None) -> "OnnxModel":
        if model_path is not None:
            return OnnxModel(str(model_path))
        return OnnxModel.from_pretrained()

    @staticmethod
    def load_available() -> "OnnxModel":
        return DocLayoutModel.load_onnx()

    @property
    @abc.abstractmethod
    def stride(self) -> int:
        """Stride of the model input."""
        pass

    @abc.abstractmethod
    def predict(self, image: np.ndarray, imgsz: Union[int, tuple] = 1024, **kwargs: Any) -> list:
        """Predict document page layout."""
        pass


class YoloBox:
    """Helper class to store bounding box and classification."""

    def __init__(self, data: Any):
        self.xyxy = data[:4]
        self.conf = data[-2]
        self.cls = data[-1]


class YoloResult:
    """Helper class to store detection results from ONNX model."""

    def __init__(self, boxes: Any, names: Dict[int, str]):
        self.boxes = [YoloBox(data=d) for d in boxes]
        self.boxes.sort(key=lambda x: x.conf, reverse=True)
        self.names = names


class OnnxModel(DocLayoutModel):
    DEFAULT_STRIDE = 32
    DEFAULT_NAMES = {
        0: "title",
        1: "plain text",
        2: "abandon",
        3: "figure",
        4: "figure_caption",
        5: "table",
        6: "table_caption",
        7: "table_footnote",
        8: "isolate_formula",
        9: "formula_caption",
    }

    def __init__(self, model_path: Union[str, Path]):
        model_path = str(model_path)
        self.model_path = model_path

        # Load metadata props from ONNX model file without full deserialization
        try:
            model = onnx.load(model_path, load_external_data=False)
            metadata = {d.key: d.value for d in model.metadata_props}
            self._stride = ast.literal_eval(metadata.get("stride", str(self.DEFAULT_STRIDE)))
            self._names = ast.literal_eval(metadata.get("names", str(self.DEFAULT_NAMES)))
            del model
        except Exception as e:
            logger.warning(
                "Could not read ONNX metadata from %s: %s; using defaults",
                model_path,
                e,
            )
            self._stride = self.DEFAULT_STRIDE
            self._names = self.DEFAULT_NAMES

        sess_options = onnxruntime.SessionOptions()
        sess_options.graph_optimization_level = (
            onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        )

        available_providers = onnxruntime.get_available_providers()

        if _preferred_backend and _preferred_backend in _BACKEND_PROVIDERS:
            providers = [
                p
                for p in _BACKEND_PROVIDERS[_preferred_backend]
                if p in available_providers
            ]
            if not providers:
                providers = ["CPUExecutionProvider"]
        else:
            providers = []
            if "DmlExecutionProvider" in available_providers:
                providers.append("DmlExecutionProvider")
            if "CPUExecutionProvider" in available_providers:
                providers.append("CPUExecutionProvider")
            if not providers:
                providers = available_providers or ["CPUExecutionProvider"]

        # Cache optimized model graph if appropriate
        compiled_providers = {"CoreMLExecutionProvider", "TensorrtExecutionProvider"}
        can_cache = not compiled_providers.intersection(providers)
        optimized_path = model_path + ".optimized"
        if can_cache and os.path.exists(optimized_path):
            model_path_to_load = optimized_path
        else:
            model_path_to_load = model_path
            if can_cache:
                try:
                    sess_options.optimized_model_filepath = optimized_path
                except Exception:
                    pass

        try:
            self.model = onnxruntime.InferenceSession(
                model_path_to_load, sess_options, providers=providers
            )
        except Exception as e:
            logger.warning(
                "Failed to initialize InferenceSession with providers %s (%s). Falling back to CPU.",
                providers,
                e,
            )
            cpu_options = onnxruntime.SessionOptions()
            cpu_options.graph_optimization_level = (
                onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            self.model = onnxruntime.InferenceSession(
                model_path, cpu_options, providers=["CPUExecutionProvider"]
            )

        logger.info("ONNX Runtime providers initialized: %s", self.model.get_providers())

    @staticmethod
    def from_pretrained() -> "OnnxModel":
        model_name = "doclayout_yolo_docstructbench_imgsz1024.onnx"
        models_dir = Path(settings.models_dir)
        models_dir.mkdir(parents=True, exist_ok=True)
        local_path = models_dir / model_name

        if not local_path.exists():
            _download_model_file(model_name, local_path)

        return OnnxModel(str(local_path))

    @property
    def stride(self) -> int:
        return self._stride

    @property
    def names(self) -> Dict[int, str]:
        return self._names

    def resize_and_pad_image(
        self, image: np.ndarray, new_shape: Union[int, tuple]
    ) -> np.ndarray:
        """Resize and pad image so both dimensions are multiples of stride."""
        if isinstance(new_shape, int):
            new_shape = (new_shape, new_shape)

        h, w = image.shape[:2]
        new_h, new_w = new_shape

        r = min(new_h / h, new_w / w)
        resized_h, resized_w = int(round(h * r)), int(round(w * r))

        pad_w = (new_w - resized_w) % self.stride
        pad_h = (new_h - resized_h) % self.stride
        top, bottom = pad_h // 2, pad_h - pad_h // 2
        left, right = pad_w // 2, pad_w - pad_w // 2

        if cv2 is not None:
            image = cv2.resize(
                image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR
            )
            image = cv2.copyMakeBorder(
                image, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114)
            )
            return image
        else:
            pil_img = Image.fromarray(image)
            pil_img = pil_img.resize((resized_w, resized_h), Image.Resampling.BILINEAR)
            resized = np.array(pil_img)
            target_h = resized_h + top + bottom
            target_w = resized_w + left + right
            padded = np.full((target_h, target_w, 3), 114, dtype=image.dtype)
            padded[top : top + resized_h, left : left + resized_w, :] = resized
            return padded

    def scale_boxes(
        self, img1_shape: tuple, boxes: np.ndarray, img0_shape: tuple
    ) -> np.ndarray:
        """Rescales bounding boxes from img1_shape (model input) to img0_shape (original image)."""
        gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])
        pad_x = round((img1_shape[1] - img0_shape[1] * gain) / 2 - 0.1)
        pad_y = round((img1_shape[0] - img0_shape[0] * gain) / 2 - 0.1)

        boxes[..., :4] = (boxes[..., :4] - [pad_x, pad_y, pad_x, pad_y]) / gain
        return boxes

    def predict(
        self, image: np.ndarray, imgsz: Union[int, tuple] = 1024, **kwargs: Any
    ) -> List[YoloResult]:
        orig_h, orig_w = image.shape[:2]
        pix = self.resize_and_pad_image(image, new_shape=imgsz)
        pix = np.transpose(pix, (2, 0, 1))  # HWC -> CHW
        pix = np.expand_dims(pix, axis=0)  # CHW -> BCHW
        pix = pix.astype(np.float32) / 255.0
        new_h, new_w = pix.shape[2:]

        preds = self.model.run(None, {"images": pix})[0]

        # Postprocess predictions: filter conf > 0.25
        preds = preds[preds[..., 4] > 0.25]
        if len(preds) > 0:
            preds[..., :4] = self.scale_boxes(
                (new_h, new_w), preds[..., :4], (orig_h, orig_w)
            )
        return [YoloResult(boxes=preds, names=self._names)]


class ModelInstance:
    value: Optional[OnnxModel] = None


def load_layout_model(
    model_path: Optional[Union[str, Path]] = None, reload: bool = False
) -> Optional[OnnxModel]:
    """Get or load the singleton DocLayout OnnxModel instance, with fallback if not available."""
    if onnxruntime is None:
        logger.warning("onnxruntime is not installed. Running in layout fallback mode.")
        return None

    if ModelInstance.value is None or reload:
        try:
            if model_path is not None:
                ModelInstance.value = OnnxModel(str(model_path))
            else:
                ModelInstance.value = OnnxModel.from_pretrained()
        except Exception as e:
            logger.warning("Could not load layout model: %s. Continuing in fallback mode.", e)
            return None
    return ModelInstance.value
