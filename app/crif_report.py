"""Turn a CRIF High Mark individual report (the bureau API response) into plain facts.

Only derived, non-identifying facts leave this module: the customer's first name, counts and
amounts, account types and lender categories. PAN, phone numbers, addresses, emails, dates of
birth and account numbers are read only to be counted, never copied.
"""
import re
from collections import Counter
from datetime import date
from typing import Any, Dict, List, Optional

LENDER_TYPES = {
    "NAB": "Public sector bank", "PRB": "Private bank", "FRB": "Foreign bank", "NBF": "NBFC",
    "SFB": "Small finance bank", "COP": "Co-operative bank", "RRB": "Regional rural bank",
    "MFI": "Microfinance lender", "HFC": "Housing finance company",
}
SERIOUS_ASSET_CLASSES = {"SMA": 1, "SUB": 2, "DBT": 3, "LSS": 4}
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


class CrifFormatError(ValueError):
    pass


def find_report(payload: Any) -> Dict[str, Any]:
    """Accept the full API response, its `data`, `crifReport`, or the INDV-REPORT itself."""
    node = payload
    for key in ("data", "crifReport", "INDV-REPORT-FILE"):
        if isinstance(node, dict) and key in node:
            node = node[key]
    if isinstance(node, dict) and "INDV-REPORTS" in node:
        reports = node["INDV-REPORTS"] or []
        node = reports[0] if reports else None
    if isinstance(node, dict) and "INDV-REPORT" in node:
        node = node["INDV-REPORT"]
    if not isinstance(node, dict) or "SCORES" not in node or "RESPONSES" not in node:
        raise CrifFormatError("This doesn't look like a CRIF High Mark report: no INDV-REPORT with SCORES and RESPONSES.")
    return node


def amount(value) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^\d-]", "", str(value or ""))
    try:
        return int(digits) if digits not in ("", "-") else 0
    except ValueError:
        return 0


def parse_date(value) -> Optional[date]:
    m = re.match(r"^(\d{2})-(\d{2})-(\d{4})$", str(value or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def first_name(variations: List[dict]) -> str:
    """First word of the most recently reported clean name, e.g. 'WASEEM AKRAM' -> 'Waseem'."""
    clean = [(parse_date(v.get("REPORTED-DATE")) or date.min, i, str(v.get("VALUE", "")).strip())
             for i, v in enumerate(variations or [])
             if re.fullmatch(r"[A-Za-z][A-Za-z .]*", str(v.get("VALUE", "")).strip())]
    if not clean:
        return ""
    latest = max(d for d, _, _ in clean)
    name = next(n for d, _, n in sorted(clean, key=lambda x: x[1]) if d == latest)
    return name.split()[0].title()


def parse_history(raw: str) -> Dict[str, Dict[str, Any]]:
    """'Aug:2026,000/STD|Jul:2026,022/STD|...' -> {'2026-08': {'dpd': 0, 'asset': 'STD'}, ...}"""
    months = {}
    for part in str(raw or "").split("|"):
        m = re.match(r"^([A-Za-z]{3}):(\d{4}),([^/]*)/(.*)$", part.strip())
        if not m or m.group(1).title() not in MONTHS:
            continue
        key = f"{m.group(2)}-{MONTHS[m.group(1).title()]:02d}"
        dpd = int(m.group(3)) if m.group(3).isdigit() else None
        asset = m.group(4).strip().upper()
        months[key] = {"dpd": dpd, "asset": asset if asset in ("STD", *SERIOUS_ASSET_CLASSES) else None}
    return months


def month_key(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def months_back(end: date, count: int) -> List[str]:
    """Month keys for the `count` months ending with `end`'s month, oldest first."""
    keys, y, m = [], end.year, end.month
    for _ in range(count):
        keys.append(f"{y}-{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return keys[::-1]


def is_card(acct_type: str) -> bool:
    t = acct_type.lower()
    return "credit card" in t and not t.startswith("loan")


def parse_report(payload: Any) -> Dict[str, Any]:
    r = find_report(payload)
    info = r.get("PERSONAL-INFO-VARIATION") or {}
    summary = r.get("ACCOUNTS-SUMMARY") or {}
    derived = summary.get("DERIVED-ATTRIBUTES") or {}
    primary = summary.get("PRIMARY-ACCOUNTS-SUMMARY") or {}
    score = (r.get("SCORES") or [{}])[0]

    accounts = []
    for item in r.get("RESPONSES") or []:
        L = item.get("LOAN-DETAILS") or {}
        acct_type = (L.get("ACCT-TYPE") or "Other").strip() or "Other"
        code = (L.get("CREDIT-GUARANTOR") or "").strip()
        security = (L.get("SECURITY-STATUS") or "").strip().lower()
        suit = (L.get("SUIT-FILED_WILFUL-DEFAULT") or "").strip()
        accounts.append({
            "type": acct_type,
            "is_card": is_card(acct_type),
            "lender_code": code,
            "lender": LENDER_TYPES.get(code, "Lender"),
            "active": (L.get("ACCOUNT-STATUS") or "").strip().lower() == "active",
            "joint": (L.get("OWNERSHIP-IND") or "").strip().lower() == "joint",
            "secured": True if security == "secured" else False if security.startswith("un") else None,
            "opened": parse_date(L.get("DISBURSED-DATE")),
            "closed": parse_date(L.get("CLOSED-DATE")),
            "reported": parse_date(L.get("DATE-REPORTED")),
            "disbursed": amount(L.get("DISBURSED-AMT")),
            "balance": max(0, amount(L.get("CURRENT-BAL"))),
            "overdue": max(0, amount(L.get("OVERDUE-AMT"))),
            "limit": amount(L.get("CREDIT-LIMIT")),
            "cash_limit": amount(L.get("CASH-LIMIT")),
            "installment": amount(L.get("INSTALLMENT-AMT")),
            "interest_rate": float(L["INTEREST-RATE"]) if re.fullmatch(r"\d+(\.\d+)?", str(L.get("INTEREST-RATE", "")).strip()) and float(L["INTEREST-RATE"]) > 0 else None,
            "term_months": amount(L.get("ORIGINAL-TERM")),
            "write_off": amount(L.get("WRITE-OFF-AMT")) + amount(L.get("PRINCIPAL-WRITE-OFF-AMT")),
            "settlement": amount(L.get("SETTLEMENT-AMT")),
            "written_off_settled_status": (L.get("WRITTEN-OFF_SETTLED-STATUS") or "").strip(),
            "suit_or_wilful": bool(suit) and suit.lower() != "no suit filed",
            "disputed": (L.get("ACCT-IN-DISPUTE") or "").strip().upper() in ("Y", "YES", "TRUE"),
            "collateral": sorted({s.get("SECURITY-TYPE") for s in (L.get("SECURITY-DETAILS") or [])
                                  if s.get("SECURITY-TYPE") and s.get("SECURITY-TYPE") != "No Collateral"}),
            "history": parse_history(L.get("COMBINED-PAYMENT-HISTORY")),
        })

    reported = [a["reported"] for a in accounts if a["reported"]]
    as_of = max(reported) if reported else date.today()
    window36 = set(months_back(as_of, 36))
    window12 = set(months_back(as_of, 12))

    # Payment behaviour over the last 36 months across every account, plus anything older.
    known = on_time = 0
    late, late_older = [], []
    for i, a in enumerate(accounts):
        for key, h in a["history"].items():
            if h["dpd"] is None:
                continue
            if key not in window36:
                if h["dpd"] > 0:
                    late_older.append({"account": i, "month": key, "dpd": h["dpd"], "asset": h["asset"]})
                continue
            known += 1
            if h["dpd"] == 0:
                on_time += 1
            else:
                late.append({"account": i, "month": key, "dpd": h["dpd"], "asset": h["asset"]})
    late.sort(key=lambda x: x["month"], reverse=True)

    active = [a for a in accounts if a["active"]]
    cards = [a for a in active if a["is_card"]]
    card_limit = sum(a["limit"] for a in cards)
    card_used = sum(a["balance"] for a in cards)
    enquiries = sorted(
        [{"date": parse_date(q.get("INQUIRY-DATE")), "purpose": (q.get("PURPOSE") or "Other").strip().title() if (q.get("PURPOSE") or "").isupper() else (q.get("PURPOSE") or "Other").strip(),
          "lender": LENDER_TYPES.get((q.get("MEMBER-NAME") or "").strip(), "Lender"), "amount": amount(q.get("AMOUNT"))}
         for q in r.get("INQUIRY-HISTORY") or []],
        key=lambda q: q["date"] or date.min, reverse=True)

    def count(key):
        return len(info.get(key) or [])

    opened = [a for a in accounts if a["opened"]]
    oldest = min(opened, key=lambda a: a["opened"]) if opened else None
    alert = next((x.get("ALERT-DESC") for x in r.get("ALERTS") or [] if (x.get("ALERT-DESC") or "NO").upper() != "NO"), None)

    return {
        "name": first_name(info.get("NAME-VARIATIONS")),
        "bureau": "CRIF High Mark",
        "score": amount(score.get("SCORE-VALUE")),
        "score_model": (score.get("SCORE-TYPE") or "").strip(),
        "score_factor_codes": [c for c in str(score.get("SCORE-FACTORS") or "").split("|") if c],
        "as_of": as_of,
        "alert": alert,
        "identity": {"names": count("NAME-VARIATIONS"), "addresses": count("ADDRESS-VARIATIONS"),
                     "phones": count("PHONE-NUMBER-VARIATIONS"), "emails": count("EMAIL-VARIATIONS"),
                     "pan": count("PAN-VARIATIONS"), "dob": count("DATE-OF-BIRTH-VARIATIONS")},
        "accounts": accounts,
        "totals": {
            "accounts": len(accounts), "active": len(active), "closed": len(accounts) - len(active),
            "secured": sum(1 for a in accounts if a["secured"]), "unsecured": sum(1 for a in accounts if a["secured"] is False),
            "active_secured": sum(1 for a in active if a["secured"]), "active_unsecured": sum(1 for a in active if a["secured"] is False),
            "balance": sum(a["balance"] for a in active),
            "sanctioned": amount(primary.get("PRIMARY-SANCTIONED-AMOUNT")) or sum(a["disbursed"] for a in accounts),
            "overdue": sum(a["overdue"] for a in active),
            "overdue_accounts": sum(1 for a in active if a["overdue"] > 0),
            "emi": sum(a["installment"] for a in active if not a["is_card"]),
        },
        "types": Counter(a["type"] for a in accounts),
        "active_types": Counter(a["type"] for a in active),
        "lenders": Counter(a["lender"] for a in accounts),
        "cards": {"count": len(cards), "limit": card_limit, "used": card_used,
                  "utilisation": round(card_used / card_limit * 100, 1) if card_limit else None,
                  "cash_limit": sum(a["cash_limit"] for a in cards)},
        "payments": {"months_known": known, "on_time": on_time, "late": late,
                     "on_time_pct": round(on_time / known * 100, 1) if known else None,
                     "late_12m": sum(1 for x in late if x["month"] in window12),
                     "late_accounts": len({x["account"] for x in late}),
                     "worst": max(late, key=lambda x: x["dpd"]) if late else None,
                     "older_late_months": len(late_older),
                     "older_worst": max(late_older, key=lambda x: x["dpd"]) if late_older else None},
        "age": {"years": int(derived.get("LENGTH-OF-CREDIT-HISTORY-YEAR") or 0),
                "months": int(derived.get("LENGTH-OF-CREDIT-HISTORY-MONTH") or 0),
                "avg_years": int(derived.get("AVERAGE-ACCOUNT-AGE-YEAR") or 0),
                "avg_months": int(derived.get("AVERAGE-ACCOUNT-AGE-MONTH") or 0),
                "oldest": {"type": oldest["type"], "opened": oldest["opened"]} if oldest else None},
        "recent": {"enquiries_6m": int(derived.get("INQUIRIES-IN-LAST-SIX-MONTHS") or 0),
                   "new_accounts_6m": int(derived.get("NEW-ACCOUNTS-IN-LAST-SIX-MONTHS") or 0),
                   "new_delinquent_6m": int(derived.get("NEW-DELINQ-ACCOUNT-IN-LAST-SIX-MONTHS") or 0)},
        "enquiries": enquiries,
        "flags": {"written_off": int(derived.get("TOTAL-WRITTEN-OFF-ACCOUNTS") or 0) or sum(1 for a in accounts if a["write_off"] > 0),
                  "written_off_amount": amount(derived.get("TOTAL-WRITTEN-OFF-AMOUNT")) or sum(a["write_off"] for a in accounts),
                  "settled": int(derived.get("TOTAL-SETTLED-ACCOUNTS") or 0) or sum(1 for a in accounts if a["settlement"] > 0),
                  "settled_amount": amount(derived.get("TOTAL-SETTLED-AMOUNT")) or sum(a["settlement"] for a in accounts),
                  "restructured": int(derived.get("TOTAL-RESTRUCTURED-ACCOUNTS") or 0),
                  "suit_or_wilful": sum(1 for a in accounts if a["suit_or_wilful"]),
                  "disputed": sum(1 for a in accounts if a["disputed"])},
        "window12": months_back(as_of, 12),
    }
