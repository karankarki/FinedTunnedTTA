"""Detailed credit report walkthrough built from a CRIF High Mark report.

Each chapter explains one part of the report: first what the concept means (static knowledge),
then what this customer's own data says, then what to do about it. Every sentence is written in
English and Hindi, and sentences carry beats, so each on-screen element appears exactly when
the narration reaches it. Chapters that don't apply (e.g. overdue amounts when there are none)
are left out, so the video only talks about what is really in the report.
"""
import asyncio
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.crif_report import parse_report
from app.story_engine import (BRAND, LANGUAGE_LABELS, SCORE_BANDS, VOICES, assemble_scenes, captions_from,
                              narrate_chapters)

SCHEMA_VERSION = "3.0"

TYPE_NAMES = {  # en singular, en plural, hindi, screen label, icon
    "Credit Card": ("credit card", "credit cards", "क्रेडिट कार्ड", "Credit card", "card"),
    "Secured Credit Card": ("secured credit card", "secured credit cards", "सिक्योर्ड क्रेडिट कार्ड", "Secured card", "card"),
    "Consumer Loan": ("consumer loan", "consumer loans", "कंज़्यूमर लोन", "Consumer loan", "wallet"),
    "Personal Loan": ("personal loan", "personal loans", "पर्सनल लोन", "Personal loan", "wallet"),
    "Housing Loan": ("home loan", "home loans", "होम लोन", "Home loan", "home"),
    "Property Loan": ("loan against property", "loans against property", "प्रॉपर्टी लोन", "Loan against property", "home"),
    "Gold Loan": ("gold loan", "gold loans", "गोल्ड लोन", "Gold loan", "coin"),
    "Loan on Credit Card": ("loan on a credit card", "loans on credit cards", "क्रेडिट कार्ड पर लोन", "Loan on card", "card"),
    "Auto Loan": ("car loan", "car loans", "कार लोन", "Car loan", "car"),
    "Two-Wheeler Loan": ("two-wheeler loan", "two-wheeler loans", "टू-व्हीलर लोन", "Two-wheeler loan", "car"),
    "Education Loan": ("education loan", "education loans", "एजुकेशन लोन", "Education loan", "doc"),
    "Business Loan": ("business loan", "business loans", "बिज़नेस लोन", "Business loan", "wallet"),
    "Other": ("other account", "other accounts", "अन्य अकाउंट", "Other", "doc"),
}

TYPE_ABOUT = {  # one general sentence per account type (en, hi)
    "Credit Card": ("A credit card lets you spend up to a limit and repay the bill every month.",
                    "क्रेडिट कार्ड से आप एक तय लिमिट तक खर्च करते हैं और हर महीने उसका बिल चुकाते हैं।"),
    "Secured Credit Card": ("A secured card is issued against a fixed deposit, which makes it easy to get.",
                            "सिक्योर्ड कार्ड एक फिक्स्ड डिपॉज़िट के बदले मिलता है, इसलिए इसे पाना आसान होता है।"),
    "Consumer Loan": ("Consumer loans are small loans, often used to buy a phone or an appliance on EMI.",
                      "कंज़्यूमर लोन छोटे लोन होते हैं, जो अक्सर फ़ोन या घर का सामान ईएमआई पर खरीदने के लिए लिए जाते हैं।"),
    "Personal Loan": ("Personal loans are unsecured, so nothing is pledged, and they can be used for any purpose.",
                      "पर्सनल लोन अनसिक्योर्ड होते हैं, यानी कुछ गिरवी नहीं रखना पड़ता, और इन्हें किसी भी ज़रूरत के लिए लिया जा सकता है।"),
    "Housing Loan": ("A home loan is a long-term loan to buy or build a house, secured by the house itself.",
                     "होम लोन घर खरीदने या बनाने के लिए लंबी अवधि का लोन होता है, जिसकी गारंटी खुद वह घर होता है।"),
    "Property Loan": ("A loan against property is taken by pledging a property you already own.",
                      "लोन अगेंस्ट प्रॉपर्टी आपकी पहले से मौजूद प्रॉपर्टी को गिरवी रखकर लिया जाता है।"),
    "Gold Loan": ("Gold loans are backed by gold jewellery, so they are quick to get and usually cheaper.",
                  "गोल्ड लोन सोने के गहनों के बदले मिलता है, इसलिए यह जल्दी मिल जाता है और आमतौर पर सस्ता होता है।"),
    "Loan on Credit Card": ("A loan on a credit card is given against the card's unused limit.",
                            "क्रेडिट कार्ड पर लोन आपके कार्ड की बची हुई लिमिट पर दिया जाता है।"),
    "Auto Loan": ("Vehicle loans are secured by the vehicle you buy.", "व्हीकल लोन की गारंटी वही गाड़ी होती है जो आप खरीदते हैं।"),
    "Two-Wheeler Loan": ("Vehicle loans are secured by the vehicle you buy.", "व्हीकल लोन की गारंटी वही गाड़ी होती है जो आप खरीदते हैं।"),
    "Education Loan": ("Education loans pay for studies and are usually repaid after the course ends.",
                       "एजुकेशन लोन पढ़ाई के लिए होता है और आमतौर पर कोर्स खत्म होने के बाद चुकाया जाता है।"),
    "Business Loan": ("Business loans fund a business and are judged on its cash flow too.",
                      "बिज़नेस लोन कारोबार के लिए होता है और इसमें कारोबार की कमाई भी देखी जाती है।"),
    "Other": ("Some accounts are reported under other categories.", "कुछ अकाउंट दूसरी कैटेगरी में दर्ज होते हैं।"),
}

LENDER_NAMES = {  # en (with article), hindi, screen label
    "Public sector bank": ("a public sector bank", "सरकारी बैंक", "Public sector bank"),
    "Private bank": ("a private bank", "प्राइवेट बैंक", "Private bank"),
    "Foreign bank": ("a foreign bank", "विदेशी बैंक", "Foreign bank"),
    "NBFC": ("an NBFC", "एनबीएफसी", "NBFC"),
    "Small finance bank": ("a small finance bank", "स्मॉल फाइनेंस बैंक", "Small finance bank"),
    "Co-operative bank": ("a co-operative bank", "को-ऑपरेटिव बैंक", "Co-operative bank"),
    "Regional rural bank": ("a regional rural bank", "ग्रामीण बैंक", "Regional rural bank"),
    "Microfinance lender": ("a microfinance lender", "माइक्रोफाइनेंस कंपनी", "Microfinance lender"),
    "Housing finance company": ("a housing finance company", "हाउसिंग फाइनेंस कंपनी", "Housing finance co."),
    "Lender": ("a lender", "एक लेंडर", "Lender"),
}

HI_MONTHS = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]
ORDINALS = (("First", "पहला"), ("Second", "दूसरा"), ("Third", "तीसरा"), ("Fourth", "चौथा"))


# ------------------------------------------------------------------------------------ helpers

def S(en: str, hi: str, *beats: dict) -> dict:
    return {"en": en, "hi": hi, "beats": list(beats)}


def B(action: str, index: Optional[int] = None, **extra) -> dict:
    return {"action": action, **({"index": index} if index is not None else {}), **extra}


def type_name(t, form="en"):
    names = TYPE_NAMES.get(t) or (t.lower(), t.lower() + "s", t, t, "doc")
    return {"en": names[0], "en_pl": names[1], "hi": names[2], "screen": names[3], "icon": names[4]}[form]


def type_count(n, t):
    return f"{n} {type_name(t, 'en' if n == 1 else 'en_pl')}"


def lender(l, form="en"):
    names = LENDER_NAMES.get(l, LENDER_NAMES["Lender"])
    return {"en": names[0], "hi": names[1], "screen": names[2]}[form]


def _num(value, digits=2):
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def money(n, lang):
    """Spoken amount: '1.99 crore rupees', '2.4 lakh rupees', '68,966 rupees'."""
    n = int(round(n))
    unit = {"en": ("crore", "lakh", "rupees"), "hi": ("करोड़", "लाख", "रुपये")}[lang]
    if n >= 10_000_000:
        return f"{_num(n / 10_000_000)} {unit[0]} {unit[2]}"
    if n >= 100_000:
        return f"{_num(n / 100_000)} {unit[1]} {unit[2]}"
    return f"{n:,} {unit[2]}"


def money_screen(n):
    n = int(round(n))
    if n >= 10_000_000:
        return f"₹{_num(n / 10_000_000)} Cr"
    if n >= 100_000:
        return f"₹{_num(n / 100_000)} L"
    return f"₹{n:,}"


def month_speech(key_or_date, lang):
    d = key_or_date if isinstance(key_or_date, date) else date(int(key_or_date[:4]), int(key_or_date[5:7]), 1)
    return f"{(EN_MONTHS if lang == 'en' else HI_MONTHS)[d.month - 1]} {d.year}"


def date_speech(d: date, lang):
    return f"{d.day} {(EN_MONTHS if lang == 'en' else HI_MONTHS)[d.month - 1]} {d.year}"


def date_screen(d: Optional[date]):
    return f"{d.day} {EN_MONTHS[d.month - 1][:3]} {d.year}" if d else "—"


def month_screen(key):
    return f"{EN_MONTHS[int(key[5:7]) - 1][:3]} {key[:4]}"


def years_months(y, m, lang):
    if lang == "en":
        parts = ([f"{y} year{'s' if y != 1 else ''}"] if y else []) + ([f"{m} month{'s' if m != 1 else ''}"] if m else [])
        return " and ".join(parts) or "less than a month"
    parts = ([f"{y} साल"] if y else []) + ([f"{m} महीने"] if m else [])
    return " ".join(parts) or "एक महीने से कम"


def an(phrase):
    return ("an " if phrase[:1].lower() in "aeiou" else "a ") + phrase


def plural(n, one, many):
    return one if n == 1 else many


def band(score):
    return next(b for b in SCORE_BANDS if b["from"] <= score <= b["to"])


def score_tone(score):
    return "good" if score >= 725 else "warn" if score >= 650 else "bad"


def theme(tone):
    return {"good": "green", "warn": "amber", "bad": "amber", "neutral": "violet"}.get(tone, "blue")


# ------------------------------------------------------------------------------------ chapters

def ch_welcome(p, name):
    t, age = p["totals"], p["age"]
    hi_name, en_name = (f" {name}" if name else ""), (f" {name}" if name else "")
    return {
        "id": "welcome", "type": "title", "chapter": "Welcome", "theme": "blue",
        "props": {
            "eyebrow": f"CRIF High Mark report · as of {date_screen(p['as_of'])}",
            "title": f"Hi{en_name}, here's your *complete credit report*",
            "subtitle": "A guided walkthrough of your score, your accounts, your payment record and a plan to improve.",
            "chips": [{"icon": "doc", "text": f"{t['accounts']} accounts"},
                      {"icon": "clock", "text": f"{age['years']} yrs {age['months']} mos of history"},
                      {"icon": "trend", "text": f"Score {p['score']}"}],
        },
        "sentences": [
            S(f"Hello{en_name}! Welcome to your complete credit report.",
              f"नमस्ते{hi_name}! आपकी पूरी क्रेडिट रिपोर्ट में आपका स्वागत है।"),
            S("This report comes from CRIF High Mark, one of India's four RBI-licensed credit bureaus, where every lender reports how you repay each month.",
              "यह रिपोर्ट क्रिफ़ हाई मार्क से आई है, जो भारत के चार आरबीआई-लाइसेंस्ड क्रेडिट ब्यूरो में से एक है, जहाँ हर लेंडर हर महीने आपके पेमेंट की जानकारी भेजता है।",
              B("chip", 0), B("chip", 1)),
            S("Over the next few minutes, we will go through your score, every type of account you have, your payment record, and a clear plan to make it even better.",
              "अगले कुछ मिनटों में हम आपका स्कोर, आपके हर तरह के अकाउंट, आपका पेमेंट रिकॉर्ड, और स्कोर को और बेहतर करने का एक साफ़ प्लान, सब कुछ विस्तार से समझेंगे।",
              B("chip", 2)),
        ],
    }


def ch_score(p):
    score = p["score"]
    b, tone = band(score), score_tone(score)
    if score >= 750:
        view = ("Low", "Faster", "Best rates")
    elif score >= 680:
        view = ("Moderate", "Standard", "Fair rates")
    else:
        view = ("High", "Limited", "Higher rates")
    verdict = {
        "Excellent": [S("That puts you in the excellent range, the highest band there is.",
                        "यह एक्सीलेंट रेंज में आता है, जो सबसे ऊँची कैटेगरी है।"),
                      S("Lenders see a score like this as a sign of a very low-risk borrower.",
                        "लेंडर्स ऐसे स्कोर को बहुत कम जोखिम वाले कस्टमर की निशानी मानते हैं।")],
        "Good": [S("That is a good score, and most lenders will see you as a reliable borrower.",
                   "यह एक अच्छा स्कोर है, और ज़्यादातर लेंडर्स आपको एक भरोसेमंद कस्टमर मानेंगे।")],
        "Fair": [S("That is a fair score. You can get credit, but often with stricter terms or a higher interest rate.",
                   "यह एक ठीक-ठाक स्कोर है। आपको क्रेडिट मिल सकता है, लेकिन अक्सर सख्त शर्तों या ज़्यादा ब्याज पर।")],
    }.get(b["label"], [S("That is below the range most lenders prefer, so approvals can be harder right now.",
                         "यह उस रेंज से नीचे है जो ज़्यादातर लेंडर्स पसंद करते हैं, इसलिए अभी लोन मिलना थोड़ा मुश्किल हो सकता है।")])
    closing = (S("You are already above that mark, so the goal now is to protect it and keep growing it.",
                 "आप पहले से ही इस लेवल से ऊपर हैं, तो अब असली काम इसे बनाए रखना और आगे बढ़ाना है।")
               if score >= 750 else
               S(f"You are {750 - score} points away from that mark, and this walkthrough will show you where those points can come from.",
                 f"आप इस लेवल से {750 - score} पॉइंट दूर हैं, और यह वीडियो आपको बताएगा कि ये पॉइंट कहाँ से आ सकते हैं।"))
    return {
        "id": "score", "type": "score_dial", "chapter": "Your score", "theme": theme(tone),
        "props": {
            "eyebrow": f"CRIF score · {p['score_model'] or 'CRIF High Mark'}",
            "title": "Your *credit score*",
            "score": score, "count_from": 300, "min": 300, "max": 900, "bands": SCORE_BANDS,
            "status": {"text": b["label"], "tone": tone},
            "lenders": {"title": "How lenders see you", "items": [
                {"icon": "shield", "label": "Risk profile", "value": view[0]},
                {"icon": "bolt", "label": "Approvals", "value": view[1]},
                {"icon": "trend", "label": "Loan offers", "value": view[2]}]},
        },
        "sentences": [
            S(f"Let's start with your score: it is {score}, on a scale that runs from 300 to 900.",
              f"शुरुआत आपके स्कोर से: आपका क्रिफ़ स्कोर {score} है, और यह स्कोर 300 से 900 के बीच होता है।", B("reveal", duration="sentence")),
            *verdict,
            S("In India, a score above 750 is generally seen as strong, and it opens the door to faster approvals and lower interest rates.",
              "भारत में आमतौर पर 750 से ऊपर का स्कोर मज़बूत माना जाता है, जिससे लोन जल्दी मिलता है और ब्याज दर भी कम मिलती है।",
              B("lenders")),
            closing,
        ],
    }


def ch_how_scored():
    return {
        "id": "how_scored", "type": "points", "chapter": "How scores work", "theme": "blue",
        "props": {
            "eyebrow": "How scores work", "title": "What goes into *your score*",
            "subtitle": "Every scoring model weighs these five signals.",
            "items": [
                {"icon": "calendar", "title": "Payment history", "text": "Paying every EMI and card bill on time", "tag": "Biggest impact"},
                {"icon": "card", "title": "Credit utilisation", "text": "How much of your card limits you use", "tag": "High impact"},
                {"icon": "clock", "title": "Length of history", "text": "How long you have had credit", "tag": "Medium impact"},
                {"icon": "shield", "title": "Credit mix", "text": "Secured and unsecured credit together", "tag": "Medium impact"},
                {"icon": "search", "title": "New credit", "text": "How often you apply for loans and cards", "tag": "Medium impact"},
            ],
        },
        "sentences": [
            S("First, a quick look at how any credit score is worked out, because scoring models weigh five main things.",
              "पहले एक नज़र इस पर कि क्रेडिट स्कोर बनता कैसे है, क्योंकि स्कोरिंग मॉडल पाँच मुख्य चीज़ें देखते हैं।"),
            S("The biggest is payment history: whether every EMI and card bill was paid on time.",
              "सबसे बड़ी है पेमेंट हिस्ट्री, यानी हर ईएमआई और कार्ड बिल समय पर भरा गया या नहीं।", B("item", 0)),
            S("Next is credit utilisation: how much of your card limits you actually use.",
              "दूसरी है क्रेडिट यूटिलाइज़ेशन, यानी कार्ड की लिमिट का कितना हिस्सा आप इस्तेमाल करते हैं।", B("item", 1)),
            S("Third is the length of your credit history, because a longer, well-managed history earns more trust.",
              "तीसरी है आपकी क्रेडिट हिस्ट्री की लंबाई, क्योंकि लंबी और अच्छी तरह संभाली गई हिस्ट्री पर ज़्यादा भरोसा होता है।", B("item", 2)),
            S("Fourth is your credit mix, meaning a healthy balance of secured loans, like home or gold loans, and unsecured credit, like cards and personal loans.",
              "चौथी है आपका क्रेडिट मिक्स, यानी होम लोन या गोल्ड लोन जैसे सिक्योर्ड लोन और कार्ड या पर्सनल लोन जैसे अनसिक्योर्ड क्रेडिट का सही संतुलन।", B("item", 3)),
            S("And fifth is new credit, because applying for many loans or cards in a short time can pull your score down.",
              "और पाँचवीं है नया क्रेडिट, क्योंकि कम समय में बहुत सारे लोन या कार्ड के लिए अप्लाई करने से स्कोर नीचे आ सकता है।", B("item", 4)),
        ],
    }


def ch_snapshot(p):
    t = p["totals"]
    tiles = [
        {"label": "Total accounts", "value": str(t["accounts"]), "icon": "doc"},
        {"label": "Active", "value": str(t["active"]), "icon": "bolt", "tone": "good"},
        {"label": "Closed", "value": str(t["closed"]), "icon": "check", "tone": "neutral"},
        {"label": "Outstanding now", "value": money_screen(t["balance"]), "sub": "on active accounts", "icon": "wallet"},
        {"label": "Sanctioned so far", "value": money_screen(t["sanctioned"]), "sub": "all accounts, all time", "icon": "trend"},
        {"label": "Overdue now", "value": money_screen(t["overdue"]),
         "sub": f"on {t['overdue_accounts']} {plural(t['overdue_accounts'], 'account', 'accounts')}" if t["overdue"] else "nothing overdue",
         "icon": "alert" if t["overdue"] else "check", "tone": "bad" if t["overdue"] else "good"},
    ]
    sentences = [
        S("Here is your report at a glance.", "यह रही आपकी रिपोर्ट की एक झलक।"),
        S(f"You have {t['accounts']} accounts in total: {t['active']} are active and {t['closed']} are closed, and closed ones still count, because their repayment record stays on your report.",
          f"आपके कुल {t['accounts']} अकाउंट हैं: {t['active']} एक्टिव हैं और {t['closed']} बंद हो चुके हैं, और बंद अकाउंट भी गिने जाते हैं, क्योंकि उनका पेमेंट रिकॉर्ड रिपोर्ट में बना रहता है।",
          B("item", 0), B("item", 1), B("item", 2)),
        S(f"Your active accounts currently have an outstanding balance of {money(t['balance'], 'en')}.",
          f"आपके एक्टिव अकाउंट्स पर अभी कुल {money(t['balance'], 'hi')} बकाया है।", B("item", 3)),
        S(f"Over the years, lenders have sanctioned a total of {money(t['sanctioned'], 'en')} across all your accounts.",
          f"अब तक सभी अकाउंट्स को मिलाकर लेंडर्स ने आपको कुल {money(t['sanctioned'], 'hi')} मंज़ूर किए हैं।", B("item", 4)),
        S(f"And right now, {t['overdue_accounts']} of your accounts {plural(t['overdue_accounts'], 'is', 'are')} overdue, by {money(t['overdue'], 'en')} in total.",
          f"और अभी आपके {t['overdue_accounts']} अकाउंट्स पर कुल {money(t['overdue'], 'hi')} ओवरड्यू है।", B("item", 5))
        if t["overdue"] else
        S("And the best part: none of your accounts has any overdue amount right now.",
          "और सबसे अच्छी बात, अभी आपके किसी भी अकाउंट पर कोई ओवरड्यू नहीं है।", B("item", 5)),
    ]
    if t["emi"]:
        tiles.append({"label": "Monthly EMIs", "value": money_screen(t["emi"]), "sub": "across active loans", "icon": "calendar"})
        sentences.append(S(f"Together, your active loans need about {money(t['emi'], 'en')} every month in EMIs.",
                           f"आपके एक्टिव लोन की कुल ईएमआई हर महीने लगभग {money(t['emi'], 'hi')} है।", B("item", 6)))
    return {"id": "snapshot", "type": "stats", "chapter": "At a glance", "theme": "blue",
            "props": {"eyebrow": "At a glance", "title": "Your report *in numbers*", "tiles": tiles},
            "sentences": sentences}


def ch_account_types(p):
    ranked = sorted(p["types"].items(), key=lambda kv: -(kv[1] + 3 * p["active_types"].get(kv[0], 0)))
    shown, rest = ranked[:4], ranked[4:]
    bars = [{"label": type_name(t, "screen"), "value": n, "display": str(n), "icon": type_name(t, "icon"),
             "sub": f"{p['active_types'].get(t, 0)} active" if p["active_types"].get(t) else "all closed"} for t, n in shown]
    sentences = [S("Now let's look at the kinds of credit you have used.", "अब देखते हैं कि आपने किस-किस तरह का क्रेडिट लिया है।")]
    for i, (t, n) in enumerate(shown):
        a = p["active_types"].get(t, 0)
        en = (f"You have had {type_count(n, t)}, and {'it is' if n == 1 else f'{a} of them are' if a > 1 else '1 of them is'} still active."
              if a else f"You have had {type_count(n, t)}, {'now closed' if n == 1 else 'all now closed'}.")
        hi = (f"आपके {n} {type_name(t, 'hi')} रहे हैं, जिनमें से {a} अभी एक्टिव {plural(a, 'है', 'हैं')}।"
              if a else f"आपके {n} {type_name(t, 'hi')} रहे हैं, जो अब बंद हो चुके हैं।")
        sentences.append(S(en, hi, B("item", i)))
        if i < 3:
            sentences.append(S(*TYPE_ABOUT.get(t, TYPE_ABOUT["Other"])))
    if rest:
        n = sum(c for _, c in rest)
        bars.append({"label": "Others", "value": n, "display": str(n), "icon": "doc",
                     "sub": ", ".join(type_name(t, "screen") for t, _ in rest)})
        sentences.append(S(f"And {n} more {plural(n, 'account', 'accounts')} of other types, like {type_name(rest[0][0], 'en' if rest[0][1] == 1 else 'en_pl')}.",
                           f"और {n} अकाउंट दूसरी तरह के भी हैं, जैसे {type_name(rest[0][0], 'hi')}।", B("item", len(shown))))
    return {"id": "account_types", "type": "bars", "chapter": "Types of credit", "theme": "blue",
            "props": {"eyebrow": "Types of credit", "title": "What you have *borrowed*",
                      "subtitle": "Accounts by type, active and closed", "bars": bars},
            "sentences": sentences}


def ch_dpd_explained():
    return {
        "id": "reading_history", "type": "points", "chapter": "Reading payments", "theme": "blue",
        "props": {
            "eyebrow": "Reading your report", "title": "How payments are *recorded*",
            "subtitle": "Two codes appear for every month on every account.",
            "items": [
                {"icon": "check", "title": "DPD 000 · On time", "text": "Zero days past due: paid by the due date", "tone": "good"},
                {"icon": "clock", "title": "DPD 030 · Late", "text": "Thirty days past the due date", "tone": "warn"},
                {"icon": "shield", "title": "STD · Standard", "text": "The lender sees the account as healthy", "tone": "good"},
                {"icon": "alert", "title": "SMA · Special mention", "text": "An early warning that repayments are under stress", "tone": "warn"},
                {"icon": "x", "title": "SUB · DBT · LSS", "text": "Sub-standard, doubtful, loss: serious default", "tone": "bad"},
            ],
        },
        "sentences": [
            S("Now for the most important part of any credit report: your payment history.",
              "अब आती है किसी भी क्रेडिट रिपोर्ट की सबसे ज़रूरी चीज़, आपकी पेमेंट हिस्ट्री।"),
            S("Every month, each lender reports how many days past the due date you paid, called D P D, and a D P D of zero means you paid on time.",
              "हर महीने हर लेंडर बताता है कि आपने ड्यू डेट से कितने दिन बाद पेमेंट किया, इसे डीपीडी कहते हैं, और डीपीडी शून्य का मतलब है समय पर पेमेंट।", B("item", 0)),
            S("A D P D of thirty means the payment was thirty days late, and the higher this number, the bigger the damage.",
              "डीपीडी तीस का मतलब है कि पेमेंट तीस दिन लेट हुआ, और यह नंबर जितना बड़ा, नुकसान उतना ज़्यादा।", B("item", 1)),
            S("Lenders also tag every account with an asset class, and Standard, shown as S T D, means the account is healthy.",
              "लेंडर्स हर अकाउंट को एक ऐसेट क्लास भी देते हैं, और स्टैंडर्ड, यानी एस टी डी, का मतलब है कि अकाउंट बिल्कुल ठीक है।", B("item", 2)),
            S("S M A, or special mention account, is an early warning that repayments are under stress.",
              "एस एम ए, यानी स्पेशल मेंशन अकाउंट, एक शुरुआती चेतावनी है कि पेमेंट में दिक्कत आ रही है।", B("item", 3)),
            S("And sub-standard, doubtful or loss mean the loan has turned into a serious default.",
              "और सब-स्टैंडर्ड, डाउटफुल या लॉस का मतलब है कि लोन गंभीर डिफ़ॉल्ट में चला गया है।", B("item", 4)),
        ],
    }


def ch_payment_record(p):
    pay, t = p["payments"], p["totals"]
    pct = pay["on_time_pct"]
    if pct is None:
        return None
    status = ({"text": "Excellent", "tone": "good"} if pct >= 99.5 else {"text": "Good", "tone": "good"} if pct >= 97
              else {"text": "Needs attention", "tone": "warn"} if pct >= 90 else {"text": "At risk", "tone": "bad"})
    last6 = p["window12"][-6:]
    late_months = {x["month"] for x in pay["late"]}
    known_months = {k for a in p["accounts"] for k, h in a["history"].items() if h["dpd"] is not None}
    months = [{"label": month_screen(k)[:3], "state": "late" if k in late_months else "ok" if k in known_months else "none"} for k in last6]
    late6 = sum(1 for m in months if m["state"] == "late")
    pct_text = _num(pct, 1)
    sentences = [
        S("So how does your own record look?", "तो आपका अपना रिकॉर्ड कैसा है?"),
        S(f"Over the last three years, your report has {pay['months_known']} monthly payment records across your accounts.",
          f"पिछले तीन साल में आपके अकाउंट्स के कुल {pay['months_known']} महीनों के पेमेंट रिकॉर्ड हैं।"),
        S(f"{pay['on_time']} of them were paid on time, which is {pct_text} percent.",
          f"इनमें से {pay['on_time']} पेमेंट समय पर हुए, यानी {pct_text} प्रतिशत।", B("value")),
        S("That is a perfect record, with not a single late payment in three years.",
          "यह एकदम परफ़ेक्ट रिकॉर्ड है, तीन साल में एक भी पेमेंट लेट नहीं हुआ।") if pct >= 100 else
        S("That is a strong record, but even a few late months get noticed by lenders.",
          "यह अच्छा रिकॉर्ड है, लेकिन कुछ महीनों की देरी भी लेंडर्स की नज़र में आती है।") if pct >= 97 else
        S("Late payments are the biggest thing holding your score back.",
          "लेट पेमेंट ही आपके स्कोर को सबसे ज़्यादा पीछे खींच रहे हैं।"),
        S("In the last six months, every payment was on time.", "पिछले छह महीनों में हर पेमेंट समय पर हुआ है।", B("detail"))
        if not late6 else
        S(f"In the last six months, {late6} {plural(late6, 'month', 'months')} had a late payment, so this needs attention now.",
          f"पिछले छह महीनों में {late6} महीनों में पेमेंट लेट हुआ है, इसलिए इस पर अभी ध्यान देना ज़रूरी है।", B("detail")),
    ]
    props = {
        "eyebrow": "Payment history", "impact": "Biggest impact", "title": "Your *payment record*",
        "explainer": f"Last 36 months, across all {t['accounts']} accounts.",
        "metric": {"visual": "ring", "value": f"{pct_text}%", "progress": round(pct / 100, 3), "label": "Paid on time",
                   "sublabel": f"{pay['on_time']} of {pay['months_known']} monthly records", "status": status},
        "detail": {"type": "months", "title": "Last 6 months, all accounts", "months": months, "tone": "warn" if late6 else "good",
                   "summary": "No late payments" if not late6 else f"{late6} of the last 6 months had a late payment"},
    }
    if pay["late"]:
        props["tip"] = {"icon": "bolt", "title": "Turn on auto-debit", "text": "Automatic payments on the due date prevent accidental delays."}
        sentences.append(S("Setting up auto-debit for every EMI and card bill is the simplest way to make sure this never happens again.",
                           "हर ईएमआई और कार्ड बिल के लिए ऑटो-डेबिट सेट करना, इसे दोबारा न होने देने का सबसे आसान तरीका है।", B("tip")))
    else:
        sentences.append(S("Keep doing exactly what you are doing, because this is the single biggest reason your score is strong.",
                           "बस ऐसे ही करते रहिए, क्योंकि आपके मज़बूत स्कोर की सबसे बड़ी वजह यही है।"))
    return {"id": "payment_record", "type": "factor_insight", "chapter": "Payment record", "theme": theme(status["tone"]),
            "props": props, "sentences": sentences}


def grid_rows(p):
    window = set(p["window12"])
    active = [a for a in p["accounts"] if a["active"]]
    active.sort(key=lambda a: (-a["overdue"], a["is_card"], -(a["balance"] or a["limit"])))
    closed = [a for a in p["accounts"] if not a["active"] and any(k in window and h["dpd"] is not None for k, h in a["history"].items())]
    closed.sort(key=lambda a: -max([h["dpd"] or 0 for k, h in a["history"].items() if k in window] or [0]))
    rows = []
    for a in (active + closed)[:6]:
        cells = []
        for k in p["window12"]:
            h = a["history"].get(k)
            d = h["dpd"] if h else None
            cells.append("none" if d is None else "ok" if d == 0 else "late" if d < 30 else "severe")
        rows.append({"label": type_name(a["type"], "screen"), "sub": lender(a["lender"], "screen") + ("" if a["active"] else " · closed"),
                     "cells": cells})
    return rows


def ch_payment_grid(p):
    rows = grid_rows(p)
    if not rows:
        return None
    pay = p["payments"]
    has_none = any("none" in r["cells"] for r in rows)
    late12 = [x for x in pay["late"] if x["month"] in set(p["window12"])]
    late_types = list(dict.fromkeys(type_name(p["accounts"][x["account"]]["type"], "en") for x in late12))
    late_types_hi = list(dict.fromkeys(type_name(p["accounts"][x["account"]]["type"], "hi") for x in late12))
    sentences = [
        S("Here is what the last twelve months look like, account by account.",
          "यह रहा पिछले बारह महीनों का हाल, हर अकाउंट के लिए अलग-अलग।", *[B("row", i) for i in range(len(rows))]),
        S("Green means paid on time, amber means a few days late, and red means thirty days or more.",
          "हरा मतलब समय पर पेमेंट, पीला मतलब कुछ दिन की देरी, और लाल मतलब तीस दिन या उससे ज़्यादा की देरी।", B("legend")),
        S("Every square with data is green, so there are no late payments in the past year.",
          "जिन भी खानों में डेटा है, वे सब हरे हैं, यानी पिछले एक साल में कोई भी पेमेंट लेट नहीं हुआ।", B("highlight"))
        if not late12 else
        S(f"You can see {len(late12)} late {plural(len(late12), 'month', 'months')} in the past year, on your {' and '.join(late_types[:2])} {plural(len(late_types[:2]), 'account', 'accounts')}.",
          f"पिछले एक साल में {len(late12)} महीनों में देरी दिख रही है, आपके {' और '.join(late_types_hi[:2])} पर।", B("highlight")),
    ]
    return {"id": "payment_grid", "type": "history_grid", "chapter": "Month by month", "theme": "green" if not late12 else "amber",
            "props": {"eyebrow": "Month by month", "title": "Your last *12 months*",
                      "subtitle": "Each square is one month on one account.",
                      "months": [month_screen(k)[:3] for k in p["window12"]], "rows": rows,
                      "legend": [{"state": "ok", "text": "On time"}, {"state": "late", "text": "1–29 days late"},
                                 {"state": "severe", "text": "30+ days late"}, {"state": "none", "text": "No data"}]},
            "sentences": sentences}


def ch_late_payments(p):
    pay = p["payments"]
    if not pay["late"] and not pay["older_worst"]:
        return None
    by_account: Dict[int, List[dict]] = {}
    for x in pay["late"]:
        by_account.setdefault(x["account"], []).append(x)
    groups = sorted(by_account.items(), key=lambda kv: (-max(x["dpd"] for x in kv[1]), -len(kv[1])))[:3]
    rows, sentences = [], []
    if pay["late"]:
        sentences.append(S(f"Looking closer at the late payments: in the last three years, {len(pay['late'])} monthly {plural(len(pay['late']), 'payment was', 'payments were')} late, across {pay['late_accounts']} {plural(pay['late_accounts'], 'account', 'accounts')}.",
                           f"लेट पेमेंट्स को ध्यान से देखें, तो पिछले तीन साल में {len(pay['late'])} पेमेंट लेट हुए, जो {pay['late_accounts']} अकाउंट्स पर हैं।"))
    else:
        sentences.append(S("Your last three years are clean, but older records show some late payments.",
                           "पिछले तीन साल का रिकॉर्ड साफ़ है, लेकिन पुराने रिकॉर्ड में कुछ लेट पेमेंट दिखते हैं।"))
    for i, (idx, items) in enumerate(groups):
        a = p["accounts"][idx]
        worst = max(x["dpd"] for x in items)
        latest = max(x["month"] for x in items)
        rows.append({"icon": type_name(a["type"], "icon"), "title": type_name(a["type"], "screen"),
                     "sub": f"{lender(a['lender'], 'screen')} · {len(items)} late {plural(len(items), 'month', 'months')} · latest {month_screen(latest)}",
                     "value": f"{worst} days", "value_sub": "worst delay", "tone": "bad" if worst >= 30 else "warn"})
        sentences.append(S(f"Your {type_name(a['type'])} with {lender(a['lender'])} was late in {len(items)} {plural(len(items), 'month', 'months')}, most recently in {month_speech(latest, 'en')}, by up to {worst} days.",
                           f"{lender(a['lender'], 'hi')} के साथ आपका {type_name(a['type'], 'hi')} {len(items)} महीनों में लेट रहा, सबसे हाल में {month_speech(latest, 'hi')} में, और सबसे ज़्यादा {worst} दिन की देरी हुई।",
                           B("item", i)))
    older = pay["older_worst"]
    if older and (not pay["worst"] or older["dpd"] > pay["worst"]["dpd"]):
        rows.append({"icon": "clock", "title": "Older records", "sub": f"More than 3 years ago · {pay['older_late_months']} late months",
                     "value": f"{older['dpd']} days", "value_sub": "worst delay", "tone": "bad" if older["dpd"] >= 30 else "warn"})
        sentences.append(S(f"Older records, from more than three years ago, also show delays of up to {older['dpd']} days.",
                           f"तीन साल से पुराने रिकॉर्ड में भी {older['dpd']} दिन तक की देरी दिखती है।", B("item", len(rows) - 1)))
    sentences.append(S("Late payments stay visible in your history for years, but their effect fades as you add a long run of on-time months.",
                       "लेट पेमेंट सालों तक हिस्ट्री में दिखते हैं, लेकिन जैसे-जैसे आप लगातार समय पर पेमेंट करते हैं, उनका असर कम होता जाता है।"))
    nd = p["recent"]["new_delinquent_6m"]
    if nd:
        sentences.append(S(f"Your report also flags {nd} {plural(nd, 'account', 'accounts')} that started running late in the last six months, which lenders watch closely.",
                           f"रिपोर्ट यह भी बताती है कि पिछले छह महीनों में {nd} अकाउंट्स में नई देरी शुरू हुई है, जिस पर लेंडर्स खास नज़र रखते हैं।"))
    return {"id": "late_payments", "type": "list", "chapter": "Late payments", "theme": "amber",
            "props": {"eyebrow": "Late payments", "title": "Where payments *slipped*",
                      "subtitle": "Grouped by account, worst delays first.", "rows": rows},
            "sentences": sentences}


def ch_overdue(p):
    over = sorted([a for a in p["accounts"] if a["active"] and a["overdue"] > 0], key=lambda a: -a["overdue"])
    if not over:
        return None
    t = p["totals"]
    rows = [{"icon": type_name(a["type"], "icon"), "title": type_name(a["type"], "screen"),
             "sub": f"{lender(a['lender'], 'screen')} · {money_screen(a['balance'])} outstanding",
             "value": money_screen(a["overdue"]), "value_sub": "overdue", "tone": "bad"} for a in over[:4]]
    sentences = [
        S("Next, something that needs your attention right away.", "अब एक ऐसी चीज़ जिस पर तुरंत ध्यान देना ज़रूरी है।"),
        S(f"{t['overdue_accounts']} of your active accounts {plural(t['overdue_accounts'], 'has', 'have')} an overdue amount, adding up to {money(t['overdue'], 'en')}.",
          f"आपके {t['overdue_accounts']} एक्टिव अकाउंट्स पर कुल {money(t['overdue'], 'hi')} ओवरड्यू है।"),
    ]
    for i, a in enumerate(over[:4]):
        sentences.append(S(f"Your {type_name(a['type'])} with {lender(a['lender'])} is overdue by {money(a['overdue'], 'en')}.",
                           f"{lender(a['lender'], 'hi')} के साथ आपके {type_name(a['type'], 'hi')} पर {money(a['overdue'], 'hi')} ओवरड्यू है।",
                           B("item", i)))
    sentences += [
        S("An overdue amount keeps being reported as late every month until it is cleared, so clearing it should be your number one priority.",
          "ओवरड्यू रकम हर महीने लेट के रूप में दर्ज होती रहती है जब तक उसे चुकाया न जाए, इसलिए इसे चुकाना आपकी सबसे पहली प्राथमिकता होनी चाहिए।"),
    ]
    return {"id": "overdue", "type": "list", "chapter": "Overdue now", "theme": "amber",
            "props": {"eyebrow": "Overdue now", "title": "Pay these *first*",
                      "subtitle": f"{money_screen(t['overdue'])} overdue across {t['overdue_accounts']} {plural(t['overdue_accounts'], 'account', 'accounts')}.",
                      "rows": rows},
            "sentences": sentences}


def ch_cards(p):
    c = p["cards"]
    cards = sorted([a for a in p["accounts"] if a["active"] and a["is_card"]], key=lambda a: -a["limit"])
    if not cards:
        return {
            "id": "cards", "type": "points", "chapter": "Credit cards", "theme": "violet",
            "props": {"eyebrow": "Credit utilisation", "title": "No active *credit card*",
                      "subtitle": "Cards are the easiest way to show steady, low utilisation.",
                      "items": [{"icon": "card", "title": "Start with a secured card", "text": "Issued against a fixed deposit, easy to get"},
                                {"icon": "calendar", "title": "Pay the full bill every month", "text": "Builds your record without interest"},
                                {"icon": "trend", "title": "Keep usage under 30%", "text": "Low utilisation lifts your score"}]},
            "sentences": [
                S("Next, credit cards.", "अब बात क्रेडिट कार्ड्स की।"),
                S("You don't have an active credit card right now.", "अभी आपके पास कोई एक्टिव क्रेडिट कार्ड नहीं है।"),
                S("A card used lightly and paid in full every month is one of the easiest ways to build a strong score.",
                  "एक कार्ड जिसे कम इस्तेमाल करके हर महीने पूरा बिल चुकाया जाए, मज़बूत स्कोर बनाने का सबसे आसान तरीका है।", B("item", 0)),
                S("If you are starting out, a secured card against a fixed deposit is easy to get.",
                  "अगर आप शुरुआत कर रहे हैं, तो फिक्स्ड डिपॉज़िट के बदले मिलने वाला सिक्योर्ड कार्ड आसानी से मिल जाता है।", B("item", 1)),
                S("Pay the full bill every month, and keep your usage under thirty percent of the limit.",
                  "हर महीने पूरा बिल चुकाइए, और खर्च को लिमिट के तीस प्रतिशत से कम रखिए।", B("item", 2)),
            ],
        }
    util = c["utilisation"] or 0.0
    tone = "good" if util <= 30 else "bad"
    bars = [{"label": f"{type_name(a['type'], 'screen')} · {lender(a['lender'], 'screen')}", "value": a["balance"], "max": a["limit"],
             "display": f"{money_screen(a['balance'])} of {money_screen(a['limit'])}",
             "tone": "bad" if a["limit"] and a["balance"] / a["limit"] > 0.3 else "good", "icon": "card"} for a in cards[:5]]
    worst = max(cards, key=lambda a: (a["balance"] / a["limit"]) if a["limit"] else 0)
    worst_pct = round(worst["balance"] / worst["limit"] * 100) if worst["limit"] else 0
    sentences = [
        S(f"Next, your credit cards: you have {len(cards)} active {plural(len(cards), 'card', 'cards')}, with a combined limit of {money(c['limit'], 'en')}.",
          f"अब आपके क्रेडिट कार्ड्स: आपके पास {len(cards)} एक्टिव कार्ड हैं, जिनकी कुल लिमिट {money(c['limit'], 'hi')} है।",
          *[B("item", i) for i in range(len(bars))]),
        S(f"Right now, the balance on them adds up to {money(c['used'], 'en')}, so your utilisation is {_num(util, 1)} percent.",
          f"अभी इन पर कुल {money(c['used'], 'hi')} का बैलेंस है, यानी आपका यूटिलाइज़ेशन {_num(util, 1)} प्रतिशत है।", B("summary")),
        S("Utilisation is the share of your limit that you use, and lenders like to see it below thirty percent, ideally below ten.",
          "यूटिलाइज़ेशन यानी आपकी लिमिट का कितना हिस्सा इस्तेमाल हो रहा है, और लेंडर्स चाहते हैं कि यह तीस प्रतिशत से कम, हो सके तो दस प्रतिशत से भी कम रहे।"),
    ]
    if util <= 10:
        sentences.append(S("Yours is very low, which is excellent for your score.", "आपका यूटिलाइज़ेशन बहुत कम है, जो स्कोर के लिए बेहतरीन है।"))
    elif util <= 30:
        sentences.append(S("Yours is within the healthy range.", "आपका यूटिलाइज़ेशन सही दायरे में है।"))
    else:
        sentences.append(S("Yours is on the high side, and paying down the balance before your statement date is one of the fastest ways to lift your score.",
                           "आपका यूटिलाइज़ेशन ज़्यादा है, और स्टेटमेंट डेट से पहले बैलेंस चुकाना स्कोर बढ़ाने का सबसे तेज़ तरीका है।"))
    if worst_pct > 30 and util <= 30:
        sentences.append(S(f"But one card is at {worst_pct} percent of its own limit, so try to spread your spending or pay it down before the statement date.",
                           f"लेकिन एक कार्ड अपनी लिमिट के {worst_pct} प्रतिशत पर है, इसलिए खर्च को बाँटें या स्टेटमेंट डेट से पहले पेमेंट कर दें।"))
    sentences.append(S("One more tip: avoid withdrawing cash on a credit card, because interest is charged from the very first day.",
                       "एक और टिप: क्रेडिट कार्ड से कैश निकालने से बचें, क्योंकि इस पर पहले ही दिन से ब्याज लगने लगता है।"))
    return {"id": "cards", "type": "bars", "chapter": "Credit cards", "theme": theme(tone),
            "props": {"eyebrow": "Credit utilisation", "title": "Your *credit cards*",
                      "subtitle": f"{len(cards)} active {plural(len(cards), 'card', 'cards')} · combined limit {money_screen(c['limit'])}",
                      "summary": {"value": f"{_num(util, 1)}%", "label": "Overall utilisation", "tone": tone, "hint": "Healthy: under 30%"},
                      "bars": bars},
            "sentences": sentences}


def ch_loans(p):
    loans = sorted([a for a in p["accounts"] if a["active"] and not a["is_card"]], key=lambda a: -a["balance"])
    if not loans:
        return None
    total = sum(a["balance"] for a in loans)
    rows, sentences = [], [
        S("Now, your active loans.", "अब आपके एक्टिव लोन।"),
        S(f"You have {len(loans)} active {plural(len(loans), 'loan', 'loans')}, with {money(total, 'en')} still to be repaid.",
          f"आपके {len(loans)} एक्टिव लोन हैं, जिन पर अभी {money(total, 'hi')} चुकाना बाकी है।")
        if total else
        S(f"You have {len(loans)} active {plural(len(loans), 'loan', 'loans')}, and nothing is outstanding on {plural(len(loans), 'it', 'them')} right now.",
          f"आपके {len(loans)} एक्टिव लोन हैं, और अभी इन पर कुछ भी बकाया नहीं है।"),
    ]
    shown = loans if len(loans) <= 5 else loans[:4]
    for i, a in enumerate(shown):
        bits_sub = [lender(a["lender"], "screen")] + ([f"since {month_screen(month_key(a['opened']))}"] if a["opened"] else []) \
            + ([f"{_num(a['interest_rate'], 2)}% interest"] if a["interest_rate"] else [])
        rows.append({"icon": type_name(a["type"], "icon"), "title": type_name(a["type"], "screen"), "sub": " · ".join(bits_sub),
                     "value": money_screen(a["balance"]), "value_sub": f"EMI {money_screen(a['installment'])}" if a["installment"] else "outstanding",
                     "tone": "bad" if a["overdue"] else "neutral"})
        extras_en = ([f"an EMI of {money(a['installment'], 'en')}"] if a["installment"] else []) \
            + ([f"an interest rate of {_num(a['interest_rate'], 2)} percent"] if a["interest_rate"] else [])
        extras_hi = ([f"ईएमआई {money(a['installment'], 'hi')}"] if a["installment"] else []) \
            + ([f"ब्याज दर {_num(a['interest_rate'], 2)} प्रतिशत"] if a["interest_rate"] else [])
        head_en = f"{an(type_name(a['type'])).capitalize()} from {lender(a['lender'])}"
        head_hi = f"{lender(a['lender'], 'hi')} से लिया गया {type_name(a['type'], 'hi')}"
        if a["balance"]:
            en = f"{head_en}, with {money(a['balance'], 'en')} outstanding" + (f", {' and '.join(extras_en)}." if extras_en else ".")
            hi = f"{head_hi}, जिस पर अभी {money(a['balance'], 'hi')} बाकी हैं" + (f", और जिसकी {' और '.join(extras_hi)} है।" if extras_hi else "।")
        else:
            en = f"{head_en}, with nothing outstanding right now."
            hi = f"{head_hi}, जिस पर अभी कुछ बाकी नहीं है।"
        if i < 2:
            sentences.append(S(en, hi, B("item", i)))
        else:  # further loans are shown on screen alongside the last one read out
            sentences[-1]["beats"].append(B("item", i))
    if len(loans) > len(shown):
        extra = loans[len(shown):]
        rows.append({"icon": "doc", "title": f"{len(extra)} more loans", "sub": "smaller balances",
                     "value": money_screen(sum(a["balance"] for a in extra)), "value_sub": "outstanding", "tone": "neutral"})
        sentences[-1]["beats"].append(B("item", len(rows) - 1))
    if len(loans) > 2:
        sentences.append(S(f"The other {len(loans) - 2} {plural(len(loans) - 2, 'loan is', 'loans are')} on screen.",
                           f"बाकी {len(loans) - 2} लोन स्क्रीन पर दिख रहे हैं।"))
    if any(a["secured"] is False and a["balance"] for a in loans):
        sentences.append(S("Unsecured loans like personal loans cost more than secured ones like home or gold loans, so if you ever prepay, start with the unsecured ones.",
                           "पर्सनल लोन जैसे अनसिक्योर्ड लोन, होम या गोल्ड लोन जैसे सिक्योर्ड लोन से महँगे होते हैं, इसलिए अगर कभी पहले चुकाएँ, तो शुरुआत अनसिक्योर्ड लोन से करें।"))
    if p["totals"]["emi"]:
        sentences.append(S(f"Your EMIs add up to about {money(p['totals']['emi'], 'en')} a month, so make sure your income covers them comfortably.",
                           f"आपकी कुल ईएमआई हर महीने लगभग {money(p['totals']['emi'], 'hi')} है, तो ध्यान रखें कि आपकी आमदनी इसे आराम से संभाल सके।"))
    return {"id": "loans", "type": "list", "chapter": "Active loans", "theme": "blue",
            "props": {"eyebrow": "Active loans", "title": "What you are *repaying*",
                      "subtitle": f"{len(loans)} active {plural(len(loans), 'loan', 'loans')} · {money_screen(total)} outstanding", "rows": rows},
            "sentences": sentences}


def month_key(d):
    return f"{d.year}-{d.month:02d}"


def ch_credit_age(p):
    age, recent = p["age"], p["recent"]
    oldest = age["oldest"]
    y = age["years"]
    tiles = [
        {"label": "Credit history", "value": f"{age['years']}y {age['months']}m", "sub": "since your first account", "icon": "clock"},
        {"label": "Average account age", "value": f"{age['avg_years']}y {age['avg_months']}m", "sub": "across all accounts", "icon": "calendar"},
        {"label": "Oldest account", "value": type_name(oldest["type"], "screen") if oldest else "—", "icon": "doc"},
        {"label": "New in last 6 months", "value": str(recent["new_accounts_6m"]), "sub": "accounts opened", "icon": "bolt",
         "tone": "good" if recent["new_accounts_6m"] <= 1 else "warn"},
    ]
    sentences = [
        S("Let's talk about the age of your credit.", "अब बात आपकी क्रेडिट की उम्र की।"),
        S(f"Your credit history goes back {years_months(age['years'], age['months'], 'en')}"
          + (f", and your oldest account is a {type_name(oldest['type'])}." if oldest else "."),
          f"आपकी क्रेडिट हिस्ट्री {years_months(age['years'], age['months'], 'hi')} पुरानी है"
          + (f", और आपका सबसे पुराना अकाउंट एक {type_name(oldest['type'], 'hi')} है।" if oldest else "।"),
          B("item", 0), B("item", 2)),
        S(f"On average, your accounts are {years_months(age['avg_years'], age['avg_months'], 'en')} old.",
          f"औसतन आपके अकाउंट {years_months(age['avg_years'], age['avg_months'], 'hi')} पुराने हैं।", B("item", 1)),
        S("That is a long, established history, and it works strongly in your favour.",
          "यह एक लंबी और मज़बूत हिस्ट्री है, जो आपके पक्ष में जाती है।") if y >= 7 else
        S("That is a reasonably established history, and it will keep getting stronger with time.",
          "यह ठीक-ठाक पुरानी हिस्ट्री है, और समय के साथ यह और मज़बूत होती जाएगी।") if y >= 3 else
        S("Your history is still young, and time is the only thing that grows it.",
          "आपकी हिस्ट्री अभी नई है, और इसे सिर्फ़ समय ही बढ़ा सकता है।"),
        S(f"You opened {recent['new_accounts_6m']} new {plural(recent['new_accounts_6m'], 'account', 'accounts')} in the last six months, and every new account lowers your average age a little.",
          f"पिछले छह महीनों में आपने {recent['new_accounts_6m']} नए अकाउंट खोले हैं, और हर नया अकाउंट आपकी औसत उम्र को थोड़ा कम कर देता है।", B("item", 3))
        if recent["new_accounts_6m"] else
        S("You haven't opened any new accounts in the last six months, which keeps your average age steady.",
          "पिछले छह महीनों में आपने कोई नया अकाउंट नहीं खोला, जिससे आपकी औसत उम्र स्थिर बनी रहती है।", B("item", 3)),
        S("This is why it is usually better to keep your oldest credit card open, even if you rarely use it.",
          "इसीलिए अपना सबसे पुराना क्रेडिट कार्ड बंद न करना आमतौर पर बेहतर होता है, भले ही आप उसे कम इस्तेमाल करें।"),
    ]
    return {"id": "credit_age", "type": "stats", "chapter": "Credit age", "theme": "green" if y >= 3 else "violet",
            "props": {"eyebrow": "Length of history", "title": "The age of *your credit*", "tiles": tiles},
            "sentences": sentences}


def ch_credit_mix(p):
    t = p["totals"]
    lenders = p["lenders"].most_common(4)
    bars = [{"label": "Secured", "value": t["secured"], "display": str(t["secured"]), "sub": "backed by an asset", "icon": "shield", "group": "By security"},
            {"label": "Unsecured", "value": t["unsecured"], "display": str(t["unsecured"]), "sub": "no collateral", "icon": "wallet", "group": "By security"}]
    bars += [{"label": lender(l, "screen"), "value": n, "display": str(n), "icon": "doc", "group": "By lender"} for l, n in lenders]
    share = t["secured"] / t["accounts"] if t["accounts"] else 0
    names_en = [re.sub(r"y$", "ie", lender(l).split(" ", 1)[1]) + "s" for l, _ in lenders]
    names_hi = [lender(l, "hi") for l, _ in lenders]
    sentences = [
        S("Next, your credit mix.", "अब आपका क्रेडिट मिक्स।"),
        S(f"{t['secured']} of your {t['accounts']} accounts are secured against an asset, and {t['unsecured']} are unsecured.",
          f"आपके {t['accounts']} अकाउंट्स में से {t['secured']} सिक्योर्ड हैं, यानी किसी संपत्ति के बदले, और {t['unsecured']} अनसिक्योर्ड हैं।",
          B("item", 0), B("item", 1)),
        S("All of your credit is unsecured, so adding a secured product later, like a gold loan or a card against a fixed deposit, would round out your profile.",
          "आपका सारा क्रेडिट अनसिक्योर्ड है, इसलिए आगे चलकर गोल्ड लोन या एफडी पर मिलने वाले कार्ड जैसा कोई सिक्योर्ड प्रोडक्ट आपकी प्रोफ़ाइल को और संतुलित बना सकता है।")
        if t["secured"] == 0 else
        S(f"Most of your credit is unsecured, which is common, and the secured {plural(t['secured'], 'account', 'accounts')} in the mix {plural(t['secured'], 'shows', 'show')} you can handle different kinds of credit.",
          "आपका ज़्यादातर क्रेडिट अनसिक्योर्ड है, जो आम बात है, और मिक्स में सिक्योर्ड अकाउंट होना दिखाता है कि आप अलग-अलग तरह का क्रेडिट संभाल सकते हैं।")
        if share < 0.15 else
        S("Having both kinds shows lenders that you can handle different types of credit responsibly.",
          "दोनों तरह का क्रेडिट होना लेंडर्स को दिखाता है कि आप अलग-अलग तरह का क्रेडिट ज़िम्मेदारी से संभाल सकते हैं।"),
        S(f"You have borrowed from {len(lenders)} kinds of lenders: {', '.join(names_en[:-1]) + ' and ' + names_en[-1] if len(names_en) > 1 else names_en[0]}.",
          f"आपने {len(lenders)} तरह के लेंडर्स से क्रेडिट लिया है: {', '.join(names_hi[:-1]) + ' और ' + names_hi[-1] if len(names_hi) > 1 else names_hi[0]}।",
          *[B("item", 2 + i) for i in range(len(lenders))]),
    ]
    return {"id": "credit_mix", "type": "bars", "chapter": "Credit mix", "theme": "blue",
            "props": {"eyebrow": "Credit mix", "title": "Secured, unsecured and *who lent*", "bars": bars},
            "sentences": sentences}


def ch_enquiries(p):
    enq, n6 = p["enquiries"], p["recent"]["enquiries_6m"]
    rows = [{"icon": "search", "title": q["purpose"], "sub": f"{lender(q['lender'], 'screen')} · {date_screen(q['date'])}",
             "value": money_screen(q["amount"]) if q["amount"] else "—", "value_sub": "amount asked" if q["amount"] else "",
             "tone": "neutral"} for q in enq[:4]]
    tone = "good" if n6 <= 1 else "warn" if n6 <= 3 else "bad"
    sentences = [
        S("Now, credit enquiries: every time you apply for a loan or a card, the lender checks your report, and that check is recorded as a hard enquiry.",
          "अब क्रेडिट इन्क्वायरीज़: जब भी आप किसी लोन या कार्ड के लिए अप्लाई करते हैं, लेंडर आपकी रिपोर्ट चेक करता है, और इसे हार्ड इन्क्वायरी के रूप में दर्ज किया जाता है।"),
        S(f"Your report lists {len(enq)} {plural(len(enq), 'enquiry', 'enquiries')}, and {n6} of them {plural(n6, 'is', 'are')} from the last six months.",
          f"आपकी रिपोर्ट में {len(enq)} इन्क्वायरीज़ हैं, जिनमें से {n6} पिछले छह महीनों की हैं।", *[B("item", i) for i in range(len(rows))]),
    ]
    if enq and enq[0]["date"]:
        q = enq[0]
        other = q["purpose"].lower() == "other" or q["purpose"] not in TYPE_NAMES
        purpose_en = "another purpose" if other else an(q["purpose"].lower())
        purpose_hi = "किसी और ज़रूरत" if other else type_name(q["purpose"], "hi")
        sentences.append(S(f"The most recent was on {date_speech(q['date'], 'en')}, for {purpose_en}"
                           + (f" of {money(q['amount'], 'en')}." if q["amount"] else "."),
                           f"सबसे हाल की इन्क्वायरी {date_speech(q['date'], 'hi')} को {purpose_hi} के लिए थी"
                           + (f", {money(q['amount'], 'hi')} की।" if q["amount"] else "।")))
    sentences.append(
        S("That is a low, healthy number.", "यह एक कम और अच्छा नंबर है।") if n6 <= 1 else
        S("That is moderate, so try to space out any new applications.", "यह ठीक-ठाक है, लेकिन नए अप्लिकेशन के बीच थोड़ा अंतर रखें।") if n6 <= 3 else
        S("That is on the high side, because many applications in a short time can make lenders think you urgently need credit.",
          "यह थोड़ा ज़्यादा है, क्योंकि कम समय में कई अप्लिकेशन से लेंडर्स को लग सकता है कि आपको तुरंत पैसों की ज़रूरत है।"))
    sentences.append(S("Checking your own score, like you are doing now, is a soft enquiry, and it never lowers your score.",
                       "अपना खुद का स्कोर चेक करना, जैसा आप अभी कर रहे हैं, एक सॉफ्ट इन्क्वायरी है, और इससे आपका स्कोर कभी कम नहीं होता।", B("note")))
    return {"id": "enquiries", "type": "list", "chapter": "Enquiries", "theme": theme(tone),
            "props": {"eyebrow": "Credit enquiries", "title": "Who checked *your report*",
                      "subtitle": f"{n6} in the last 6 months · {len(enq)} in total",
                      "rows": rows, "note": {"icon": "info", "text": "Checking your own score is a soft enquiry and never lowers it."}},
            "sentences": sentences}


def ch_red_flags(p):
    f = p["flags"]
    rows = [
        {"title": "Written-off accounts", "value": str(f["written_off"]), "value_sub": money_screen(f["written_off_amount"]) if f["written_off_amount"] else "", "ok": not f["written_off"]},
        {"title": "Settled accounts", "value": str(f["settled"]), "value_sub": money_screen(f["settled_amount"]) if f["settled_amount"] else "", "ok": not f["settled"]},
        {"title": "Restructured loans", "value": str(f["restructured"]), "ok": not f["restructured"]},
        {"title": "Suit filed / wilful default", "value": str(f["suit_or_wilful"]), "ok": not f["suit_or_wilful"]},
        {"title": "Accounts in dispute", "value": str(f["disputed"]), "ok": not f["disputed"]},
    ]
    for r in rows:
        r.update(icon="check" if r["ok"] else "alert", tone="good" if r["ok"] else "bad")
    clean = all(r["ok"] for r in rows[:4])
    problems_en = [txt for n, txt in ((f["written_off"], "write-offs"), (f["settled"], "settlements"),
                                      (f["restructured"], "restructured loans"), (f["suit_or_wilful"], "legal cases")) if n]
    problems_hi = [txt for n, txt in ((f["written_off"], "राइट-ऑफ़"), (f["settled"], "सेटलमेंट"),
                                      (f["restructured"], "रीस्ट्रक्चर्ड लोन"), (f["suit_or_wilful"], "कानूनी मामले")) if n]
    sentences = [
        S("Now, a check for the most serious negative marks, the kind that can hurt a score for years.",
          "अब एक जाँच उन सबसे गंभीर निशानों की, जो सालों तक स्कोर को नुकसान पहुँचा सकते हैं।"),
        S("A write-off means a lender gave up on recovering a loan, and a settlement means a loan was closed by paying less than what was owed.",
          "राइट-ऑफ़ का मतलब है कि लेंडर ने लोन की वसूली की उम्मीद छोड़ दी, और सेटलमेंट का मतलब है कि लोन पूरी रकम से कम चुकाकर बंद किया गया।",
          B("item", 0), B("item", 1)),
        S("A restructured loan had its terms changed because of repayment trouble, and a suit filed or wilful default means the lender went to court.",
          "रीस्ट्रक्चर्ड लोन वह है जिसकी शर्तें पेमेंट में दिक्कत की वजह से बदली गईं, और सूट फ़ाइल्ड या विलफुल डिफ़ॉल्ट का मतलब है कि लेंडर कोर्ट तक गया।",
          B("item", 2), B("item", 3)),
        S("The good news is that your report has none of them: no write-offs, no settlements, no restructuring and no legal cases.",
          "अच्छी खबर यह है कि आपकी रिपोर्ट में इनमें से कुछ भी नहीं है: न राइट-ऑफ़, न सेटलमेंट, न रीस्ट्रक्चरिंग और न कोई कानूनी मामला।")
        if clean else
        S(f"Your report does show {' and '.join(problems_en)}, and resolving these with the lender matters more than anything else.",
          f"आपकी रिपोर्ट में {' और '.join(problems_hi)} दिख रहे हैं, और लेंडर के साथ इन्हें सुलझाना सबसे ज़्यादा ज़रूरी है।"),
        S("There are also no accounts under dispute.", "कोई भी अकाउंट डिस्प्यूट में भी नहीं है।", B("item", 4))
        if not f["disputed"] else
        S(f"{f['disputed']} of your accounts {plural(f['disputed'], 'is', 'are')} marked as under dispute.",
          f"आपके {f['disputed']} अकाउंट डिस्प्यूट में दर्ज हैं।", B("item", 4)),
    ]
    return {"id": "red_flags", "type": "list", "chapter": "Serious marks", "theme": "green" if clean else "amber",
            "props": {"eyebrow": "Serious marks check", "title": "Anything *serious*?",
                      "subtitle": "The marks that hurt a score the most.", "rows": rows},
            "sentences": sentences}


def ch_identity(p):
    i = p["identity"]
    tiles = [{"label": "Name versions", "value": str(i["names"]), "icon": "doc"},
             {"label": "Addresses", "value": str(i["addresses"]), "icon": "home"},
             {"label": "Phone numbers", "value": str(i["phones"]), "icon": "phone"},
             {"label": "Email addresses", "value": str(i["emails"]), "icon": "mail"}]
    return {
        "id": "identity", "type": "stats", "chapter": "Your details", "theme": "violet",
        "props": {"eyebrow": "Details on file", "title": "Your *personal details*", "tiles": tiles,
                  "note": {"icon": "shield", "text": "For your privacy, this video never shows your PAN, phone numbers or addresses."}},
        "sentences": [
            S("Your report also records the personal details that lenders have sent over the years.",
              "आपकी रिपोर्ट में वे निजी जानकारियाँ भी होती हैं जो सालों में लेंडर्स ने भेजी हैं।"),
            S(f"For you, CRIF has {i['names']} versions of your name, {i['addresses']} addresses, {i['phones']} phone numbers and {i['emails']} email addresses on file.",
              f"आपके लिए क्रिफ़ के पास आपके नाम के {i['names']} रूप, {i['addresses']} पते, {i['phones']} फ़ोन नंबर और {i['emails']} ईमेल पते दर्ज हैं।",
              B("item", 0), B("item", 1), B("item", 2), B("item", 3)),
            S("If you ever spot an account or a detail that is not yours, you can raise a dispute with CRIF High Mark, and they will check it with the lender.",
              "अगर आपको कोई ऐसा अकाउंट या जानकारी दिखे जो आपकी नहीं है, तो आप क्रिफ़ हाई मार्क के पास डिस्प्यूट दर्ज कर सकते हैं, और वे लेंडर से इसकी जाँच करेंगे।"),
            S("For your privacy, this video never shows your PAN, phone numbers or addresses.",
              "आपकी प्राइवेसी के लिए, इस वीडियो में आपका पैन, फ़ोन नंबर या पता कभी नहीं दिखाया जाता।", B("note")),
        ],
    }


def action_steps(p):
    t, pay, c, recent = p["totals"], p["payments"], p["cards"], p["recent"]
    steps = []
    if t["overdue"]:
        steps.append(({"icon": "wallet", "title": f"Clear the {money_screen(t['overdue'])} overdue", "text": "Overdue amounts are reported as late every month."},
                      S(f"clear the overdue amount of {money(t['overdue'], 'en')} as soon as you can, because it is reported as late every single month.",
                        f"जितनी जल्दी हो सके {money(t['overdue'], 'hi')} का ओवरड्यू चुकाइए, क्योंकि यह हर महीने लेट के रूप में दर्ज होता है।")))
    if pay["late"]:
        steps.append(({"icon": "bolt", "title": "Turn on auto-debit everywhere", "text": "Every EMI and card bill paid on the due date."},
                      S("set up auto-debit for every EMI and card bill, so that no payment is ever late again.",
                        "हर ईएमआई और कार्ड बिल के लिए ऑटो-डेबिट सेट कीजिए, ताकि कोई भी पेमेंट दोबारा लेट न हो।")))
    if c["count"] and (c["utilisation"] or 0) > 30:
        steps.append(({"icon": "card", "title": "Bring card usage under 30%", "text": "Pay before the statement date to lower the reported balance."},
                      S("bring your card usage below thirty percent by paying before the statement date.",
                        "स्टेटमेंट डेट से पहले पेमेंट करके अपने कार्ड का इस्तेमाल तीस प्रतिशत से नीचे लाइए।")))
    if recent["enquiries_6m"] > 2 or recent["new_accounts_6m"] > 1:
        steps.append(({"icon": "search", "title": "Pause new applications", "text": "Give it six months before applying for more credit."},
                      S("pause new loan and card applications for about six months, to let your enquiries settle.",
                        "करीब छह महीने तक नए लोन और कार्ड के लिए अप्लाई करना रोक दीजिए, ताकि इन्क्वायरीज़ का असर कम हो सके।")))
    if not c["count"]:
        steps.append(({"icon": "card", "title": "Consider a secured credit card", "text": "Use it lightly and pay the full bill every month."},
                      S("consider a secured credit card, use it lightly, and pay the full bill every month.",
                        "एक सिक्योर्ड क्रेडिट कार्ड लेने पर विचार कीजिए, उसे कम इस्तेमाल कीजिए, और हर महीने पूरा बिल चुकाइए।")))
    fillers = [
        ({"icon": "calendar", "title": "Keep every payment on time", "text": "Your payment record is the biggest part of your score."},
         S("keep paying every EMI and card bill on time, because your payment record is the biggest part of your score.",
           "हर ईएमआई और कार्ड बिल समय पर भरते रहिए, क्योंकि आपका पेमेंट रिकॉर्ड स्कोर का सबसे बड़ा हिस्सा है।")),
        ({"icon": "shield", "title": "Keep your oldest accounts open", "text": "They lengthen your credit history."},
         S("keep your oldest accounts open, because they lengthen your credit history.",
           "अपने सबसे पुराने अकाउंट खुले रखिए, क्योंकि इनसे आपकी क्रेडिट हिस्ट्री लंबी होती है।")),
        ({"icon": "doc", "title": "Check your report every few months", "text": "Spot errors early and dispute them."},
         S("check your report every few months, so you can spot and dispute any error early.",
           "हर कुछ महीनों में अपनी रिपोर्ट चेक कीजिए, ताकि कोई भी गलती जल्दी पकड़कर उसे ठीक करवा सकें।")),
    ]
    for f in fillers:
        if len(steps) >= 4:
            break
        steps.append(f)
    return steps[:4]


def ch_action_plan(p):
    score = p["score"]
    target = next((m for m in (750, 800, 850, 900) if m > score), 900)
    steps = action_steps(p)
    sentences = [
        S("Let's bring it all together into a simple action plan.", "अब इन सब बातों को एक आसान एक्शन प्लान में समेटते हैं।"),
        S(f"Your score today is {score}, and a realistic next milestone is {target}.",
          f"आज आपका स्कोर {score} है, और अगला सही लक्ष्य {target} है।") if target > score else
        S(f"Your score today is {score}, right at the top of the scale.", f"आज आपका स्कोर {score} है, जो सबसे ऊपर के स्तर पर है।"),
    ]
    for i, (_, s) in enumerate(steps):
        sentences.append(S(f"{ORDINALS[i][0]}, {s['en']}", f"{ORDINALS[i][1]}, {s['hi']}", B("step", i)))
    sentences.append(S("None of this happens overnight, because scores update as lenders report each month, but steady habits always show up in your score.",
                       "यह सब रातों-रात नहीं होता, क्योंकि स्कोर हर महीने लेंडर्स की रिपोर्ट के साथ बदलता है, लेकिन अच्छी आदतें स्कोर में हमेशा दिखती हैं।"))
    return {"id": "action_plan", "type": "action_plan", "chapter": "Action plan", "theme": "blue",
            "props": {"eyebrow": "Action plan", "title": f"Your path to *{target}+*", "current": score, "target": target,
                      "target_label": f"{target}+", "range": [max(300, (score // 50) * 50 - 50), min(900, target + 50)],
                      "gap_label": f"{target - score} points to go" if target > score else "Top of the scale", "steps": [s for s, _ in steps],
                      "note": "Scores update every month as lenders report your activity."},
            "sentences": sentences}


def recap_rows(p):
    t, pay, c, n6 = p["totals"], p["payments"], p["cards"], p["recent"]["enquiries_6m"]
    b = band(p["score"])
    rows = [
        {"icon": "trend", "title": "Credit score", "value": f"{p['score']} · {b['label']}", "tone": score_tone(p["score"])},
        {"icon": "calendar", "title": "Paid on time", "value": f"{_num(pay['on_time_pct'] or 0, 1)}%",
         "tone": "good" if (pay["on_time_pct"] or 0) >= 97 else "warn"},
        {"icon": "card", "title": "Card utilisation", "value": f"{_num(c['utilisation'] or 0, 1)}%" if c["count"] else "No active card",
         "tone": "neutral" if not c["count"] else "good" if (c["utilisation"] or 0) <= 30 else "bad"},
        {"icon": "clock", "title": "Credit history", "value": f"{p['age']['years']}y {p['age']['months']}m", "tone": "good" if p["age"]["years"] >= 3 else "neutral"},
        {"icon": "search", "title": "Enquiries, 6 months", "value": str(n6), "tone": "good" if n6 <= 1 else "warn" if n6 <= 3 else "bad"},
        {"icon": "alert" if t["overdue"] else "check", "title": "Overdue now", "value": money_screen(t["overdue"]) if t["overdue"] else "None",
         "tone": "bad" if t["overdue"] else "good"},
    ]
    return rows


def ch_closing(p, name):
    t, pay, c = p["totals"], p["payments"], p["cards"]
    rows = recap_rows(p)
    en_name, hi_name = (f", {name}" if name else ""), (f" {name}" if name else "")
    if t["overdue"]:
        focus = S("The one thing to fix first is the overdue amount.", "सबसे पहले जिस चीज़ को ठीक करना है, वह है ओवरड्यू रकम।")
    elif pay["late"]:
        focus = S("The one thing to focus on is paying every single bill on time.", "जिस एक चीज़ पर ध्यान देना है, वह है हर बिल समय पर भरना।")
    elif c["count"] and (c["utilisation"] or 0) > 30:
        focus = S("The one thing to focus on is bringing your card usage down.", "जिस एक चीज़ पर ध्यान देना है, वह है कार्ड का इस्तेमाल कम करना।")
    else:
        focus = S("Your job now is simply to keep these good habits going.", "अब आपको बस इन अच्छी आदतों को बनाए रखना है।")
    focus["beats"] = [B("item", 3), B("item", 4), B("item", 5)]
    return {
        "id": "closing", "type": "list", "chapter": "Summary", "theme": "blue",
        "props": {"eyebrow": "Summary", "title": "Your report *in one view*", "rows": rows},
        "sentences": [
            S(f"So{en_name}, here is your report in one view.", f"तो{hi_name}, यह रही आपकी पूरी रिपोर्ट एक नज़र में।"),
            S(f"Your score is {p['score']}, {_num(pay['on_time_pct'] or 0, 1)} percent of your payments were on time, and your credit history is {years_months(p['age']['years'], p['age']['months'], 'en')} long.",
              f"आपका स्कोर {p['score']} है, आपके {_num(pay['on_time_pct'] or 0, 1)} प्रतिशत पेमेंट समय पर हुए हैं, और आपकी क्रेडिट हिस्ट्री {years_months(p['age']['years'], p['age']['months'], 'hi')} की है।",
              B("item", 0), B("item", 1), B("item", 2)),
            focus,
            S(f"Thank you for watching{en_name}.", f"देखने के लिए धन्यवाद{hi_name}!"),
        ],
    }


def build_chapters(p, name):
    chapters = [ch_welcome(p, name), ch_score(p), ch_how_scored(), ch_snapshot(p), ch_account_types(p), ch_dpd_explained(),
                ch_payment_record(p), ch_payment_grid(p), ch_late_payments(p), ch_overdue(p), ch_cards(p), ch_loans(p),
                ch_credit_age(p), ch_credit_mix(p), ch_enquiries(p), ch_red_flags(p), ch_identity(p), ch_action_plan(p),
                ch_closing(p, name)]
    return [c for c in chapters if c]


# ------------------------------------------------------------------------------------ story

def report_summary(p) -> dict:
    """Non-identifying summary stored with the story (and used for its cache key)."""
    return {"source": "crif", "name": p["name"], "score": p["score"], "as_of": str(p["as_of"]),
            "accounts": p["totals"]["accounts"], "active": p["totals"]["active"], "overdue": p["totals"]["overdue"],
            "on_time_pct": p["payments"]["on_time_pct"], "utilisation": p["cards"]["utilisation"],
            "enquiries_6m": p["recent"]["enquiries_6m"]}


def story_id(chapters, languages, speed) -> str:
    payload = json.dumps({"schema": SCHEMA_VERSION, "languages": languages, "speed": speed,
                          "script": [[s["en"] for s in c["sentences"]] + [json.dumps(c["props"], sort_keys=True, default=str)] for c in chapters]},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def prepare(report_json: Any, customer_name: Optional[str], languages: List[str], speed: float):
    p = parse_report(report_json)
    name = (customer_name or p["name"] or "").strip()
    chapters = build_chapters(p, name)
    langs = [l for l in dict.fromkeys(languages) if l in VOICES] or ["hi"]
    return p, name, chapters, langs, story_id(chapters, langs, speed)


async def generate_crif_stories(p, name, chapters, langs, speed, sid, json_path: Callable[[str], Path],
                                audio_path: Callable[[str], Path]) -> Dict[str, dict]:
    slots = asyncio.Semaphore(8)
    t = p["totals"]

    async def one(lang):
        duration, timings = await narrate_chapters(chapters, lang, VOICES[lang], speed, audio_path(lang), slots)
        minutes = max(1, round(duration / 60))
        story = {
            "schema_version": SCHEMA_VERSION,
            "story_id": sid,
            "title": f"Detailed credit report · {name or 'Customer'} · {date_screen(p['as_of'])}",
            "language": lang,
            "languages": [{"code": c, "label": LANGUAGE_LABELS[c], "src": json_path(c).name} for c in langs],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "brand": BRAND,
            "audio": {"src": audio_path(lang).name, "duration": duration, "voice": VOICES[lang], "speed": speed, "engine": "edge-neural"},
            "player": {"captions": True},
            "report_summary": report_summary(p),
            "intro": {
                "title": f"Hi {name}, your *detailed credit report* is ready" if name else "Your *detailed credit report* is ready",
                "subtitle": f"A {minutes}-minute guided walkthrough of your CRIF High Mark report: your score, all {t['accounts']} accounts, your payment record and a plan to improve.",
                "badges": [{"icon": "shield", "text": "100% Secure & Private"}, {"icon": "check", "text": "No impact on your score"}],
            },
            "scenes": assemble_scenes(chapters, timings, duration),
            "end_card": {
                "title": "That's your *complete report*",
                "text": f"Data from CRIF High Mark, as of {date_screen(p['as_of'])}.",
                "recap": [{"label": r["title"], "value": r["value"], "tone": r["tone"]} for r in recap_rows(p)],
                "primary_label": "Replay story", "secondary_label": "Close",
                "disclaimer": "This video is for educational purposes only and should not be construed as financial advice.",
            },
            "captions": captions_from(timings),
        }
        out = json_path(lang)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return lang, story

    return dict(await asyncio.gather(*(one(lang) for lang in langs)))
