"""One crawl of the website, turned into facts we can look up: rate rows, hours, org figures, addresses."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from .pages import authority, clean_markdown, is_indexable, path_of, published_on
from .search import Index
from .tables import Row, parse_rows

DAY_LINE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\b.*:", re.I)
ORG_PATTERNS = {
    "branches": re.compile(r"(\d+)\s+branch office locations", re.I),
    "members": re.compile(r"(?:more than|over)\s+(\d{2,3},\d{3})\b(?=[^.]{0,40}(?:assets|members))", re.I),
    "assets": re.compile(r"assets exceeding\s+\$(\d+(?:\.\d+)?\s+billion)", re.I),
    "employees": re.compile(r"((?:over|nearly|more than)\s+\d{3})\s+employees", re.I),
    "counties": re.compile(r"serves residents in\s+(\d+)\s+counties", re.I),
    "footprint": re.compile(r"serves residents in\s+(\d+\s+counties throughout [^.]+)\.", re.I),
}
ADDRESS = re.compile(r"Dupaco Branch\s*\n+\s*([^\n]+?,\s*[A-Z]{2},\s*\d{5})")


@dataclass(frozen=True)
class Schedule:
    lobby: tuple
    drive_up: tuple
    where: str
    url: str
    published: str


@dataclass(frozen=True)
class OrgFact:
    value: str
    url: str
    published: str


class View:
    """The website as it stood on one crawl date."""

    def __init__(self, snapshot):
        self.id = snapshot.snapshot_id
        self.taken_on = snapshot.taken_on
        self.pages = {p.url.rstrip("/"): p for p in snapshot.pages}
        self.by_path = {path_of(p.url): p for p in snapshot.pages}
        self.rows: list[Row] = []
        for page in snapshot.pages:
            if "%" in page.markdown and "|" in page.markdown and is_indexable(page.url):
                self.rows.extend(parse_rows(page.markdown, page.url, self.id, self.taken_on, authority(page.url)))
        self.current = self._current_rows()
        self.index = Index(snapshot.pages)
        self.schedules = self._schedules(snapshot.pages)
        self.org = self._org(snapshot.pages)
        contact = self.by_path.get("/contact")
        self.addresses = ADDRESS.findall(contact.markdown) if contact else []

    def _current_rows(self) -> list[Row]:
        """For each product, keep only the rows from the most trusted page that lists it."""
        groups: dict[tuple, list[Row]] = defaultdict(list)
        for row in self.rows:
            groups[(row.family, row.product, row.audience)].append(row)
        out: list[Row] = []
        for rows in groups.values():
            top = max(r.authority for r in rows)
            best: dict[tuple, Row] = {}
            for r in sorted((r for r in rows if r.authority == top), key=lambda r: (r.effective or "", r.url), reverse=True):
                best.setdefault(r.key, r)
            out.extend(best.values())
        return out

    def _schedules(self, pages) -> list[Schedule]:
        found: dict[tuple, Schedule] = {}
        for page in sorted(pages, key=lambda p: p.url):
            if "Lobby" not in page.markdown:
                continue
            text = re.sub(r"\b(Lobby|Drive.?up):?\s+(?=(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun))", r"\1\n", clean_markdown(page.markdown))
            text = re.sub(r"[ \t]+(?=(?:Tues|Wednes|Thurs|Fri|Satur|Sun)day[^:\n]{0,14}:)", "\n", text)
            text = re.sub(r"[ \t]+(?=Drive.?up\b)", "\n", text)
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            for i, line in enumerate(lines):
                if line.rstrip(":") != "Lobby":
                    continue
                lobby, j = _day_lines(lines, i + 1)
                drive: tuple = ()
                if j < len(lines) and re.match(r"Drive.?up", lines[j], re.I):
                    drive, _ = _day_lines(lines, j + 1)
                if lobby:
                    found.setdefault((lobby, drive), Schedule(
                        lobby, drive, " ".join(lines[:i]), page.url, published_on(page.markdown) or ""))
        return sorted(found.values(), key=lambda s: (s.published, s.url), reverse=True)

    def _org(self, pages) -> dict[str, list[OrgFact]]:
        """Boilerplate figures, newest publication first. Newest wins, not most repeated."""
        out: dict[str, list[OrgFact]] = defaultdict(list)
        for page in pages:
            date = published_on(page.markdown)
            if not date:
                continue
            for name, rx in ORG_PATTERNS.items():
                m = rx.search(page.markdown)
                if m:
                    out[name].append(OrgFact(m.group(1), page.url, date))
        for facts in out.values():
            facts.sort(key=lambda f: (f.published, f.url), reverse=True)
        return out


def _day_lines(lines: list[str], start: int) -> tuple[tuple, int]:
    got, j = [], start
    while j < len(lines) and DAY_LINE.match(lines[j]) and len(lines[j]) < 70:
        got.append(lines[j])
        j += 1
    return tuple(got), j
