import io, boto3
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from ..config.settings import settings
from .watermark import watermark_service

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

    def make_thumbnail(self, image_bytes: bytes, size=(400, 400)) -> bytes:
        img = self._open_corrected(image_bytes)
        img.thumbnail(size, Image.LANCZOS)
        stream = io.BytesIO()
        img.save(stream, format="JPEG", quality=82)
        return stream.getvalue()

    def upload_thumbnail(self, thumb_bytes: bytes, key: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=thumb_bytes, ContentType="image/jpeg")
        return f"https://{self.bucket}.s3.{settings.aws_region}.amazonaws.com/{key}"

    def watermark_and_upload(self, image_bytes: bytes, key: str) -> str:
        """Display version for watermarked events: bottom-right watermark on
        the orientation-corrected, EXIF-stripped image."""
        img = self._open_corrected(image_bytes)
        body = watermark_service.apply_to_jpeg(img)
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

s3_service = S3Service()