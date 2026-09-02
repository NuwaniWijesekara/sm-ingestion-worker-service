import logging
import boto3
from botocore.exceptions import ClientError
from ..config.settings import settings

logger = logging.getLogger(__name__)

class FaceEngine:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.client = boto3.client(
                'rekognition',
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                region_name=settings.aws_region,
            )
            cls._instance.known_collections = set()
        return cls._instance

    def load(self):
        """No-op retained for backwards compatibility."""
        pass

    def ensure_collection(self, collection_id: str):
        if collection_id in self.known_collections:
            return
        try:
            self.client.describe_collection(CollectionId=collection_id)
            self.known_collections.add(collection_id)
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceNotFoundException':
                logger.info(f"Creating AWS Rekognition collection: {collection_id}")
                self.client.create_collection(CollectionId=collection_id)
                self.known_collections.add(collection_id)
            else:
                logger.error(f"Error checking Rekognition collection {collection_id}: {e}")
                raise

    def index_faces(self, bucket: str, photo_key: str, collection_id: str) -> list[str]:
        self.ensure_collection(collection_id)
        try:
            response = self.client.index_faces(
                CollectionId=collection_id,
                Image={
                    'S3Object': {
                        'Bucket': bucket,
                        'Name': photo_key,
                    }
                },
                DetectionAttributes=['DEFAULT'],
                QualityFilter='AUTO'
            )
            face_records = response.get('FaceRecords', [])
            face_ids = [record['Face']['FaceId'] for record in face_records]
            logger.info(f"Rekognition index_faces found {len(face_ids)} face(s) in {photo_key}")
            return face_ids
        except ClientError as e:
            logger.error(f"AWS Rekognition index_faces failed for {photo_key}: {e}")
            raise

face_engine = FaceEngine()
