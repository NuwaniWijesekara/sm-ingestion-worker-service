import re, io, logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from ..config.settings import settings

logger = logging.getLogger(__name__)

class GoogleDriveService:
    def __init__(self):
        self.service = build('drive', 'v3', developerKey=settings.google_drive_api_key)

    def extract_folder_id(self, url: str) -> str:
        match = re.search(r"folders/([a-zA-Z0-9-_]+)", url)
        if match:
            return match.group(1)
        raise ValueError(f"Invalid Drive URL: {url}")

    def list_images(self, folder_id: str):
        query = f"'{folder_id}' in parents and (mimeType='image/jpeg' or mimeType='image/png') and trashed=false"
        results = self.service.files().list(q=query, fields="files(id,name,mimeType)").execute()
        return results.get('files', [])

    def download_to_memory(self, file_id: str) -> io.BytesIO:
        request = self.service.files().get_media(fileId=file_id)
        stream = io.BytesIO()
        downloader = MediaIoBaseDownload(stream, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        stream.seek(0)
        return stream

drive_service = GoogleDriveService()