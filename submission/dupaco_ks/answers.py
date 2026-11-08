"""Answer one question from one crawl. Each handler either answers or passes to the next one."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .pages import EDITORIAL, PRODUCT, authority, clean_markdown, path_of
from .question import Parsed
from .search import best_passage, expand
from .store import View
from .tables import Row

REFUSAL = "That isn't listed on Dupaco's website, so I can't confirm it. Please contact Dupaco directly."
BASE = "https://www.dupaco.com"
JOIN_URL = "https://join.dupaco.com/"
MAX_ROWS = 18
MIN_COVERAGE = 0.5

# Where a member would go for each topic. Every entry is checked against the crawl before use.
NAVIGATION = [
    (r"branch|\batm\b|locat", "/contact", ""),
    (r"certificate|\bcds?\b|savings|money market", "/personal/savings-account", "#rates"),
    (r"checking", "/personal/checking-accounts", "#rates"),
    (r"\bauto\b|\bcar\b|vehicle|motorcycle", "/personal/loans/auto-motorcycle-loans", "#rates"),
    (r"mortgage|home loan", "/personal/loans/home-loans", "#rates"),
    (r"personal loan", "/personal/loans/personal-loans", "#rates"),
    (r"credit card|visa", "/personal/credit-cards", ""),
    (r"\brates?\b", "/rates", ""),
    (r"contact", "/contact", ""),
]
PRODUCT_PAGES = {
    ("card", "personal"): "/personal/credit-cards",
    ("card", "business"): "/business/credit-cards",
}


@dataclass
class Result:
    text: str
    urls: list = field(default_factory=list)
    confident: bool = True
    note: Optional[str] = None
    rows: list = field(default_factory=list)


def refuse() -> Result:
    return Result(REFUSAL, confident=False)


def answer(p: Parsed, view: View) -> Result:
    for handler in (_links, _contact, _account_updates, _org, _eligibility, _branches, _rates, _passages):
        result = handler(p, view)
        if result is not None:
            return result
    return refuse()


# --- navigation -----------------------------------------------------------------------------------
def _links(p: Parsed, view: View) -> Optional[Result]:
    asks_link = re.search(r"\b(link|url|web ?address|web ?page)\b", p.low)
    joining = re.search(r"\bjoin\b|become a member", p.low) and re.search(r"\bonline\b|\blink\b|\burl\b|\bapply\b|\bwhere\b", p.low)
    if joining:
        about = view.by_path.get("/about/join")
        if about and "join.dupaco.com" in about.markdown:
            return Result(f"You can join online at {JOIN_URL}", [about.url, JOIN_URL])
        return None
    if not asks_link:
        return None
    for rx, path, anchor in NAVIGATION:
        if re.search(rx, p.low) and path in view.by_path:
            url = BASE + path + anchor
            return Result(f"You can find that at {url}", [url])
    return None


# --- contact details ------------------------------------------------------------------------------
def _contact(p: Parsed, view: View) -> Optional[Result]:
    page = view.by_path.get("/contact")
    if not page:
        return None
    if "routing" in p.low:
        m = re.search(r"Routing Number:\**\s*(\d{9})", page.markdown)
        if m:
            also = [q.url for q in view.pages.values() if q.url != page.url and m.group(1) in q.markdown
                    and authority(q.url) >= PRODUCT]
            return Result(f"Dupaco's routing number is {m.group(1)}.", [page.url] + sorted(also)[:4])
    if re.search(r"\b(phone number|telephone|call you|call dupaco)\b", p.low) and "branch" not in p.low:
        m = re.search(r"Phone:\**\s*\[?(\d{3}-\d{3}-\d{4})", page.markdown)
        if m:
            return Result(f"You can reach Dupaco at {m.group(1)}.", [page.url])
    return None


def _account_updates(p: Parsed, view: View) -> Optional[Result]:
    if not re.search(r"\b(update|change|correct)\b.*\b(contact|address|phone|email|name)\b", p.low):
        return None
    page = view.by_path.get("/contact")
    phone = re.search(r"Phone:\**\s*\[?(\d{3}-\d{3}-\d{4})", page.markdown) if page else None
    if not phone:
        return None
    return Result(f"Log in to Shine Online or Mobile Banking and use the Contact Us tab, or call {phone.group(1)}.", [page.url])


def _eligibility(p: Parsed, view: View) -> Optional[Result]:
    if not re.search(r"eligib|who can (join|become)|qualif\w+ (for|to) (join|member)|can i (join|become a member)", p.low):
        return None
    page = view.by_path.get("/about/join")
    footprint = view.org.get("footprint")
    if not page:
        return None
    cleaned = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", page.markdown)
    bullets = re.findall(r"^\s*-\s+(You[^\n]+)", cleaned, re.M)[:4]
    area = f" Dupaco serves {footprint[0].value}." if footprint else ""
    return Result(" ".join(b.rstrip(".") + "." for b in bullets) + area, [page.url] + ([footprint[0].url] if footprint else []))


# --- organisation facts (newest publication wins) -----------------------------------------------------
ORG_QUESTIONS = [
    (r"how many .*(branch|location)|number of (branch|location)", "branches", "Dupaco has {v} branch office locations."),
    (r"how many .*member|number of members|membership (total|size)", "members", "Dupaco has more than {v} members."),
    (r"how many .*employee|number of employees", "employees", "Dupaco has {v} employees."),
    (r"how many .*count(y|ies)|number of counties", "counties", "Dupaco serves residents in {v} counties."),
    (r"total assets|how (big|large)|asset size", "assets", "Dupaco has assets exceeding ${v}."),
]


def _org(p: Parsed, view: View) -> Optional[Result]:
    if re.search(r"used|volunteer|last year", p.low):
        return None
    for rx, name, template in ORG_QUESTIONS:
        if re.search(rx, p.low) and view.org.get(name):
            fact = view.org[name][0]
            others = sorted({f.value for f in view.org[name]} - {fact.value})
            note = f"Older posts say {', '.join(others)}; used the most recently published figure." if others else None
            return Result(template.format(v=fact.value), [fact.url], note=note)
    return None


# --- branches -------------------------------------------------------------------------------------
PLACE = re.compile(r"\b(?:for|at|of|the|our|your)\s+(?:the\s+)?((?:[A-Z][\w'-]+\s+)*[A-Z][\w'-]+)\s+(?:[Bb]ranch|[Ll]ocation|[Oo]ffice)\b")
DATED_CLOSURE = re.compile(r"[^.\n]*\bclos\w*\b[^.\n]*\b((?:January|February|March|April|May|June|July|August|September|October|"
                           r"November|December)\s+\d{1,2},\s+\d{4})", re.I)


def _closure(place: str, p: Parsed, view: View) -> Optional[Result]:
    """Dated closure announcements. A branch page that was never taken down must not read as 'open'."""
    needle = place.lower()
    for page in sorted(view.pages.values(), key=lambda q: q.url):
        if needle in page.title.lower() and "clos" in page.title.lower():
            m = DATED_CLOSURE.search(clean_markdown(page.markdown))
            if m:
                lead = "No. " if re.search(r"\b(open|still)\b", p.low) else ""
                return Result(f"{lead}The {place} branch was scheduled to close on {m.group(1)}.", [page.url])
    return None


def _place(question: str) -> Optional[str]:
    m = PLACE.search(question)
    return m.group(1) if m and m.group(1).lower() not in ("dupaco", "all", "any") else None


def _join_hours(lines) -> str:
    return "; ".join(lines)


def _branches(p: Parsed, view: View) -> Optional[Result]:
    asks_hours = re.search(r"\bhours\b|what time", p.low)
    asks_address = re.search(r"\baddress\b|where is the .*branch", p.low)
    place = _place(p.text)
    if place and not asks_hours and re.search(r"\b(open|close[sd]?|closing|closure)\b", p.low):
        closed = _closure(place, p, view)
        if closed:
            return closed
    if asks_address and place:
        key = place.split()[0].lower()
        hit = next((a for a in view.addresses if key in a.lower()), None)
        if hit:
            contact = view.by_path["/contact"]
            return Result(f"The {place} branch is at {hit}.", [contact.url])
        return refuse()
    if not asks_hours or "branch" not in p.low and "lobby" not in p.low and "drive" not in p.low:
        return None
    drive = bool(re.search(r"drive", p.low))
    kind = "Drive-up" if drive else "Lobby"
    if place:
        words = [w.lower() for w in place.split() if len(w) > 3]
        hits = [s for s in view.schedules if words and all(w in s.where.lower() for w in words)]
        if not hits:
            return refuse()
        s = hits[0]
        return Result(f"{kind} hours at {place}: {_join_hours(s.drive_up if drive else s.lobby)}.", [s.url])
    pool = [s for s in view.schedules if (s.drive_up if drive else s.lobby)]
    if not pool:
        return refuse()
    if re.search(r"\b(same|all|every|differ|vary)\b", p.low):
        distinct, seen = [], set()
        for s in pool:
            lines = s.drive_up if drive else s.lobby
            if lines not in seen:
                seen.add(lines)
                distinct.append((lines, s))
        shown = _diverse(distinct, 4)
        body = " | ".join(_join_hours(lines) for lines, _ in shown)
        return Result(f"No, {kind.lower()} hours vary by branch. Schedules on file include: {body}. Which branch do you need?",
                      _hours_sources(view, [s.url for _, s in shown][:4]))
    s = pool[0]
    return Result(f"{kind} hours vary by branch. A typical schedule is {_join_hours(s.drive_up if drive else s.lobby)}. "
                  "Tell me which branch and I can check it.", _hours_sources(view, [u.url for u in pool[:3]]))


def _hours_sources(view: View, urls: list) -> list:
    """The locator and home page are where a member checks hours; the posts are where we read them."""
    return [BASE + "/contact", BASE + "/"] + urls if "/contact" in view.by_path else urls


def _diverse(distinct, limit):
    """Prefer schedules whose Saturday hours differ, so the reader sees the real range."""
    picked, saturdays = [], set()
    for lines, s in distinct:
        sat = next((l for l in lines if l.lower().startswith("sat")), "")
        if sat not in saturdays:
            saturdays.add(sat)
            picked.append((lines, s))
    for item in distinct:
        if item not in picked:
            picked.append(item)
    return picked[:limit]


# --- rates ----------------------------------------------------------------------------------------
def _in_range(row: Row, amount: float) -> bool:
    return (row.low is None or amount >= row.low) and (row.high is None or amount <= row.high)


def select_rows(p: Parsed, view: View) -> list[Row]:
    rows = [r for r in view.current if r.family == p.family and not r.intro or (r.family == p.family and p.intro)]
    if p.family != "card":
        rows = [r for r in rows if (r.audience == "business") == p.business]
    if p.intro:
        rows = [r for r in rows if r.intro] or rows

    if p.family == "savings":
        wanted = p.named_product or "Share Savings"
        rows = [r for r in rows if r.product == wanted]
    elif p.family == "money_market":
        # Achieve is a separate promotional account; only name it when asked, or when asked for the best.
        if "achieve" in p.low:
            rows = [r for r in rows if r.product == "Achieve Money Market"]
        elif not p.superlative:
            rows = [r for r in rows if r.product != "Achieve Money Market"]
    elif p.family == "checking":
        if "free" in p.low:
            rows = [r for r in rows if r.product == "Free Checking"]
        elif "1st" in p.low or "first rate" in p.low:
            rows = [r for r in rows if r.product == "1st Rate Checking"]
    elif p.family == "card":
        if p.card_name:
            rows = [r for r in rows if r.product == p.card_name]
        elif p.business:
            rows = [r for r in rows if r.audience == "business"]
    elif p.family == "ira":
        if "roth" in p.low:
            rows = [r for r in rows if r.ira == "roth"]
        elif "traditional" in p.low:
            rows = [r for r in rows if r.ira == "traditional"]
    elif p.family == "mortgage":
        wanted = "Mortgage Rate Relief" if p.relief else "Refinance" if p.refinance else "Purchase"
        rows = [r for r in rows if r.product == wanted]
    elif p.family == "auto":
        if p.model_year is not None:
            rows = [r for r in rows if r.year_low is not None and r.year_low <= p.model_year <= r.year_high]
        elif rows:
            newest = max(r.year_high or 0 for r in rows)
            rows = [r for r in rows if (r.year_high or 0) == newest]

    if p.family in ("certificate", "ira"):
        if p.jumbo:
            rows = [r for r in rows if r.jumbo]
        if p.special:
            rows = [r for r in rows if r.special]
        elif "regular" in p.low:
            rows = [r for r in rows if not r.special]

    if p.term_months is not None:
        rows = [r for r in rows if r.term_months == p.term_months]
    if p.amount is not None:
        ranged = [r for r in rows if r.low is not None or r.high is not None]
        if ranged:
            rows = [r for r in ranged if _in_range(r, p.amount)]
            if p.family == "mortgage" and rows:
                smallest = min(r.high or float("inf") for r in rows)
                rows = [r for r in rows if (r.high or float("inf")) == smallest]
    elif p.family == "mortgage" and p.term_months is None:
        # A general "what are your mortgage rates" leads with the entry tier of each term.
        by_term: dict[int, Row] = {}
        for r in sorted(rows, key=lambda r: r.high or float("inf")):
            by_term.setdefault(r.term_months, r)
        rows = list(by_term.values())
    return sorted(rows, key=_display_order)


def _display_order(r: Row):
    return (r.audience == "business", r.product, r.jumbo, -(r.term_months or 0), -(r.year_high or 0), r.high or float("inf"), -(r.low or 0))


def _number(value: str, top: bool) -> float:
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", value)]
    return (max(nums) if top else min(nums)) if nums else 0.0


def _values(r: Row) -> str:
    m = dict(r.metrics)
    if "apy" in m and "dividend" in m:
        return f"{m['apy']} APY ({m['dividend']} dividend rate)"
    if "apy" in m:
        return f"{m['apy']} APY"
    if "rate" in m and "apr" in m:
        return f"{m['rate']} interest rate / {m['apr']} APR"
    if "apr" in m:
        return f"{m['apr']} APR"
    return " / ".join(m.values())


def _name(r: Row) -> str:
    if r.family == "mortgage":
        return re.sub(r"\s+", " ", r.label.replace("Purchase: ", "").replace("Refinance: ", ""))
    if r.family == "card":
        return r.label if r.intro else r.product
    if r.family == "auto":
        return r.label
    if r.family in ("certificate",):
        return r.label
    if r.family == "ira":
        return f"{r.product} {r.label.split(':', 1)[-1].strip()}"
    if r.label and r.label != r.product and not r.label.startswith(r.product):
        return f"{r.product} ({r.label})"
    return r.label or r.product


def _sources(rows: list[Row], view: View) -> list[str]:
    urls: list[str] = []
    for r in rows[:6]:
        if r.url not in urls:
            urls.append(r.url)
    for r in rows[:6]:
        for other in view.rows:
            if other.authority >= PRODUCT and other.key == r.key and other.headline == r.headline and other.url not in urls:
                urls.append(other.url)
        path = PRODUCT_PAGES.get((r.family, r.audience))
        page = view.by_path.get(path) if path else None
        if page and page.url not in urls and re.search(re.escape(re.sub(r"[–-].*", "", r.headline)), page.markdown):
            urls.append(page.url)
    return urls[:6]


def _disagreement(rows: list[Row], view: View) -> Optional[str]:
    notes: list[str] = []
    for r in rows[:6]:
        stale = sorted({(o.headline, path_of(o.url)) for o in view.rows
                        if o.authority < r.authority and o.same_fact(r) and o.headline != r.headline})
        for value, path in stale[:2]:
            notes.append(f"{path} still shows {value}")
    if not notes:
        return None
    return "; ".join(notes[:4]) + f". Used {path_of(rows[0].url)}, the more authoritative page."


def _rates(p: Parsed, view: View) -> Optional[Result]:
    if not p.rate_intent:
        return None
    if p.unlisted or p.family is None:
        return refuse()
    rows = select_rows(p, view)
    if not rows:
        return refuse()

    if p.yes_no and p.asked_value is not None:
        best = max(rows, key=lambda r: _number(r.headline, True))
        if any(abs(_number(r.headline, False) - p.asked_value) < 1e-6 or abs(_number(r.headline, True) - p.asked_value) < 1e-6 for r in rows):
            match = next(r for r in rows if p.asked_value in (_number(r.headline, False), _number(r.headline, True)))
            return Result(f"Yes. {_name(match)} pays {_values(match)}.", _sources([match], view), rows=[match])
        return Result(f"No. {best.product} tops out at {_values(best)}"
                      f"{' for ' + best.label if best.label != best.product else ''}.", _sources([best], view), rows=[best])

    if p.superlative and len(rows) > 1:
        pick = (max if p.superlative == "max" else min)(rows, key=lambda r: _number(r.headline, p.superlative == "max"))
        rows = [pick]

    shown = rows[:MAX_ROWS]
    body = "; ".join(f"{_name(r)}: {_values(r)}" for r in shown)
    dates = sorted({r.effective for r in shown if r.effective})
    suffix = f" Rates effective as of {dates[-1]}." if dates else ""
    prefix = f"On the website as of {view.taken_on}: " if p.as_of else ""
    return Result(f"{prefix}{body}.{suffix}", _sources(shown, view), note=_disagreement(shown, view), rows=shown)


# --- everything else: quote the best passage --------------------------------------------------------
def _passages(p: Parsed, view: View) -> Optional[Result]:
    query = expand(p.text)
    hits = view.index.search(query)
    good = [(s, cov, c) for s, cov, c in hits if cov >= MIN_COVERAGE or query != p.text]
    if not good:
        return refuse()
    chunk, text = best_passage(view.index, good, query)
    if not chunk or not text:
        return refuse()
    # "Do you offer X?" needs a page where Dupaco describes X, not a blog post that mentions it in passing.
    if re.match(r"\s*(do|does) (you|dupaco|we)\s+(offer|have|provide)", p.low) and chunk.trust <= EDITORIAL:
        return refuse()
    if "fee" in p.low and "fee" not in text.lower():
        return refuse()
    return Result(text, [chunk.url])
