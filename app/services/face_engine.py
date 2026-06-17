import logging
from io import BytesIO
import cv2, numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

class FaceEngine:
    _instance = None
    _app = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self):
        if self._app is not None:
            return
        from insightface.app import FaceAnalysis
        logger.info("Loading InsightFace ArcFace model...")
        self._app = FaceAnalysis(name='buffalo_l', providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
        self._app.prepare(ctx_id=0, det_size=(640, 640))
        logger.info("InsightFace model loaded")

    def _to_bgr(self, image_bytes: bytes) -> np.ndarray:
        pil = Image.open(BytesIO(image_bytes)).convert("RGB")
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    def _normalize(self, vec: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def extract_embeddings(self, image_bytes: bytes) -> list[np.ndarray]:
        if self._app is None:
            self.load()
        faces = self._app.get(self._to_bgr(image_bytes))
        return [self._normalize(f.embedding) for f in faces] if faces else []

face_engine = FaceEngine()