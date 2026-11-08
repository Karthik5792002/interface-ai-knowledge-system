"""Turn the markdown rate tables on a page into typed, comparable rows.

The site's tables changed layout between crawls (May rows carry only a balance tier and leave the
product in a heading above the table; September rows name the product), so a row is identified by
what it *means* -- product, term, tier, audience -- never by its position or its literal label.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Optional

PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
PCT_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(?:-|–|—|to)\s*(\d+(?:\.\d+)?)\s*%")
EFFECTIVE = re.compile(r"effective as of\s+(\d{1,2})/(\d{1,2})/(\d{4})", re.I)
SEPARATOR = re.compile(r"^\|\s*:?-{3}")
TERM_MONTHS = re.compile(r"(\d+)\s*-?\s*(?:months?|mos?\b)", re.I)
TERM_YEARS = re.compile(r"(\d+)\s*-?\s*years?\b", re.I)
MODEL_YEAR = re.compile(r"(\d{4})\s*[-–—]\s*(\d{2,4})\s*model year|model year\s*(\d{4})\s*[-–—]\s*(\d{2,4})", re.I)
OLDER_MODEL = re.compile(r"(\d{4})\s*model year\s*\(and older\)", re.I)
MONEY = r"\$?\s*(\d[\d,]*(?:\.\d+)?)"
AMOUNT_RANGE = re.compile(MONEY + r"\s*(?:-|–|—|to)\s*" + MONEY)
AMOUNT_OR_LESS = re.compile(MONEY + r"\s*or less", re.I)
AMOUNT_PLUS = re.compile(MONEY + r"\s*\+")

# (pattern, family, display name). Checked against the row first, then the nearest heading.
PRODUCTS = [
    (r"share savings", "savings", "Share Savings"),
    (r"bright savings", "savings", "Bright Savings"),
    (r"you.?name.?it", "savings", "You Name It Savings"),
    (r"holiday club", "savings", "Holiday Club Savings"),
    (r"launchpad", "savings", "Launchpad Savings"),
    (r"achieve", "money_market", "Achieve Money Market"),
    (r"investor.?s choice|money market", "money_market", "Investor's Choice Money Market"),
    (r"high.?yield", "high_yield", "High-Yield Savings"),
    (r"health savings|\bhsa\b", "hsa", "Health Savings Account"),
    (r"free (business )?checking", "checking", "Free Checking"),
    (r"1st rate", "checking", "1st Rate Checking"),
    (r"business platinum", "card", "Business Platinum Visa"),
    (r"business rewards", "card", "Business Rewards Visa"),
    (r"platinum", "card", "Platinum Visa"),
    (r"rewards visa", "card", "Rewards Visa"),
    (r"home equity refinance", "home_equity_refi", "Home Equity Refinance"),
    (r"heloc|line of credit", "heloc", "HELOC"),
    (r"home equity", "home_equity_loan", "Home Equity Loan"),
    (r"home improvement", "home_improvement", "Home Improvement Loan"),
    (r"boat|\brv\b|recreational", "recreational", "Boat & RV Loan"),
    (r"motorcycle|auto", "auto", "Auto & Motorcycle Loan"),
    (r"personal loan", "personal_loan", "Personal Loan"),
    (r"certificate", "certificate", "Certificate"),
]
PRODUCTS = [(re.compile(p, re.I), fam, name) for p, fam, name in PRODUCTS]


@dataclass(frozen=True)
class Row:
    family: str
    product: str
    label: str                       # readable tier/term description for the answer
    audience: str = "personal"
    ira: str = ""                    # "", "roth", "traditional"
    special: bool = False
    jumbo: bool = False
    intro: bool = False
    term_months: Optional[int] = None
    low: Optional[float] = None      # balance / loan amount bounds
    high: Optional[float] = None
    year_low: Optional[int] = None   # vehicle model years
    year_high: Optional[int] = None
    metrics: tuple = ()              # (("apy", "4.10%"), ("dividend", "4.06%"))
    effective: Optional[str] = None
    url: str = ""
    snapshot_id: str = ""
    taken_on: str = ""
    authority: int = 0

    def metric(self, name: str) -> Optional[str]:
        return dict(self.metrics).get(name)

    @property
    def headline(self) -> str:
        """The number a member would quote: APY for deposits, APR for loans."""
        m = dict(self.metrics)
        return m.get("apy") or m.get("apr") or m.get("rate") or m.get("dividend") or ""

    @property
    def key(self) -> tuple:
        """Identity of the fact, independent of page, crawl and label wording."""
        return (self.family, self.product, self.audience, self.ira, self.special, self.jumbo, self.intro,
                self.term_months, self.low, self.high, self.year_low, self.year_high)

    def same_fact(self, other: "Row") -> bool:
        """Same fact even if the model-year band was relabelled ('2023-26' became '2024-27')."""
        if self.key[:-2] != other.key[:-2]:
            return False
        if self.year_low is None or other.year_low is None:
            return self.year_low == other.year_low
        return self.year_low <= other.year_high and other.year_low <= self.year_high


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def _clean_cell(c: str) -> str:
    c = re.sub(r"\\([*_$#|])", r"\1", c)
    c = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", c)
    return c.replace("*", "").strip()


def _metric_kind(header: str) -> Optional[str]:
    h = header.lower()
    if "point" in h or "payment" in h or "min" in h:
        return None
    if "dividend" in h:
        return "dividend"
    if "yield" in h or "apy" in h:
        return "apy"
    if "apr" in h or "annual percentage rate" in h:
        return "apr"
    if "rate" in h and "%" not in h:
        return "rate"
    return None


def _value(cell: str) -> Optional[str]:
    """'12.50% – 19.50%2' -> '12.50%–19.50%'; '4.20%1' -> '4.20%'. Footnote digits are dropped."""
    if "%" not in cell:
        return None
    m = PCT_RANGE.search(cell)
    if m and float(m.group(2)) >= float(m.group(1)) and m.start() < 3:
        return f"{m.group(1)}%–{m.group(2)}%"
    m = PCT.search(cell)
    return f"{m.group(1)}%" if m else None


def iter_tables(md: str) -> Iterator[tuple[list[str], list[str], list[list[str]], str]]:
    """Yield (preceding short lines, header cells, data rows, text after the table)."""
    lines = md.splitlines()
    context: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|") and i + 1 < len(lines) and SEPARATOR.match(lines[i + 1].strip()):
            header = [_clean_cell(c) for c in line.strip("|").split("|")]
            j, rows = i + 2, []
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([_clean_cell(c) for c in lines[j].strip().strip("|").split("|")])
                j += 1
            yield list(context[-5:]), header, rows, " ".join(lines[j:j + 8])[:1500]
            i = j
            continue
        if line and not line.startswith(("[", "!", "|", "*Rates", "**Rates", "Rates are")) and len(line) <= 60 \
                and "effective" not in line.lower():
            context.append(line.lstrip("#* ").rstrip("* "))
        i += 1


def _classify(desc: str, context: list[str], header: list[str]) -> Optional[tuple[str, str]]:
    for rx, fam, name in PRODUCTS:
        if rx.search(desc):
            return fam, name
    for line in reversed(context):
        for rx, fam, name in PRODUCTS:
            if rx.search(line):
                return fam, name
    return None


def _in_special_section(context: list[str]) -> bool:
    """The nearest 'Special ...' or 'Regular ...' heading above the table decides."""
    for line in reversed(context):
        low = line.lower()
        if len(low) < 34 and low.startswith("special"):
            return True
        if len(low) < 34 and low.startswith("regular"):
            return False
    return False


def parse_rows(md: str, url: str, snapshot_id: str, taken_on: str, authority: int) -> list[Row]:
    out: list[Row] = []
    seen: set = set()
    for context, header, rows, after in iter_tables(md):
        kinds = [_metric_kind(h) for h in header]
        eff = EFFECTIVE.search(after)
        effective = f"{eff.group(3)}-{int(eff.group(1)):02d}-{int(eff.group(2)):02d}" if eff else None
        for cells in rows:
            if len(cells) != len(header):
                continue
            metrics: list[tuple[str, str]] = []
            desc_parts: list[str] = []
            for h, kind, cell in zip(header, kinds, cells):
                value = _value(cell) if cell else None
                if value and kind is None and "%" in cell and "min" not in h.lower():
                    kind = "apr" if "apr" in cell.lower() else None
                if value and "apr" in cell.lower():
                    kind = "apr"
                if kind and value:
                    metrics.append((kind, value))
                elif not kind and "point" not in h.lower() and "payment" not in h.lower() \
                        and "min" not in h.lower() and "%" not in cell:
                    desc_parts.append(cell)
            if not metrics:
                continue
            desc = " ".join(p for p in desc_parts if p and p != "—")
            row = _build(desc, context, header, metrics, effective, url, snapshot_id, taken_on, authority)
            # A later table with the same identity is the Home Equity Refinance copy of the mortgage
            # table (or a repeated widget); the first table on the page is the primary one.
            if row and row.key not in seen:
                seen.add(row.key)
                out.append(row)
    return out


def _build(desc, context, header, metrics, effective, url, snapshot_id, taken_on, authority) -> Optional[Row]:
    low_url = url.lower()
    ctx_text = " ".join(context).lower()
    d = desc.lower()
    business = "business" in d or "/business" in low_url or "business-rates" in low_url
    ira = "roth" if d.startswith("roth") else "traditional" if d.startswith("traditional") else ""
    has_rate_kinds = {k for k, _ in metrics}

    term = None
    m = TERM_MONTHS.search(desc)
    if m:
        term = int(m.group(1))
    else:
        m = TERM_YEARS.search(desc)
        if m:
            term = int(m.group(1)) * 12

    y_low = y_high = None
    m = MODEL_YEAR.search(desc)
    if m:
        a, b = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        a, b = int(a), int(b)
        if b < 100:
            b += (a // 100) * 100
        y_low, y_high = min(a, b), max(a, b)
    else:
        m = OLDER_MODEL.search(desc)
        if m:
            y_low, y_high = 0, int(m.group(1))

    low = high = None
    # Only look for money outside the model-year text so "2024-27" is never read as an amount.
    money_text = re.sub(r"\d{4}\s*[-–—]\s*\d{2,4}", " ", desc)
    money_text = TERM_MONTHS.sub(" ", money_text)
    m = AMOUNT_RANGE.search(money_text)
    if m and "$" in money_text:
        low, high = _money(m.group(1)), _money(m.group(2))
    else:
        m = AMOUNT_OR_LESS.search(money_text)
        if m:
            high = _money(m.group(1))
        else:
            m = AMOUNT_PLUS.search(money_text)
            if m:
                low = _money(m.group(1))

    mortgage = re.match(r"(purchase|refinance):", d)
    if mortgage or "mortgage rate relief" in d:
        family, product = "mortgage", "Mortgage Rate Relief" if not mortgage else mortgage.group(1).title()
    elif ira or ("ira" in ctx_text and ("roth" in d or "traditional" in d)):
        family, product = "ira", (ira or "ira").title() + " IRA"
    elif y_low is not None:
        family, product = "auto", "Auto & Motorcycle Loan"
        if d.startswith("model year"):
            family, product = "powersport", "Motorcycle/ATV Loan"
    elif term and "dividend" in has_rate_kinds and not any(rx.search(desc) for rx, _, _ in PRODUCTS):
        # A bare "60 Month" row with a dividend rate is a certificate, whatever heading sits above it.
        family, product = "certificate", "Certificate"
    else:
        found = _classify(desc, context, header)
        if not found:
            if term and "apr" in has_rate_kinds:
                family, product = "personal_loan", "Personal Loan"
            elif term and "dividend" in has_rate_kinds:
                family, product = "certificate", "Certificate"
            else:
                return None
        else:
            family, product = found
    if family == "certificate" and term is None:
        return None

    special = "special" in d or (family in ("certificate", "ira") and _in_special_section(context))
    jumbo = "jumbo" in d
    intro = "intro" in d
    if family == "card" and product.startswith("Business"):
        business = True
    if family == "certificate" or family == "ira":
        name = f"{term} Month" if term else desc
        product_label = name + (" Fixed Jumbo Certificate Special" if special and jumbo
                                else " Fixed Certificate Special" if special else " Regular Certificate")
        label = desc if ira else product_label
    else:
        label = desc or product
    return Row(family=family, product=product, label=label, audience="business" if business else "personal",
               ira=ira, special=special, jumbo=jumbo, intro=intro, term_months=term, low=low, high=high,
               year_low=y_low, year_high=y_high, metrics=tuple(metrics), effective=effective, url=url,
               snapshot_id=snapshot_id, taken_on=taken_on, authority=authority)
