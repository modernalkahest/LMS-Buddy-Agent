"""FAISS index over Drive documents: chunk, embed, persist, reload."""

import os

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


def get_embeddings() -> OpenAIEmbeddings:
    # Changing the model requires rebuilding the index (--sync); vectors from different models aren't comparable.
    return OpenAIEmbeddings(model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL))


def build_index(docs: list[Document], index_path: str) -> FAISS:
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    chunks = splitter.split_documents(docs)
    if not chunks:
        raise ValueError("No text found in the Drive files to index.")

    store = FAISS.from_documents(chunks, get_embeddings())
    store.save_local(index_path)
    print(f"Indexed {len(chunks)} chunks from {len(docs)} documents -> {index_path}/")
    return store


def load_index(index_path: str) -> FAISS | None:
    if not os.path.exists(os.path.join(index_path, "index.faiss")):
        return None
    # The docstore is pickled; this is safe only because we wrote the index ourselves.
    return FAISS.load_local(index_path, get_embeddings(), allow_dangerous_deserialization=True)
