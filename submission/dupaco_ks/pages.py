"""Page-level knowledge: how much to trust a page, when it was written, and clean text for search."""
from __future__ import annotations

import re
from urllib.parse import urlparse

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
PUBLISHED = re.compile(rf"\b({MONTHS})\s+(\d{{1,2}}),\s+(\d{{4}})\b")
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}

# Trust ladder used whenever two pages state the same fact differently.
OFFICIAL, PRODUCT, GENERAL, CAMPAIGN, CALCULATOR, PDF, EDITORIAL = 100, 80, 50, 40, 20, 15, 10
CAMPAIGN_PATHS = {"/switch-and-earn", "/earn-more", "/homebuying-guidance", "/mortgage-refi"}
NOISE = re.compile(r"/(tag|author)/|/page/\d+")


def path_of(url: str) -> str:
    return urlparse(url).path.rstrip("/").lower() or "/"


def authority(url: str) -> int:
    p = path_of(url)
    if p in ("/rates", "/business-rates", "/personal/rates"):
        return OFFICIAL
    if "calculator" in p:
        return CALCULATOR
    if p.startswith("/hubfs/"):
        return PDF
    if p.startswith(("/learn/", "/newsroom", "/business/resources")) or re.match(r"/20\d\d/", p):
        return EDITORIAL
    if p.endswith("-lp") or p in CAMPAIGN_PATHS:
        return CAMPAIGN
    if p.startswith(("/personal/", "/business/")):
        return PRODUCT
    return GENERAL


def is_indexable(url: str) -> bool:
    return not NOISE.search(path_of(url))


def published_on(markdown: str) -> str | None:
    """Publication date printed at the top of newsroom/blog posts, as ISO, if there is one."""
    m = PUBLISHED.search(markdown[:900])
    return f"{m.group(3)}-{MONTH_NUM[m.group(1)]:02d}-{int(m.group(2)):02d}" if m else None


_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_CUT_MARKERS = ("## Handpicked for You", "**ABOUT DUPACO", "### **ABOUT", "ABOUT DUPACO COMMUNITY")


def clean_markdown(md: str) -> str:
    for marker in _CUT_MARKERS:
        i = md.find(marker)
        if i > 0:
            md = md[:i]
    md = _IMAGE.sub(" ", md)
    md = _LINK.sub(r"\1", md)
    md = re.sub(r"\\([*_$#|\[\]])", r"\1", md)
    return md.replace("**", "").replace("__", "")
