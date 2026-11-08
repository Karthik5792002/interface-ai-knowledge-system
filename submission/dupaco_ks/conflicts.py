"""Find places the website contradicts itself, across pages and across crawls.

Tiers and qualifiers are part of a fact's identity (see Row.key), so a balance-tier table never
shows up here as a false alarm. One conflict is reported per fact, however many pages repeat it.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

from .pages import CAMPAIGN, OFFICIAL, path_of
from .tables import Row


@dataclass
class Finding:
    topic: str
    values: list
    winner: str
    reason: str
    evidence: list          # (url, snapshot_id, value)
    kind: str               # stale_page | changed_over_time | same_page | figures


def describe(r: Row) -> str:
    audience = "business " if r.audience == "business" else ""
    term = f"{r.term_months}-month " if r.term_months else ""
    if r.family == "mortgage" and r.term_months:
        term = f"{r.term_months // 12}-year "
    jumbo = "jumbo " if r.jumbo else ""
    special = "special " if r.special else ""
    years = ""
    if r.year_low is not None:
        years = f" ({r.year_high} and older model year)" if r.year_low == 0 else f" ({r.year_low}-{r.year_high} model year)"
    tier = ""
    if r.high is not None or r.low is not None:
        tier = f" [{'' if r.low is None else f'${r.low:,.0f}'}{' to ' if r.low is not None and r.high is not None else ''}" \
               f"{'' if r.high is None else f'${r.high:,.0f}'}{'+' if r.high is None else ''}]"
    metric = "APY" if r.metric("apy") else "APR"
    product = {"certificate": "certificate", "auto": "auto loan", "mortgage": f"{r.product.lower()} mortgage"}.get(
        r.family, r.product)
    if r.family == "ira":
        product = r.product
    return f"{audience}{term}{jumbo}{special}{product}{years}{tier} {metric}".replace("  ", " ")


def _overlap(a: Row, b: Row) -> int:
    if a.year_low is None or b.year_low is None:
        return 0
    return max(0, min(a.year_high, b.year_high) - max(a.year_low, b.year_low) + 1)


def _winner(r: Row) -> str:
    """Name the rate and the APR together, so a reader can match either figure a page printed."""
    rate = r.metric("rate")
    return f"{rate} rate / {r.headline} APR" if rate and r.family == "mortgage" else r.headline


def _stale_rows(r: Row, view) -> list[Row]:
    """Lower-trust rows stating this fact differently; a relabelled model-year band matches one band only."""
    out = []
    for o in view.rows:
        if o.authority >= r.authority or not o.same_fact(r) or not o.headline or o.headline == r.headline:
            continue
        if o.year_low is not None:
            rivals = [c for c in view.current if c.same_fact(o) and c.authority > o.authority]
            if rivals and _overlap(o, r) < max(_overlap(o, c) for c in rivals):
                continue
        out.append(o)
    return out


def find_conflicts(views) -> list[Finding]:
    latest = views[-1]
    findings: "OrderedDict[tuple, Finding]" = OrderedDict()

    # 1. Pages that disagree with the most trusted page in the newest crawl.
    for r in sorted(latest.current, key=lambda r: str(r.key)):
        stale = _stale_rows(r, latest)
        if stale:
            values = sorted({o.headline for o in stale})
            if r.metric("rate") and r.family == "mortgage":
                values += sorted({o.metric("rate") for o in stale if o.metric("rate") and o.metric("rate") != r.metric("rate")})
                values.append(r.metric("rate"))
            paths = sorted({path_of(o.url) for o in stale})
            findings[r.key] = Finding(
                describe(r), values + [r.headline], _winner(r),
                f"{path_of(r.url)} is the most authoritative page for this product; {', '.join(paths[:4])} still show the older value.",
                [(o.url, o.snapshot_id, o.headline) for o in stale] + [(r.url, r.snapshot_id, r.headline)], "stale_page")

    # 2. The official value changed between crawls.
    for r in sorted(latest.current, key=lambda r: str(r.key)):
        if r.authority != OFFICIAL:
            continue
        # Business and IRA certificates mirror the personal table; one entry per real change is enough.
        if r.key not in findings and (r.audience == "business" or r.family == "ira"):
            continue
        history: "OrderedDict[str, Row]" = OrderedDict()
        for view in views[:-1]:
            old = next((o for o in view.current if o.authority == OFFICIAL and o.same_fact(r)), None)
            if old and old.headline and old.headline != r.headline:
                history.setdefault(old.headline, old)
        if not history:
            continue
        evidence = [(o.url, o.snapshot_id, v) for v, o in history.items()] + [(r.url, r.snapshot_id, r.headline)]
        if r.key in findings:
            f = findings[r.key]
            f.values = list(dict.fromkeys(list(history) + f.values))
            f.evidence.extend(evidence)
            f.reason += f" The official value also changed over time, most recently to {r.headline} in the {latest.taken_on} crawl."
        else:
            findings[r.key] = Finding(describe(r), list(history) + [r.headline], r.headline,
                                      f"The official rate page changed; {latest.taken_on} is the newest crawl.", evidence,
                                      "changed_over_time")

    out = list(findings.values())
    out.extend(_ira_asymmetry(latest))
    out.extend(_figures(latest))
    already = {v for f in out if "checking" in f.topic.lower() for v in f.values}
    out.extend(c for c in _promo_claims(latest) if not set(c.values) <= already)
    return out


def _ira_asymmetry(view) -> list[Finding]:
    """Roth and Traditional versions of one IRA certificate should match; a mismatch is likely a typo."""
    out = []
    rows = [r for r in view.current if r.family == "ira" and r.ira == "roth"]
    for roth in sorted(rows, key=lambda r: str(r.key)):
        twin = next((o for o in view.current if o.family == "ira" and o.ira == "traditional" and o.special == roth.special
                     and o.jumbo == roth.jumbo and o.term_months == roth.term_months and o.low == roth.low and o.high == roth.high), None)
        if not twin or twin.headline == roth.headline:
            continue
        certificate = next((c for c in view.current if c.family == "certificate" and c.audience == "personal"
                            and c.special == roth.special and c.jumbo == roth.jumbo and c.term_months == roth.term_months), None)
        winner = certificate.headline if certificate else roth.headline
        out.append(Finding(
            f"{roth.term_months}-month {'jumbo ' if roth.jumbo else ''}special IRA APY (Roth vs Traditional)",
            sorted({roth.headline, twin.headline}), winner,
            f"The same page lists different values for Roth and Traditional; the matching certificate pays {winner}.",
            [(roth.url, roth.snapshot_id, roth.headline), (twin.url, twin.snapshot_id, twin.headline)], "same_page"))
    return out


def _figures(view) -> list[Finding]:
    facts = view.org.get("branches", [])
    values = list(dict.fromkeys(f.value for f in facts))
    if len(values) < 2:
        return []
    newest = facts[0]
    return [Finding("number of branch office locations", sorted(values), newest.value,
                    f"Boilerplate differs by post; the most recently published ({newest.published}) says {newest.value}.",
                    [(f.url, view.id, f.value) for f in facts[:8]], "figures")]


def _promo_claims(view) -> list[Finding]:
    """Marketing copy quoting an APY for checking that the current rate table does not offer."""
    official = {r.headline for r in view.current if r.family == "checking"}
    official_top = max((r for r in view.current if r.family == "checking" and r.audience == "personal"),
                       key=lambda r: float(r.headline.rstrip("%") or 0), default=None)
    if official_top is None:
        return []
    claims = []
    for url, page in sorted(view.pages.items()):
        from .pages import authority
        if authority(url) < CAMPAIGN or authority(url) == OFFICIAL:
            continue
        for m in re.finditer(r"checking[^.|]{0,120}?up to\s+\**(\d+\.\d+)%\s*APY", page.markdown, re.I):
            value = f"{m.group(1)}%"
            if value not in official:
                claims.append((page.url, view.id, value))
    if not claims:
        return []
    values = sorted({c[2] for c in claims})
    return [Finding("checking account APY", values + [official_top.headline], official_top.headline,
                    f"Campaign copy advertises a rate the current checking rate table no longer shows ({official_top.headline}).",
                    claims + [(official_top.url, official_top.snapshot_id, official_top.headline)], "stale_page")]
