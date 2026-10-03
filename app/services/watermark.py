import io
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from ..config.settings import settings

# Watermark is confined to the bottom-right corner so it never overlaps faces
# in the middle of the frame. It is only ever applied to the display copy —
# Rekognition always indexes the untouched original.
MARGIN_RATIO = 0.05   # 5% of width / height from the right / bottom edges
WIDTH_RATIO  = 0.18   # watermark spans ~18% of the image width
OPACITY      = 0.6

class WatermarkService:
    def __init__(self):
        self._logo = self._load_logo(settings.watermark_logo_path)

    @staticmethod
    def _load_logo(path: str):
        if path and Path(path).is_file():
            return Image.open(path).convert("RGBA")
        return None

    @staticmethod
    def _load_font(size: int):
        for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default(size=size)

    def _render_mark(self, target_width: int) -> Image.Image:
        """RGBA watermark scaled to `target_width` — the logo file if one is
        configured, otherwise placeholder text."""
        if self._logo is not None:
            ratio = target_width / self._logo.width
            mark = self._logo.resize(
                (target_width, max(1, round(self._logo.height * ratio))), Image.LANCZOS
            )
        else:
            text = settings.watermark_text
            # Size the font so the text renders at roughly target_width.
            probe = self._load_font(100)
            probe_w = max(1, probe.getbbox(text)[2])
            font = self._load_font(max(10, round(100 * target_width / probe_w)))
            left, top, right, bottom = font.getbbox(text)
            stroke = max(1, font.size // 20)
            mark = Image.new("RGBA", (right - left + 2 * stroke, bottom - top + 2 * stroke), (0, 0, 0, 0))
            ImageDraw.Draw(mark).text(
                (stroke - left, stroke - top), text, font=font,
                fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(0, 0, 0, 160),
            )
        alpha = mark.getchannel("A").point(lambda a: round(a * OPACITY))
        mark.putalpha(alpha)
        return mark

    def apply(self, img: Image.Image) -> Image.Image:
        """Return an RGB copy of `img` with the watermark in the bottom-right
        corner, inset 5% from the right and bottom edges."""
        base = img.convert("RGBA")
        mark = self._render_mark(max(1, round(base.width * WIDTH_RATIO)))
        x = base.width  - round(base.width  * MARGIN_RATIO) - mark.width
        y = base.height - round(base.height * MARGIN_RATIO) - mark.height
        base.alpha_composite(mark, (max(0, x), max(0, y)))
        return base.convert("RGB")

    def apply_to_jpeg(self, img: Image.Image, quality: int = 92) -> bytes:
        stream = io.BytesIO()
        self.apply(img).save(stream, format="JPEG", quality=quality)
        return stream.getvalue()

watermark_service = WatermarkService()
