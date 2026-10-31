"""Google Drive access: authenticate, list files, and convert them to LangChain Documents."""

import io
import json
import os

from docx import Document as DocxDocument
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
from langchain_core.documents import Document
from pypdf import PdfReader

# readonly: read every file for indexing. drive.file: create files (limited to files this app creates).
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

FOLDER_MIME = "application/vnd.google-apps.folder"

# Google-native formats have no binary content; they must be exported.
EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIMES = {"text/plain", "text/markdown", "text/csv"}

SUPPORTED_MIMES = set(EXPORT_MIME) | {PDF_MIME, DOCX_MIME} | TEXT_MIMES


def _creds_from_env() -> Credentials | None:
    """Build credentials from the GOOGLE_TOKEN_JSON env var (the contents of token.json), for hosted
    deployments such as Streamlit Cloud where there is no browser to run the OAuth flow."""
    raw = os.getenv("GOOGLE_TOKEN_JSON")
    if not raw:
        return None
    info = json.loads(raw)
    if not set(SCOPES) <= set(info.get("scopes", [])):
        raise RuntimeError("GOOGLE_TOKEN_JSON was not granted the required Drive scopes; re-authorize locally.")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid:
        creds.refresh(Request())  # kept in memory only; the refresh token in the secret stays valid
    return creds


def get_drive_service(credentials_path: str = "credentials.json", token_path: str = "token.json"):
    """Return an authenticated Drive v3 client, running the OAuth browser flow on first use."""
    creds = _creds_from_env()
    if creds:
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        # A token saved before the write scope was added must be re-authorized. Compare against the scopes
        # recorded in the file: creds.scopes just echoes the SCOPES requested above.
        with open(token_path) as f:
            granted = set(json.load(f).get("scopes", []))
        if not set(SCOPES) <= granted:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(
                    f"{credentials_path} not found. Download an OAuth 'Desktop app' client "
                    "from Google Cloud Console (see README)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


def create_google_doc(service, title: str, content: str, folder_id: str | None = None) -> dict:
    """Create a Google Doc from plain text, inside folder_id when given (else My Drive root)."""
    metadata = {"name": title, "mimeType": "application/vnd.google-apps.document"}
    if folder_id:
        metadata["parents"] = [folder_id]
    # Uploading text/plain with a Google Doc target mimeType makes Drive convert it on the fly.
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
    return service.files().create(
        body=metadata, media_body=media, fields="id, name, webViewLink", supportsAllDrives=True
    ).execute()


def list_files(service, folder_id: str | None = None, recursive: bool = True) -> list[dict]:
    """List supported files in a folder (and its subfolders), or across the whole Drive if no folder is given."""
    query = "trashed = false"
    if folder_id:
        query += f" and '{folder_id}' in parents"

    files, page_token = [], None
    while True:
        resp = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink)",
            pageSize=100,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        for f in resp.get("files", []):
            if f["mimeType"] == FOLDER_MIME:
                # Without a folder_id the query already spans every folder.
                if folder_id and recursive:
                    files.extend(list_files(service, f["id"], recursive))
            elif f["mimeType"] in SUPPORTED_MIMES:
                files.append(f)

        page_token = resp.get("nextPageToken")
        if not page_token:
            return files


def _download(service, file: dict) -> bytes:
    if file["mimeType"] in EXPORT_MIME:
        request = service.files().export_media(fileId=file["id"], mimeType=EXPORT_MIME[file["mimeType"]])
    else:
        request = service.files().get_media(fileId=file["id"], supportsAllDrives=True)

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def file_to_documents(service, file: dict) -> list[Document]:
    """Download one Drive file and convert it to Documents (one per page for PDFs)."""
    data = _download(service, file)
    metadata = {
        "source": file["name"],
        "file_id": file["id"],
        "mime_type": file["mimeType"],
        "modified_time": file.get("modifiedTime"),
        "url": file.get("webViewLink"),
    }

    if file["mimeType"] == PDF_MIME:
        reader = PdfReader(io.BytesIO(data))
        return [
            Document(page_content=text, metadata={**metadata, "page": i + 1})
            for i, page in enumerate(reader.pages)
            if (text := page.extract_text() or "").strip()
        ]

    if file["mimeType"] == DOCX_MIME:
        text = "\n".join(p.text for p in DocxDocument(io.BytesIO(data)).paragraphs)
    else:
        text = data.decode("utf-8", errors="replace")

    return [Document(page_content=text, metadata=metadata)] if text.strip() else []


def load_drive_documents(service, folder_id: str | None = None) -> list[Document]:
    """Fetch every supported file under folder_id and return them as Documents."""
    docs = []
    for file in list_files(service, folder_id):
        try:
            docs.extend(file_to_documents(service, file))
            print(f"  loaded: {file['name']}")
        except Exception as e:  # one bad file shouldn't abort the whole sync
            print(f"  skipped {file['name']}: {e}")
    return docs
