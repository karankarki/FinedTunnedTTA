# Credit Story Player

An animated, narrated credit report video in the Oct Credit visual style. It can be made two ways:

- **Detailed walkthrough (main flow):** send a **CRIF High Mark report** exactly as the bureau API returns it, and you get an 8–14 minute video covering every part of the report. Each chapter explains the concept first, then walks through the customer's own numbers.
- **Quick summary:** a 1–2 minute video from a handful of fields (score, payments, cards, enquiries).

Either way, the backend writes the narration, synthesizes it in Hindi and/or English, and returns a **story JSON**. The player then animates that story in sync with the voice.

```
app/crif_report.py        # CRIF High Mark response -> plain facts (no PAN/phones/addresses/account numbers)
app/crif_story.py         # facts -> 19-chapter script in English + Hindi -> story JSON
app/story_engine.py       # narration engine (edge-tts + word timings) and the quick-summary story
app/story_routes.py       # POST /api/story/crif, POST /api/story, serves /stories/<id>/ and /player/
frontend/
├── index.html            # builder panel + player
├── builder.js            # CRIF upload tab, quick form, JSON tab; calls the API
├── player.js             # timeline engine + scene renderers (window.StoryPlayer)
├── player.css            # Oct Credit tokens: Manrope, #1677ff accent; black (default) and light themes
├── icons.js              # line icons referenced by name from the story JSON
├── config.js             # default story + API location
├── data/, audio/         # bundled sample story (Karan, 776), shown on first load
└── tools/generate_story.py  # regenerate the bundled sample from the command line
```

## Run it

The backend serves the page, the API and the generated stories:

```bash
./start.sh
# open http://localhost:8000/player/
```

On the **CRIF report** tab, drop the CRIF response file (or paste its JSON). A summary card confirms it's the right report. Pick the languages and speed, then press **Generate video**. A detailed walkthrough takes about 15–30 seconds to generate for both languages. The same report again within 24 hours comes back instantly. The address bar gets `?story=…`, so the link reopens that exact video until it expires.

## Detailed walkthrough from a CRIF report

**Input:** the CRIF High Mark API response as-is (`{"statusCode":200,"data":{"crifReport":{"INDV-REPORT-FILE":…}}}`). The parser also accepts just `data`, `crifReport` or the `INDV-REPORT` itself.

```bash
curl -X POST "http://localhost:8000/api/story/crif?languages=hi,en" \
  -H "Content-Type: application/json" --data-binary @WaseemCrifResponse
```

Options go in the query string (`languages=hi,en`, `customer_name=Waseem`, `voice_speed=1.05`), or you can wrap the report: `{"report": {…}, "languages": ["en"], "customer_name": "Waseem", "voice_speed": 1.0}`. The response has the same shape as `/api/story` below, plus `summary` and the list of `chapters`. A body that isn't a CRIF report returns HTTP 422.

**What the video covers.** Chapters that don't apply are left out; for example, "Overdue now" only appears if something is overdue.

| # | Chapter | From the report | Explained |
| --- | --- | --- | --- |
| 1 | Welcome | name, account count, history length, score | what a bureau is, CRIF High Mark, monthly reporting |
| 2 | Your score | `SCORES` | the 300–900 scale, what 750+ means, how lenders see the score |
| 3 | How scores work | — | the five scoring factors and their impact |
| 4 | At a glance | `ACCOUNTS-SUMMARY` | total / active / closed, outstanding, sanctioned, overdue, monthly EMIs |
| 5 | Types of credit | `ACCT-TYPE` per account | what each loan / card type is |
| 6 | Reading payments | — | DPD, and the STD / SMA / SUB / DBT / LSS asset classes |
| 7 | Payment record | `COMBINED-PAYMENT-HISTORY` (36 months) | on-time %, last 6 months |
| 8 | Month by month | payment history, last 12 months | grid per account: on time / 1–29 / 30+ days late |
| 9 | Late payments | delays by account, older records | how late payments fade over time |
| 10 | Overdue now | `OVERDUE-AMT` | why overdue is the top priority |
| 11 | Credit cards | limits and balances | utilisation, 30% / 10% guidance, cash withdrawals |
| 12 | Active loans | balance, EMI, interest rate | secured vs unsecured cost, EMI load |
| 13 | Credit age | history length, average age, new accounts | why old accounts matter |
| 14 | Credit mix | secured / unsecured, lender types | why a mix helps |
| 15 | Enquiries | `INQUIRY-HISTORY` | hard vs soft enquiries |
| 16 | Serious marks | write-offs, settlements, restructuring, suits, disputes | what each one means |
| 17 | Your details | counts of name / address / phone / email variations | how to dispute errors with CRIF |
| 18 | Action plan | derived from all of the above | up to 4 prioritised steps, next score milestone |
| 19 | Summary | — | recap |

**Length:** measured at the default speed of 1.05×, a clean 15-account report runs 8.8 minutes in English and 10.5 in Hindi. A 33-account report with overdue amounts runs 11.4 and 13.4 minutes. A faster voice speed shortens it.

**Privacy:** only counts, amounts, account types, lender categories (bank / NBFC / …) and the customer's first name make it into the video and the stored story files. PAN, phone numbers, addresses, emails, dates of birth, account numbers and the `crifPDF` link are never copied out of the report, and the raw report isn't stored. CRIF's score-factor codes (`SF03|SF11|…`) aren't interpreted, because their meanings need CRIF's reference table.

**Retention:** every generated video (its story JSON and audio in `outputs/stories/<story_id>/`) is deleted 24 hours after it was generated. A cleanup runs when the server starts, every 15 minutes, and before each generation request, so an expired video is always rebuilt rather than served. API responses include `expires_at`, and opening an expired link shows an "expired, generate it again" message. Change the window with the `STORY_TTL_HOURS` environment variable, e.g. `STORY_TTL_HOURS=1 ./start.sh`.

## Quick summary: the input JSON

This is everything the video is generated from. It is the body of `POST /api/story`, and exactly what the JSON tab shows:

```json
{
  "customer_name": "Karan",
  "customer_name_hi": "करण",
  "credit_score": 776,
  "score_bureau": "CIBIL",
  "report_month": "2026-09",
  "on_time_repayment_pct": 100,
  "missed_payments_count": 0,
  "active_credit_cards": 0,
  "credit_utilization_pct": 0,
  "recent_inquiries": 0,
  "languages": ["hi", "en"],
  "voice_speed": 0.92
}
```

| Field | Required | Allowed | Notes |
| --- | --- | --- | --- |
| `credit_score` | yes | 300–900 | |
| `customer_name` | no (form requires it) | 1–40 chars | Shown on screen and spoken in English. API default: `Customer`. |
| `customer_name_hi` | no | up to 40 chars | Name as spoken in the Hindi narration, e.g. `करण`. Defaults to `customer_name`. |
| `score_bureau` | no | text | Default `CIBIL`. The form offers CIBIL, Experian, Equifax and CRIF High Mark. |
| `report_month` | no | `YYYY-MM` | Default: the current month. Drives "September 2026" and the 6-month payment strip. |
| `on_time_repayment_pct` | no | 0–100 | Default 100. |
| `missed_payments_count` | no | 0–36 | Missed payments in the last 6 months. Default 0. |
| `active_credit_cards` | no | 0–30 | Default 0. With 0 cards the utilisation chapter shows the "no cards" state. |
| `credit_utilization_pct` | no | 0–100 | Ignored when there are no active cards. |
| `recent_inquiries` | no | 0–50 | New loan/card applications in the last 6 months. Default 0. |
| `languages` | no | `hi`, `en` | Narrations to generate. The first one plays first. Default `["hi", "en"]`. |
| `voice_speed` | no | 0.7–1.3 | Default 0.92. |

### From your own system

```bash
curl -X POST http://localhost:8000/api/story -H "Content-Type: application/json" \
  -d '{"customer_name":"Priya","credit_score":665,"missed_payments_count":2,"active_credit_cards":2,"credit_utilization_pct":45,"recent_inquiries":2,"languages":["en","hi"]}'
```

```json
{
  "story_id": "50beaf4fbce6d554",
  "cached": false,
  "story_url": "/stories/50beaf4fbce6d554/story.en.json",
  "languages": [
    { "code": "en", "label": "English", "story_url": "/stories/50beaf4fbce6d554/story.en.json" },
    { "code": "hi", "label": "हिंदी", "story_url": "/stories/50beaf4fbce6d554/story.hi.json" }
  ],
  "input": { "...": "the normalised input" }
}
```

Open `http://localhost:8000/player/?story=/stories/50beaf4fbce6d554/story.en.json` to play it. Invalid input returns HTTP 422 with one entry per bad field. If the voice service can't be reached, you get HTTP 502. Generated files live in `outputs/stories/<story_id>/`.

## Other ways to play a story

- **Theme:** black by default. Set `theme: 'light'` in [`config.js`](config.js), or add `?theme=light` to the address for one visit. All colours are CSS tokens at the top of `player.css`, one set per theme.
- **Default story:** `storyUrl` in [`config.js`](config.js). `apiBase` there points at the backend when the page is served from somewhere other than the backend itself.
- **For one visit:** `?story=<url of a story JSON>`. `?t=42` opens it paused at 42 seconds.
- **In the page:** the **Story file** tab loads a story JSON by URL. **Reload** re-reads it after edits without losing your place, and the scene list jumps to any chapter.
- **From other scripts:** `window.StoryPlayer.load(url, { play: true })`.

Paths inside a story JSON (`audio.src`, `languages[].src`) resolve **relative to the JSON file**.

## Regenerate the bundled sample

```bash
.venv/bin/python frontend/tools/generate_story.py                    # Karan, 776, Hindi + English
.venv/bin/python frontend/tools/generate_story.py --name Priya --score 665 --cards 2 --util 45 --missed 2
```

It needs network access (edge-tts) and overwrites `frontend/data/story.<lang>.json` and `frontend/audio/credit_story_<lang>.mp3`.

## Story JSON format

This is what `POST /api/story` produces and the player reads. You normally don't write it by hand, but every field can be edited.

```jsonc
{
  "schema_version": "2.1",
  "input": { /* the input JSON it was generated from; the builder form reads it back */ },
  "brand": { "name": "Oct Credit" },                         // first word is drawn in the accent colour
  "language": "hi",
  "languages": [                                              // language pill + sheet (hidden if < 2)
    { "code": "hi", "label": "हिंदी",   "src": "story.hi.json" },
    { "code": "en", "label": "English", "src": "story.en.json" }
  ],
  "audio": { "src": "../audio/credit_story_hi.mp3", "duration": 86.278 },
  "player": { "captions": true },                             // captions on/off by default
  "intro": {                                                  // start screen
    "title": "Hi Karan, your *September* credit story is ready",
    "subtitle": "…",
    "badges": [ { "icon": "shield", "text": "100% Secure & Private" } ]
  },
  "scenes": [ /* see below */ ],
  "end_card": {
    "title": "That's your *September* story", "text": "Your next update will be ready in 1 day",
    "recap": [ { "label": "Credit score", "value": "776 · Very good", "tone": "good" } ],
    "primary_label": "Replay story", "secondary_label": "Close", "disclaimer": "…"
  },
  "captions": [
    { "start": 0.70, "end": 1.92, "text": "नमस्ते करण!",
      "words": [ { "start": 0.70, "end": 1.39, "text": "नमस्ते" }, { "start": 1.41, "end": 1.92, "text": "करण" } ] }
  ]
}
```

**Conventions**

- `scenes[].start` / `end` and all `captions` times are **seconds on the audio timeline**.
- `scenes[].beats[].at` is **seconds after the scene's start**. A beat is a moment where something new animates in. Beats that are left out fall back to defaults, and blocks whose beat is missing stay hidden.
- The scene whose `start` is the latest one `≤` the current time is shown, so scenes should cover the audio back to back. Each scene is one segment of the progress bar at the top.
- `chapter` is the scene's label in the header. `theme` tints the background haze: `blue`, `green`, `amber` or `violet`.
- `tone` / `status.tone` colours chips and icons: `good`, `warn`, `bad` or `neutral`.
- In any `title`, wrap words in `*asterisks*` to draw them in the accent blue.

### Scene types

| `type` | `props` | `beats` (`action`) |
| --- | --- | --- |
| `title` | `eyebrow`, `title`, `subtitle`, `chips[] {icon, text}` | `chip` with `index` |
| `points` | `eyebrow`, `title`, `subtitle`, `items[] {icon, title, text, tag, tone}` | `item` with `index` |
| `stats` | `eyebrow`, `title`, `subtitle`, `tiles[] {icon, label, value, sub, tone}` (numbers count up), `note {icon, text}` | `item` with `index`; `note` |
| `bars` | `eyebrow`, `title`, `subtitle`, `summary {value, label, hint, tone}`, `bars[] {label, sub, value, max, display, tone, group}` (with `max` a bar shows value of max, otherwise bars scale to the largest) | `item` with `index`; `summary` |
| `history_grid` | `eyebrow`, `title`, `subtitle`, `months[]`, `rows[] {label, sub, cells[]: "ok" / "late" / "severe" / "none"}`, `legend[] {state, text}` | `row` with `index`; `legend`; `highlight` (late cells pulse) |
| `list` | `eyebrow`, `title`, `subtitle`, `rows[] {icon, title, sub, value, value_sub, tone}`, `note {icon, text}` | `item` with `index`; `note` |
| `score_dial` | `eyebrow`, `title`, `score`, `count_from`, `min`, `max`, `bands[] {from, to, label, color}`, `status {text, tone}`, `percentile {value, text}`, `lenders {title, items[] {icon, label, value}}` | `intro`; `reveal` (+`duration`): marker sweeps the dial and the score counts up; `percentile`: people chart fills; `lenders`: lender stats appear |
| `factor_overview` | `eyebrow`, `title`, `subtitle`, `items[] {icon, name, impact, result, tone}`, `note` | `item` with `index`: that row slides in (the note follows the last row) |
| `factor_insight` | `eyebrow`, `impact`, `title`, `explainer`, `metric {visual: "ring" or "count", value, progress 0..1, label, sublabel, status}`, `detail` (see below), `tip {icon, title, text}` | `intro`; `value`: metric card, ring fills and number counts up; `detail`: detail card; `tip` (defaults to 0.9s after `detail`) |
| `action_plan` | `eyebrow`, `title`, `current`, `target`, `target_label`, `range [lo, hi]`, `gap_label`, `steps[] {icon, title, text}`, `note` | `reveal` (+`duration`): progress track fills; `step` with `index`: that step card appears |

**`factor_insight.detail` types**

- `{ "type": "months", "title", "months": [ { "label": "Mar", "state": "ok" or "late" or "none" } ], "summary" }` shows a payment strip.
- `{ "type": "meter", "title", "value": 0-100 or null, "ideal_max": 30, "summary" }` shows a utilisation bar with a healthy zone. `null` draws the empty state.
- `{ "type": "slots", "title", "value", "limit", "summary" }` shows usage against a limit, e.g. enquiries.

An unknown `type` renders a placeholder card instead of breaking the story, and a malformed JSON shows the exact problem on screen.

**Icon names:** `calendar`, `card`, `doc`, `search`, `wallet`, `bolt`, `shield`, `trend`, `bulb`, `info`, `check`, `sparkle`.
