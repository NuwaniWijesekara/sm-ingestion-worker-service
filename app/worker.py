"""
worker.py — Redis Stream consumer (replaces Celery)
Listens on 'photo.ingest' stream, processes each event.
"""
import socket
# Force IPv4 to prevent connection timeouts on systems with broken IPv6 routing
orig_getaddrinfo = socket.getaddrinfo
def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = patched_getaddrinfo

import logging, time
import redis
from sqlalchemy import create_engine, text, Column, String, DateTime, Enum as SAEnum, ForeignKey, Integer
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from pgvector.sqlalchemy import Vector
import uuid, enum
from datetime import datetime

from .config.settings import settings
from .services.drive import drive_service
from .services.s3 import s3_service
from .services.face_engine import face_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── DB Models (read/write events + images) ─────────────────────
Base = declarative_base()
def _uuid(): return str(uuid.uuid4())

class EventStatus(str, enum.Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    READY      = "ready"
    FAILED     = "failed"

class Event(Base):
    __tablename__ = "events"
    id              = Column(String, primary_key=True, default=_uuid)
    name            = Column(String, nullable=False)
    date            = Column(DateTime, nullable=False)
    drive_url       = Column(String, nullable=True)
    cover_photo_url = Column(String, nullable=True)
    qr_token        = Column(String, unique=True, nullable=False)
    status          = Column(SAEnum(EventStatus), default=EventStatus.PENDING, nullable=False)
    photographer_id = Column(String, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    total_photos    = Column(Integer, default=0)
    images          = relationship("Image", back_populates="event", cascade="all, delete-orphan")

class Image(Base):
    __tablename__ = "images"
    id             = Column(String, primary_key=True, default=_uuid)
    event_id       = Column(String, ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    s3_url         = Column(String, nullable=False)
    thumbnail_url  = Column(String, nullable=True)
    filename       = Column(String, nullable=False)
    face_embedding = Column(Vector(512), nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    event          = relationship("Event", back_populates="images")

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ── Redis Stream setup ─────────────────────────────────────────
r = redis.from_url( settings.redis_url,
    decode_responses=True,
    socket_timeout=10,          # wait up to 10s for a response
    socket_connect_timeout=5,   # wait up to 5s to connect
    retry_on_timeout=True,)

def ensure_stream_group():
    try:
        r.xgroup_create(settings.stream_name, settings.consumer_group, id="0", mkstream=True)
        logger.info(f"Consumer group '{settings.consumer_group}' created")
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" in str(e):
            logger.info("Consumer group already exists")
        else:
            raise

# ── Core ingestion logic ───────────────────────────────────────
def ingest_event(event_id: str, drive_url: str):
    db = SessionLocal()
    processed = 0
    faces_found = 0
    try:
        event = db.query(Event).filter(Event.id == event_id).first()
        if not event:
            logger.error(f"Event {event_id} not found")
            return

        event.status = EventStatus.PROCESSING
        db.commit()

        folder_id = drive_service.extract_folder_id(drive_url)
        files = drive_service.list_images(folder_id)
        logger.info(f"Found {len(files)} images in Drive folder")

        for file in files:
            try:
                image_bytes = drive_service.download_to_memory(file['id']).getvalue()

                # Upload original (EXIF stripped) to S3
                photo_key = f"events/{event_id}/photos/{file['name']}"
                s3_url = s3_service.strip_exif_and_upload(image_bytes, photo_key)

                # Thumbnail
                thumb_bytes = s3_service.make_thumbnail(image_bytes)
                thumb_key = f"events/{event_id}/thumbs/thumb_{file['name']}"
                thumb_url = s3_service.upload_thumbnail(thumb_bytes, thumb_key)

                # ArcFace embeddings
                embeddings = face_engine.extract_embeddings(image_bytes)

                if embeddings:
                    for emb in embeddings:
                        img = Image(
                            event_id=event_id, s3_url=s3_url, thumbnail_url=thumb_url,
                            filename=file['name'],
                            face_embedding=emb.tolist()
                        )
                        db.add(img)
                else:
                    img = Image(
                        event_id=event_id, s3_url=s3_url, thumbnail_url=thumb_url,
                        filename=file['name'],
                        face_embedding=None
                    )
                    db.add(img)
                db.commit()

                processed += 1
                faces_found += len(embeddings)
                logger.info(f"✓ {file['name']}: {len(embeddings)} face(s)")

            except Exception as e:
                logger.error(f"Failed to process {file.get('name')}: {e}")
                continue

        event.status = EventStatus.READY
        event.total_photos = processed
        if not event.cover_photo_url:
            first = db.query(Image).filter(Image.event_id == event_id).first()
            if first:
                event.cover_photo_url = first.thumbnail_url
        db.commit()
        logger.info(f"✅ Ingestion complete: {processed} photos, {faces_found} faces")

    except Exception as e:
        db.rollback()
        event = db.query(Event).filter(Event.id == event_id).first()
        if event:
            event.status = EventStatus.FAILED
            db.commit()
        logger.error(f"Ingestion failed for event {event_id}: {e}")
        raise
    finally:
        db.close()

# ── Main consumer loop ─────────────────────────────────────────
def run():
    logger.info("Loading ArcFace model...")
    face_engine.load()
    logger.info("✓ Ingestion worker started — listening on Redis Stream")

    ensure_stream_group()

    while True:
        try:
            messages = r.xreadgroup(
                settings.consumer_group,
                settings.consumer_name,
                {settings.stream_name: ">"},
                count=1,
                block=5000   # block 5s waiting for messages
            )

            if not messages:
                continue

            for stream, entries in messages:
                for msg_id, data in entries:
                    event_id  = data.get("event_id")
                    drive_url = data.get("drive_url")
                    logger.info(f"Processing message {msg_id}: event={event_id}")
                    try:
                        ingest_event(event_id, drive_url)
                        # ACK only on success
                        r.xack(settings.stream_name, settings.consumer_group, msg_id)
                    except Exception as e:
                        logger.error(f"Message {msg_id} failed — will retry: {e}")
                        # Not ACKed → Redis will redeliver on next worker restart

        except redis.exceptions.ConnectionError:
            logger.warning("Redis connection lost, retrying in 5s...")
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Worker stopped")
            break

if __name__ == "__main__":
    run()