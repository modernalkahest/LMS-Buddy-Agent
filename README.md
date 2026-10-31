# LMS Buddy Agent

A LangChain agent that pulls files from your Google Drive, indexes them in a local FAISS vector store, and answers questions about them with an OpenAI model, citing the file each answer comes from.

**Supported files:** Google Docs, Slides and Sheets; PDF, DOCX, TXT, MD and CSV.

## Setup

### 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Google Drive credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project.
2. Enable the **Google Drive API** (APIs & Services → Library).
3. Configure the **OAuth consent screen** (External, then add yourself as a test user).
4. Under **Credentials**, create an **OAuth client ID** of type **Desktop app**.
5. Download the JSON and save it in this folder as `credentials.json`.

On first run a browser window asks you to sign in. The token is then saved to `token.json`. The app requests read-only access.

### 3. Environment

```bash
cp .env.example .env
```

Set `OPENAI_API_KEY`. Set `DRIVE_FOLDER_ID` to limit indexing to one folder and its subfolders. To get the ID, take the last part of the folder's URL.

## Run

```bash
python agent.py          # builds the index on first run, then starts chatting
python agent.py --sync   # re-fetch Drive and rebuild the index first
```

You can also ask the agent to "re-sync my Drive" during a chat.

## How it works

| File | Role |
|---|---|
| `drive.py` | OAuth, recursive folder listing, download/export, and conversion to LangChain `Document`s (PDFs split by page) |
| `vectorstore.py` | Chunks text (1000 chars, 150 overlap), embeds it with OpenAI `text-embedding-3-small`, and saves/loads the FAISS index in `faiss_index/` |
| `agent.py` | `create_agent` with `ChatOpenAI` (`gpt-5` by default, set `OPENAI_MODEL` to change) and three tools: `search_drive`, `list_indexed_files`, `sync_drive` |
