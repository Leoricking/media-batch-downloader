import re
import unicodedata
from opencc import OpenCC

cc = OpenCC("s2t")


def clean_text(text: str) -> str:
    if not text:
        return "Untitled"

    text = cc.convert(text)
    text = text.replace("一二", "Bubu ")
    text = text.replace("布布", "Dudu")

    text = "".join(
        ch for ch in text
        if unicodedata.category(ch) not in ("Cf", "Cc")
    )

    text = re.sub(r'[\\/*?:"<>|]', "", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(". ")

    if not text:
        return "Untitled"

    return text[:60]


def safe_title(text: str) -> str:
    return clean_text(text or "Untitled")