from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url:         str = "postgresql://postgres:postgres@postgres:5432/scanme_db"
    redis_url:            str = "redis://localhost:6379/0"
    aws_access_key_id:    str
    aws_secret_access_key: str
    aws_region:           str = "eu-north-1"
    s3_bucket_name:       str
    google_drive_api_key: str
    stream_name:          str = "photo.ingest"
    consumer_group:       str = "ingestion-workers"
    consumer_name:        str = "worker-1"
    face_det_size:        int = 1024
    face_det_thresh:      float = 0.4
    # Free-tier watermark: a logo image (PNG with transparency) if the path
    # exists, otherwise the placeholder text below is rendered instead.
    watermark_logo_path:  str = ""
    watermark_text:       str = "ScanMe"

settings = Settings()