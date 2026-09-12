"""Text chunking for retrieval.

Splits on paragraph boundaries first, then sentences, and only falls back to a
hard character cut inside a very long sentence. Overlap carries context across
the seam so a fact split across the boundary is still retrievable.
"""

from __future__ import annotations

import re

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
WHITESPACE = re.compile(r"[ \t]+")


def normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = WHITESPACE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    text = normalise(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    units = _split_units(text, chunk_size)
    chunks: list[str] = []
    current = ""

    for unit in units:
        if not current:
            current = unit
        elif len(current) + 1 + len(unit) <= chunk_size:
            current = f"{current}\n{unit}" if unit.startswith(("#", "-", "*")) else f"{current} {unit}"
        else:
            chunks.append(current.strip())
            current = (_tail(current, overlap) + " " + unit).strip() if overlap else unit

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]


def _split_units(text: str, chunk_size: int) -> list[str]:
    units: list[str] = []

    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= chunk_size:
            units.append(paragraph)
            continue

        for sentence in SENTENCE_END.split(paragraph):
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= chunk_size:
                units.append(sentence)
            else:
                # A single sentence longer than the chunk size: hard-cut it.
                units.extend(
                    sentence[i : i + chunk_size] for i in range(0, len(sentence), chunk_size)
                )

    return units


def _tail(text: str, overlap: int) -> str:
    """Last `overlap` characters, trimmed to a word boundary."""
    if overlap <= 0 or len(text) <= overlap:
        return text
    tail = text[-overlap:]
    space = tail.find(" ")
    return tail[space + 1 :] if space != -1 else tail
