"""A small BM25 index over paragraphs, for questions that are not about a rate table."""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .pages import CAMPAIGN, EDITORIAL, GENERAL, PRODUCT, authority, clean_markdown, is_indexable, published_on

TOKEN = re.compile(r"[a-z0-9$]+(?:[-'][a-z0-9]+)*")
STOP = set("""a about an and are at be been by can could did do does for from get give has have how i if in is it its
me my of on or our please should that the their them there these they this to was we were what when where which who
why will with would you your yours tell know need want dupaco dupaco's credit union going new any i'm im
still today like looking some just also not than then so very much many one""".split())
K1, B = 1.4, 0.7
TRUST_WEIGHT = {100: 1.0, PRODUCT: 1.0, GENERAL: 1.0, CAMPAIGN: 0.8, 20: 0.5, 15: 0.55, EDITORIAL: 0.75}


# Members and the website use different words for the same thing.
GLOSSARY = {"mobile wallet": "apple pay google pay samsung pay", "student": "student loan private student loans education"}


def expand(question: str) -> str:
    low = question.lower()
    return question + "".join(f" {extra}" for phrase, extra in GLOSSARY.items() if phrase in low)


def tokens(text: str) -> list[str]:
    return [_stem(t) for t in TOKEN.findall(text.lower()) if t not in STOP]


def _stem(t: str) -> str:
    """Deliberately crude: enough that recognition/recognized and eligible/eligibility meet."""
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "y"
    elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        t = t[:-1]
    return t[:6] if len(t) > 6 and "$" not in t else t


@dataclass
class Chunk:
    url: str
    text: str
    published: str | None
    trust: int


class Index:
    def __init__(self, pages):
        self.chunks: list[Chunk] = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        lengths: list[int] = []
        for page in pages:
            if not is_indexable(page.url):
                continue
            trust = authority(page.url)
            date = published_on(page.markdown)
            for heading, text in _paragraphs(clean_markdown(page.markdown)):
                terms = Counter(tokens(f"{heading} {text} {page.title}"))
                if not terms:
                    continue
                idx = len(self.chunks)
                self.chunks.append(Chunk(page.url, text, date, trust))
                lengths.append(sum(terms.values()))
                for term, tf in terms.items():
                    self.postings[term].append((idx, tf))
        self.lengths = lengths
        self.avg = (sum(lengths) / len(lengths)) if lengths else 1.0
        n = max(1, len(lengths))
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def search(self, question: str, limit: int = 12) -> list[tuple[float, float, Chunk]]:
        """Return (score, coverage, chunk). Coverage is the idf-weighted share of the question found."""
        q = list(dict.fromkeys(tokens(question)))
        if not q:
            return []
        total_idf = sum(self.idf.get(t, 8.0) for t in q)
        score: dict[int, float] = defaultdict(float)
        found: dict[int, float] = defaultdict(float)
        for t in q:
            idf = self.idf.get(t)
            if idf is None:
                continue
            for idx, tf in self.postings[t]:
                norm = tf * (K1 + 1) / (tf + K1 * (1 - B + B * self.lengths[idx] / self.avg))
                score[idx] += idf * norm
                found[idx] += idf
        ranked = []
        for idx, s in score.items():
            chunk = self.chunks[idx]
            weight = TRUST_WEIGHT.get(chunk.trust, 0.8)
            # Among equally relevant news items, the newer one speaks for the credit union today.
            if chunk.published:
                weight *= 1 + 0.02 * max(0, int(chunk.published[:4]) - 2015)
            # A page *named* after the topic beats a page that merely mentions it.
            if chunk.trust >= GENERAL:
                weight *= 1 + 0.5 * len(set(q) & set(tokens(chunk.url.split("dupaco.com")[-1].replace("/", " "))))
            ranked.append((s * weight, found[idx] / total_idf, idx))
        ranked.sort(key=lambda r: (-r[0], r[2]))
        return [(s, cov, self.chunks[idx]) for s, cov, idx in ranked[:limit]]


def _paragraphs(text: str) -> list[tuple[str, str]]:
    out, heading = [], ""
    for raw in re.split(r"\n\s*\n", text):
        para = " ".join(raw.split())
        if not para or para.startswith("|") or "data:image" in para:
            continue
        if para.startswith("#"):
            heading = para.lstrip("# ")
            continue
        if len(para) < 40:
            continue
        out.append((heading if len(heading) < 80 else "", para))
    return out


SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“])|\s+-\s+(?=[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]


def sentence_score(index: Index, sentence: str, question_terms: set) -> float:
    return sum(index.idf.get(t, 0.0) for t in question_terms & set(tokens(sentence)))


def best_passage(index: Index, hits, question: str, max_chars: int = 420):
    """Across the top paragraphs, find the sentence that best answers and quote it with close runners-up.

    Paragraph ranking finds the right page; sentence ranking finds the right line on it. Only
    sentences from the winning paragraph are quoted so an answer never splices two sources.
    """
    q = set(tokens(question))
    best = None
    for rank, (_, _, chunk) in enumerate(hits):
        for sentence in split_sentences(chunk.text):
            weight = TRUST_WEIGHT.get(chunk.trust, 0.8) * (1 - 0.02 * rank)
            if chunk.published:
                weight *= 1 + 0.02 * max(0, int(chunk.published[:4]) - 2015)
            score = _adjusted(index, sentence, q, question) * weight
            if best is None or score > best[0]:
                best = (score, chunk)
    if best is None:
        return None, ""
    chunk = best[1]
    # A page often splits one answer over neighbouring paragraphs, so quote from the whole winning page.
    sentences = list(dict.fromkeys(s for _, _, c in hits if c.url == chunk.url for s in split_sentences(c.text)))
    scores = [_adjusted(index, s, q, question) for s in sentences]
    top = max(scores) if scores else 0
    # Choose the strongest sentences first, then quote them in the order the page wrote them.
    chosen, used = [], 0
    for i in sorted(range(len(sentences)), key=lambda i: (-scores[i], i)):
        if scores[i] < 0.6 * top or scores[i] <= 0:
            break
        if chosen and used + len(sentences[i]) > max_chars:
            continue
        chosen.append(i)
        used += len(sentences[i]) + 1
    # A definition usually takes two sentences: "X is ...". "It provides ...".
    if re.match(r"\s*what(?:'s| is| are)\b", question, re.I) and chosen:
        lead = min(chosen, key=lambda i: (-scores[i], i))
        if lead + 1 < len(sentences) and lead + 1 not in chosen:
            chosen.append(lead + 1)
    text = " ".join(sentences[i] for i in sorted(chosen))[:max_chars + 180]
    # The quote itself must carry the question: a lone shared word is a coincidence, not an answer.
    shared = q & set(tokens(text))
    if len(shared) < min(2, len(q)):
        return None, ""
    return chunk, text.lstrip("_* ")


DEFINING = re.compile(r"\b(provides?|offers?|grants?|helps?|up to|is an?|are)\b", re.I)


def _adjusted(index: Index, sentence: str, q: set, question: str) -> float:
    """Term overlap, nudged by what kind of sentence actually answers a question."""
    score = sentence_score(index, sentence, q)
    if sentence.rstrip().endswith("?"):
        score *= 0.5                    # an FAQ heading restates the question; the answer follows it
    if sentence.lstrip().startswith(("“", '"')):
        score *= 0.7                    # a spokesperson's quote is colour, rarely the fact
    if re.search(r"\bdupaco\b", question, re.I) and re.search(r"\bdupaco\b", sentence, re.I):
        score *= 1.15                   # about the credit union, not the wider industry
    if re.match(r"\s*what(?:'s| is| are)\b", question, re.I) and DEFINING.search(sentence):
        score *= 1.25                   # a definition question wants the defining sentence
    return score
