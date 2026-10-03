import io, boto3
import urllib.request
from urllib.parse import urlparse, unquote
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from ..config.settings import settings
from .watermark import Watermarker

register_heif_opener()  # lets Pillow open the HEIC/HEIF files drive.py lists

MAX_FETCH_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10

class S3Service:
    def __init__(self):
        self.client = boto3.client(
            's3',
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )
        self.bucket = settings.s3_bucket_name

    def _open_corrected(self, image_bytes: bytes) -> Image.Image:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)   # fix orientation before stripping EXIF
        return img.convert("RGB")

    def strip_exif_and_upload(self, image_bytes: bytes, key: str) -> str:
        img = self._open_corrected(image_bytes)
        clean = io.BytesIO()
        img.save(clean, format="JPEG", quality=95)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=clean.getvalue(), ContentType="image/jpeg")
        return f"https://{self.bucket}.s3.{settings.aws_region}.amazonaws.com/{key}"

    def make_thumbnail(self, image_bytes: bytes, size=(400, 400), watermarker: Watermarker | None = None) -> bytes:
        img = self._open_corrected(image_bytes)
        img.thumbnail(size, Image.LANCZOS)
        if watermarker:
            # Same bottom-right watermark as the display version, applied
            # after resizing so it stays proportionate to the thumbnail.
            img = watermarker.apply(img)
        stream = io.BytesIO()
        img.save(stream, format="JPEG", quality=82)
        return stream.getvalue()

    def upload_thumbnail(self, thumb_bytes: bytes, key: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=thumb_bytes, ContentType="image/jpeg")
        return f"https://{self.bucket}.s3.{settings.aws_region}.amazonaws.com/{key}"

    def watermark_and_upload(self, image_bytes: bytes, key: str, watermarker: Watermarker) -> str:
        """Display version for watermarked events: bottom-right watermark on
        the orientation-corrected, EXIF-stripped image."""
        img = self._open_corrected(image_bytes)
        body = watermarker.apply_to_jpeg(img)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType="image/jpeg")
        return f"https://{self.bucket}.s3.{settings.aws_region}.amazonaws.com/{key}"

    def copy_object(self, src_key: str, dst_key: str) -> str:
        """Server-side S3 copy — display version for non-watermarked events."""
        self.client.copy_object(
            Bucket=self.bucket, Key=dst_key,
            CopySource={"Bucket": self.bucket, "Key": src_key},
            ContentType="image/jpeg", MetadataDirective="REPLACE",
        )
        return f"https://{self.bucket}.s3.{settings.aws_region}.amazonaws.com/{dst_key}"

    def fetch_bytes(self, url: str) -> bytes:
        """Download `url` — via the S3 API when it points into our (private)
        bucket, otherwise plain HTTP(S). Capped at MAX_FETCH_BYTES."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
        if parsed.hostname and parsed.hostname.startswith(f"{self.bucket}.s3."):
            body = self.client.get_object(Bucket=self.bucket, Key=unquote(parsed.path.lstrip("/")))["Body"]
            data = body.read(MAX_FETCH_BYTES + 1)
        else:
            with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_SECONDS) as resp:
                data = resp.read(MAX_FETCH_BYTES + 1)
        if len(data) > MAX_FETCH_BYTES:
            raise ValueError(f"File at {url} exceeds {MAX_FETCH_BYTES} bytes")
        return data

s3_service = S3Service()