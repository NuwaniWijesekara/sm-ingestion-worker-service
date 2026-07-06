import logging
from io import BytesIO
import cv2, numpy as np
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

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
        from ..config.settings import settings
        logger.info("Loading InsightFace ArcFace model...")
        self._app = FaceAnalysis(name='buffalo_l', providers=['CUDAExecutionProvider', 'CPUExecutionProvider'])
        self._app.prepare(ctx_id=0, det_size=(settings.face_det_size, settings.face_det_size), det_thresh=settings.face_det_thresh)
        logger.info(f"InsightFace model loaded — det_model.det_thresh = {self._app.det_model.det_thresh}") 

    def _to_bgr(self, image_bytes: bytes) -> np.ndarray:
        pil = Image.open(BytesIO(image_bytes))
        pil = ImageOps.exif_transpose(pil)  
        pil = pil.convert("RGB")
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

    def _normalize(self, vec: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def extract_embeddings(self, image_bytes: bytes) -> list[np.ndarray]:
        if self._app is None:
            self.load()
        bgr = self._to_bgr(image_bytes)
        faces = self._app.get(bgr)

        if not faces:
            faces = self._retry_lower_threshold(bgr)

        if not faces:
            faces = self._retry_with_padding(bgr)

        return [self._normalize(f.embedding) for f in faces] if faces else []

    def _retry_lower_threshold(self, bgr):
        original_thresh = self._app.det_model.det_thresh
        for thresh in (0.3, 0.2, 0.15):
            self._app.det_model.det_thresh = thresh
            faces = self._app.get(bgr)
            if faces:
                logger.info(f"Face found at threshold {thresh}")
                self._app.det_model.det_thresh = original_thresh
                return faces
        self._app.det_model.det_thresh = original_thresh
        return []

    def _retry_with_padding(self, bgr, pad_ratio=0.4):
        h, w = bgr.shape[:2]
        pad_h, pad_w = int(h * pad_ratio), int(w * pad_ratio)
        padded = cv2.copyMakeBorder(bgr, pad_h, pad_h, pad_w, pad_w, cv2.BORDER_REPLICATE)

        original_thresh = self._app.det_model.det_thresh
        for thresh in (original_thresh, 0.3, 0.2):
            self._app.det_model.det_thresh = thresh
            faces = self._app.get(padded)
            if faces:
                self._app.det_model.det_thresh = original_thresh
                # Discard faces whose bbox center falls in the padding margin —
                # these can only be padding artifacts, never real content.
                valid_faces = []
                for f in faces:
                    x0, y0, x1, y1 = f.bbox
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    if pad_w <= cx <= pad_w + w and pad_h <= cy <= pad_h + h:
                        valid_faces.append(f)
                if valid_faces:
                    logger.info(f"Face found after padding retry at threshold {thresh} ({len(valid_faces)} valid of {len(faces)} raw)")
                    return valid_faces
        self._app.det_model.det_thresh = original_thresh
        return []
face_engine = FaceEngine()
