import re, io, logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from ..config.settings import settings
import time
import random

logger = logging.getLogger(__name__)

# All image types you want to accept from Drive
IMAGE_MIME_TYPES = [
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
]

class GoogleDriveService:
    def __init__(self):
        self._service = None

    def get_service(self):
        if self._service is None:
            self._service = build('drive', 'v3', developerKey=settings.google_drive_api_key, cache_discovery=False)
        return self._service

    def _execute_with_retry(self, fn, max_retries=3):
        for attempt in range(max_retries):
            try:
                srv = self.get_service()
                return fn(srv)
            except (OSError, ConnectionResetError) as e:
                logger.warning(f"Socket connection reset ({e}), rebuilding Google Drive service client (attempt {attempt+1}/{max_retries})...")
                self._service = None
                time.sleep(1)
                if attempt == max_retries - 1:
                    raise

    def extract_folder_id(self, url: str) -> str:
        match = re.search(r"folders/([a-zA-Z0-9-_]+)", url)
        if match:
            return match.group(1)
        raise ValueError(f"Invalid Drive URL: {url}")

    def _list_folder_images(self, folder_id: str):
        """List images directly inside one folder, handling pagination."""
        mime_filter = " or ".join(f"mimeType='{m}'" for m in IMAGE_MIME_TYPES)
        query = f"'{folder_id}' in parents and ({mime_filter}) and trashed=false"

        files = []
        page_token = None
        while True:
            current_token = page_token
            def req(srv):
                return srv.files().list(
                    q=query,
                    fields="nextPageToken, files(id,name,mimeType)",
                    pageSize=1000,
                    pageToken=current_token,
                ).execute()
            results = self._execute_with_retry(req)
            files.extend(results.get('files', []))
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        return files

    def _list_subfolders(self, folder_id: str):
        """List subfolders inside a folder, handling pagination."""
        query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        folders = []
        page_token = None
        while True:
            current_token = page_token
            def req(srv):
                return srv.files().list(
                    q=query,
                    fields="nextPageToken, files(id,name)",
                    pageSize=1000,
                    pageToken=current_token,
                ).execute()
            results = self._execute_with_retry(req)
            folders.extend(results.get('files', []))
            page_token = results.get('nextPageToken')
            if not page_token:
                break
        return folders

    def list_images(self, folder_id: str, recursive: bool = True):
        """List all images in a folder, optionally including subfolders."""
        all_files = self._list_folder_images(folder_id)

        if recursive:
            for sub in self._list_subfolders(folder_id):
                all_files.extend(self.list_images(sub['id'], recursive=True))

        return all_files

    def download_to_memory(self, file_id: str, max_retries: int = 5) -> io.BytesIO:
        for attempt in range(max_retries):
            try:
                srv = self.get_service()
                request = srv.files().get_media(fileId=file_id)
                stream = io.BytesIO()
                downloader = MediaIoBaseDownload(stream, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                stream.seek(0)
                return stream
            except (OSError, ConnectionResetError) as e:
                logger.warning(f"Socket connection reset on download ({e}), rebuilding client (attempt {attempt+1}/{max_retries})...")
                self._service = None
                time.sleep(1)
                if attempt == max_retries - 1:
                    raise
            except Exception as e:
                # Google's abuse-block page raises HttpError 403, but the body
                # is HTML rather than the usual JSON error — treat any 403
                # here as rate-limiting and back off, since legitimate
                # permission errors would surface earlier at the list() step.
                if "403" in str(e) and attempt < max_retries - 1:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(f"Rate limited on {file_id}, retrying in {wait:.1f}s (attempt {attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                raise

drive_service = GoogleDriveService()
