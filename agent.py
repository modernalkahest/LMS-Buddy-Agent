"""LangChain agent that answers questions using files from Google Drive, retrieved via FAISS.

Usage:
    python agent.py            # chat (builds the index on first run)
    python agent.py --sync     # re-fetch Drive and rebuild the index before chatting
"""

import argparse
import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from drive import create_google_doc, get_drive_service, load_drive_documents
from vectorstore import build_index, load_index

load_dotenv()

INDEX_PATH = os.getenv("FAISS_INDEX_PATH", "faiss_index")
FOLDER_ID = os.getenv("DRIVE_FOLDER_ID") or None
MODEL = os.getenv("OPENAI_MODEL", "gpt-5")

SYSTEM_PROMPT = """You are LMS Buddy, an assistant that answers questions using the user's Google Drive files \
(course notes, slides, assignments, readings).

Use the search_drive tool to find relevant passages before answering anything about the user's material. \
Run several searches with different phrasings when a question spans topics. Base your answer on what the \
retrieved passages say and cite the file name (and page, when given) for each claim. If the files don't \
cover the question, say so plainly rather than filling the gap from general knowledge, then offer what \
you know separately and label it as such."""


NO_FILES_MESSAGE = "The Drive folder has no indexed files yet. Tell the user to add files and ask you to sync."


class DriveIndex:
    """Holds the live FAISS store so the sync tool can swap it out mid-conversation."""

    def __init__(self):
        self.store = load_index(INDEX_PATH)

    def sync(self) -> int:
        print(f"Fetching files from Google Drive ({'folder ' + FOLDER_ID if FOLDER_ID else 'entire Drive'})...")
        docs = load_drive_documents(get_drive_service(), FOLDER_ID)
        if not docs:
            # An empty folder is fine; there is just nothing to index yet.
            print("No supported files found in Drive yet; nothing to index.")
            self.store = None
            return 0
        self.store = build_index(docs, INDEX_PATH)
        return len(docs)


index = DriveIndex()


@tool
def search_drive(query: str, k: int = 5) -> str:
    """Semantic search over the user's indexed Google Drive files.

    Args:
        query: What to look for, phrased as a natural-language description of the content.
        k: Number of passages to return (1-15).
    """
    if index.store is None:
        return NO_FILES_MESSAGE
    results = index.store.similarity_search(query, k=max(1, min(k, 15)))
    if not results:
        return "No matching passages found."
    return "\n\n---\n\n".join(
        f"[{d.metadata['source']}"
        + (f", page {d.metadata['page']}" if "page" in d.metadata else "")
        + f"]\n{d.page_content}"
        for d in results
    )


@tool
def list_indexed_files() -> str:
    """List the names and links of all Google Drive files currently in the index."""
    if index.store is None:
        return NO_FILES_MESSAGE
    files = {
        d.metadata["source"]: d.metadata.get("url") or ""
        for d in index.store.docstore._dict.values()
    }
    return "\n".join(f"- {name} {url}".rstrip() for name, url in sorted(files.items()))


@tool
def sync_drive() -> str:
    """Re-download files from Google Drive and rebuild the index. Use only when the user asks to refresh,
    or says they added or changed files."""
    count = index.sync()
    if count == 0:
        return NO_FILES_MESSAGE
    return f"Re-indexed {count} documents from Google Drive."


@tool
def create_drive_doc(title: str, content: str) -> str:
    """Create a new Google Doc in the user's Drive folder. Use only when the user explicitly asks you to
    write, save, or create a document. Never overwrites or edits existing files.

    Args:
        title: The document's file name.
        content: The full document body as plain text (no Markdown syntax; use blank lines between sections).
    """
    doc = create_google_doc(get_drive_service(), title, content, FOLDER_ID)
    return f"Created Google Doc '{doc['name']}': {doc['webViewLink']}"


def build_agent():
    return create_agent(
        model=ChatOpenAI(model=MODEL),
        tools=[search_drive, list_indexed_files, sync_drive, create_drive_doc],
        system_prompt=SYSTEM_PROMPT,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync", action="store_true", help="Rebuild the index from Drive before chatting")
    args = parser.parse_args()

    if args.sync or index.store is None:
        index.sync()

    agent = build_agent()

    print("\nLMS Buddy ready. Ask about your Drive files (Ctrl+C or 'exit' to quit).\n")
    messages = []
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in {"exit", "quit"}:
            break
        if not question:
            continue

        messages.append({"role": "user", "content": question})
        result = agent.invoke({"messages": messages})
        # Keep the full history (including tool calls) so follow-up questions have context.
        messages = result["messages"]
        print(f"\nbuddy> {messages[-1].text}\n")


if __name__ == "__main__":
    main()
