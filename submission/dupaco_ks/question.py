"""Read a member's question into the qualifiers that decide which fact it is asking about."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}
_LAST_DAY = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}

NUMBER_WORDS = {"three": 3, "five": 5, "six": 6, "ten": 10, "twelve": 12, "fifteen": 15, "seventeen": 17,
                "eighteen": 18, "twenty": 20, "twenty-four": 24, "thirty": 30, "thirty-six": 36, "forty-eight": 48,
                "sixty": 60, "seventy-two": 72, "eighty-four": 84}
NUMBER_WORD = re.compile(r"\b(" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True)) + r")[- ](month|year)\b")
DATE_PHRASE = re.compile(rf"\b(?:as of|in|on|during|back in)\s+({MONTHS})(?:\s+(\d{{1,2}})(?:st|nd|rd|th)?,?)?\s+(20\d\d)\b", re.I)
ANY_DATE = re.compile(rf"\b({MONTHS})(?:\s+\d{{1,2}},?)?\s+20\d\d\b", re.I)
STRONG_RATE = re.compile(r"\b(rates?|aprs?|apys?|interest|dividends?|yield|percent(age)?)\b|%", re.I)
WEAK_RATE = re.compile(r"\b(pays?|earns?)\b", re.I)
DEPOSITS = {"savings", "checking", "certificate", "ira", "hsa", "money_market", "high_yield"}
UNLISTED = re.compile(
    r"(\b\d+/\d+\b|\b(arms?|adjustable|fha|va loans?|usda|construction|land loans?|lot loans?|bridge loans?|balloon|"
    r"interest.only|student loans?|sba|payday|commercial real estate|equipment loans?|business loans?|"
    r"jumbo mortgages?|jumbo home loans?|reverse mortgages?)\b)",
    re.I,
)

FAMILIES = [
    (r"\bira\b|\biras\b|\broth\b|retirement (account|certificate)", "ira"),
    (r"certificates?\b|\bcds?\b", "certificate"),
    (r"health savings|\bhsa\b", "hsa"),
    (r"money market|investor.?s choice|\bachieve\b", "money_market"),
    (r"high.?yield", "high_yield"),
    (r"bright savings|launchpad|holiday club|you.?name.?it|share savings", "savings"),
    (r"checking", "checking"),
    (r"heloc|line of credit", "heloc"),
    (r"home equity", "home_equity_loan"),
    (r"home improvement", "home_improvement"),
    (r"personal loan|signature loan", "personal_loan"),
    (r"\bboats?\b|\brvs?\b|camper|recreational", "recreational"),
    (r"\bauto\b|\bcars?\b|vehicle|\btruck\b|\bsuv\b|motorcycle", "auto"),
    (r"credit cards?|\bvisa\b", "card"),
    (r"mortgage|home loan|refinanc|\d+[- ]year fixed|\bhome purchase\b", "mortgage"),
    (r"\bsavings?\b", "savings"),
]
FAMILIES = [(re.compile(p, re.I), f) for p, f in FAMILIES]
NAMED_SAVINGS = [
    (r"bright", "Bright Savings"), (r"launchpad", "Launchpad Savings"), (r"holiday", "Holiday Club Savings"),
    (r"you.?name.?it", "You Name It Savings"),
]


@dataclass
class Parsed:
    text: str
    low: str
    family: Optional[str]
    rate_intent: bool
    unlisted: bool
    term_months: Optional[int]
    amount: Optional[float]
    model_year: Optional[int]
    business: bool
    jumbo: bool
    special: bool
    intro: bool
    refinance: bool
    relief: bool
    superlative: Optional[str]
    asked_value: Optional[float]
    named_product: Optional[str]
    card_name: Optional[str]
    as_of: Optional[str]
    yes_no: bool


def date_from_text(q: str) -> Optional[str]:
    m = DATE_PHRASE.search(q)
    if not m:
        return None
    month = MONTH_NUM[m.group(1).lower()]
    day = int(m.group(2)) if m.group(2) else _LAST_DAY[month]
    return f"{m.group(3)}-{month:02d}-{day:02d}"


def parse(question: str, as_of: Optional[str]) -> Parsed:
    low = question.lower().replace("–", "-").replace("—", "-")
    low = NUMBER_WORD.sub(lambda m: f"{NUMBER_WORDS[m.group(1)]}-{m.group(2)}", low)  # "six month" -> "6-month"
    plain = ANY_DATE.sub(" ", low)  # dates must not be read as model years or amounts

    family = next((f for rx, f in FAMILIES if rx.search(plain)), None)
    term = None
    m = re.search(r"(\d+)\s*-?\s*(?:months?|mos?\b)", plain)
    if m:
        term = int(m.group(1))
    else:
        m = re.search(r"(\d+)\s*-?\s*(?:years?|yrs?)\b", plain)
        if m:
            term = int(m.group(1)) * 12

    model_year = None
    if family == "auto":
        m = re.search(r"\b(19[89]\d|20[0-3]\d)\b", plain)
        if m:
            model_year = int(m.group(1))

    amounts = [float(a.replace(",", "")) for a in re.findall(r"\$\s*(\d[\d,]*(?:\.\d+)?)", plain)]
    if not amounts:
        amounts = [float(a.replace(",", "")) for a in re.findall(r"\b(\d{1,3}(?:,\d{3})+)\b", plain)]
    k = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*k\b", plain)
    if not amounts and k:
        amounts = [float(k.group(1)) * 1000]

    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", plain)
    sup = "max" if re.search(r"\b(highest|best|most|top|maximum)\b", plain) else \
        "min" if re.search(r"\b(lowest|cheapest|smallest|minimum rate)\b", plain) else None
    named = next((n for rx, n in NAMED_SAVINGS if re.search(rx, plain)), None)
    if "share savings" in plain:
        named = "Share Savings"
    card = "Business Platinum Visa" if "business platinum" in plain else \
        "Business Rewards Visa" if "business rewards" in plain else \
        "Platinum Visa" if "platinum" in plain else "Rewards Visa" if "rewards" in plain else None
    # "How much is a 72-month car loan?" never says "rate", but a product plus a term can only mean one.
    rate_intent = bool(STRONG_RATE.search(plain)) or (family in DEPOSITS and bool(WEAK_RATE.search(plain))) \
        or (family is not None and term is not None)

    return Parsed(
        text=question, low=plain, family=family, rate_intent=rate_intent,
        unlisted=bool(UNLISTED.search(plain)), term_months=term, amount=amounts[0] if amounts else None,
        model_year=model_year, business="business" in plain, jumbo="jumbo" in plain, special="special" in plain,
        intro=bool(re.search(r"\bintro(ductory)?\b|balance.transfer", plain)),
        refinance="refinanc" in plain or bool(re.search(r"\brefi\b", plain)),
        relief="relief" in plain, superlative=sup, asked_value=float(pct.group(1)) if pct else None,
        named_product=named, card_name=card, as_of=as_of or date_from_text(question),
        yes_no=bool(re.match(r"\s*(do|does|is|are|can|did)\b", plain)),
    )
