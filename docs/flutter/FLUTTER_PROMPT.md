# Flutter integration: Credit Story Player

Paste everything below the line into your AI coding assistant (Claude Code, Cursor, Copilot…) inside the Flutter project. Put `sample_full.en.json` and `sample_requests.json` from this folder into the app's `assets/mock/` first.

---

## Prompt

Build a **Credit Story Player** feature in this Flutter app. A backend turns a credit profile into a narrated, animated "video": it returns **one MP3** and **one JSON timeline**. The app plays the MP3 and draws animated scenes in sync with it: a score dial, factor cards, bars, month grids, an action plan, and word-by-word captions. The app renders everything itself, with no WebView.

### 1. Backend

Base URL: `https://finedtunnedtta.onrender.com` (put it in one config constant).

- It runs on Render's free tier, so the **first request after it has been idle can take 30–60 s** while the server wakes up. When the feature opens, call `GET /api/status` to wake the server. Use a 120 s timeout on story requests and show a loader, not an error, while waiting.
- All URLs in responses are absolute. Use them as they are.
- Stories are deleted **24 h** after creation (`expires_at`). Never persist the URLs. Re-request the story instead: the same input gives the same `story_id` and comes back instantly while it is still cached.

#### Endpoints

| Method | Path | Use |
|---|---|---|
| GET | `/api/status` | Wake-up / health check |
| POST | `/api/story?complete=true` | Quick summary story (1–2 min) from a few fields. Waits until the MP3 + JSON are ready (~3–10 s warm) |
| POST | `/api/story/crif?complete=true&languages=hi,en&customer_name=X` | Detailed story (8–14 min) from a raw CRIF High Mark bureau response, sent as the body unchanged. ~30–60 s the first time |
| GET | `/api/story/{story_id}` | Progress, for when you call without `complete=true`. Poll every 2 s until `status == "ready"` and `full != null` |

`POST /api/story` body (all optional except `credit_score`):

```json
{
  "customer_name": "Karan",          // shown on screen, spoken in English (max 40)
  "customer_name_hi": "करण",          // name spoken in Hindi narration
  "credit_score": 776,               // 300–900, required
  "score_bureau": "CIBIL",
  "report_month": "2026-09",         // YYYY-MM, default current month
  "on_time_repayment_pct": 100,      // 0–100
  "missed_payments_count": 0,        // last 6 months, 0–36
  "active_credit_cards": 0,          // 0–30
  "credit_utilization_pct": 0,       // 0–100, ignored if no cards
  "recent_inquiries": 0,             // last 6 months, 0–50
  "languages": ["hi", "en"],         // first = default; each gets its own MP3 + JSON
  "voice_speed": 0.92                // 0.7–1.3
}
```

Response (trimmed; the full example is in `assets/mock/sample_story_response.json`):

```json
{
  "story_id": "9f1a012d7332394c",
  "cached": false,
  "status": "ready",                       // "generating" | "ready" | "failed"
  "error": "…",                            // only when something failed
  "full": {                                // null until ready; first language
    "language": "en",
    "audio_url": "https://finedtunnedtta.onrender.com/stories/9f1a…/full.en.mp3",
    "json_url":  "https://finedtunnedtta.onrender.com/stories/9f1a…/full.en.json",
    "duration": 68.04
  },
  "languages": [
    {"code": "en", "label": "English", "story_url": "…", "full": { …same shape… }}
  ],
  "expires_at": "2026-09-24T06:40:12+00:00"
}
```

Errors: `422` means invalid input (show `detail`), `502` means the voice service failed (offer Retry), and a timeout means the server is still waking up (retry once automatically).

### 2. Timeline JSON (`full.<lang>.json`)

**All times are seconds from the start of that MP3.** Top-level keys:

- `title`, `brand` (`{name}`), `language`, `duration`
- `intro`: `{title, subtitle, badges:[{icon,text}]}`, the start screen shown before Play
- `end_card`: `{title, text, recap:[{label,value,tone}], primary_label, secondary_label, disclaimer}`, shown when the audio ends. Primary = replay, secondary = close.
- `chapters[]`: `{id, chapter, theme, start, end}`, for chapter ticks on the seek bar and a chapter list
- `scenes[]`: `{id, type, chapter, theme, start, end, props, beats[], continues?}`
- `captions[]`: `{start, end, text, words:[{start, end, text}]}`

Model every type with `fromJson` classes. Keep `props` as `Map<String, dynamic>` and let each scene widget read its own fields. **Ignore unknown keys and unknown scene types**: an unknown type should render a generic card with `props.title`, never crash.

### 3. Sync rules (most important)

- Use **`just_audio`**. Drive everything from `player.positionStream` (plus a ~60 fps `Ticker` that interpolates between position events for smooth animation).
- **Active scene** = the last scene with `scene.start <= t`. Scenes are contiguous and sorted.
- **Beat** `b` of a scene has fired when `t >= scene.start + b.at`. Beats carry `action` and optional `index` / `duration`. A negative `at` (e.g. `-30`) means the beat has already happened.
- **Compute the visual state from `t`, not from events.** For any `t` the widget must look right: all beats with `at <= t - scene.start` are applied. Seeking back or forward, or pausing, then just works. Animate only the transition when a beat newly fires during normal playback.
- A scene with `continues: true` is the second half of the previous scene (same visual). Swap it in with no transition.
- Crossfade/slide between scenes over ~350 ms. Use `theme` to tint the background haze.
- **Active caption** = the caption with `start <= t < end`. Highlight words with `word.start <= t`. Show it at the bottom, 2 lines max, and hide it between sentences.

### 4. Scene types and what to draw

Titles can contain `*accent*` markup: render the text between asterisks in the accent colour (`#1677ff`, or the theme colour).

| `type` | Props | Beats → what happens |
|---|---|---|
| `title` | `eyebrow, title, subtitle, chips[{icon,text}]` | `intro` fade in; `chip{index}` pop chip i |
| `score_dial` | `eyebrow, title, score, count_from, min, max, bands[{from,to,label,color}], status{text,tone}, percentile?{value,text}, lenders?{title, items[{icon,label,value}]}` | `intro`; `reveal{duration}` needle + number count from `count_from` → `score` over `duration` s, band-coloured arc (semicircle, 300–900); `percentile` show the percentile line; `lenders` show the 3 lender tiles |
| `factor_overview` | `eyebrow, title, subtitle, items[{icon,name,impact,result,tone}], note` | `item{index}` reveal row i |
| `factor_insight` | `eyebrow, impact, title, explainer, metric{visual:"ring"\|"count", value, progress?, label, sublabel, status{text,tone}}, detail` | `intro`; `value` animate ring to `progress` (0–1) or count up; `detail` show detail |
| ↳ `detail.type == "months"` | `title, months[{label, state}], summary` | 6 chips; state `ok` green, `late` amber, `severe` red, `none` grey |
| ↳ `detail.type == "meter"` | `title, value (0–100 or null), ideal_max (30), summary` | horizontal bar with a marker at `ideal_max`; fill to value |
| ↳ `detail.type == "slots"` | `title, value, limit, summary` | `limit` slots, `value` filled (overflow in red) |
| `points` | `eyebrow, title, subtitle, items[{icon,title,text,tag}]` | `intro`; `item{index}` reveal item |
| `stats` | `eyebrow, title, tiles[{label,value,sub?,icon,tone?}]` | `intro`; `item{index}` reveal tile (2-column grid) |
| `bars` | `eyebrow, title, subtitle, bars[{label,value,display,icon,sub}]` | `intro`; `item{index}` grow bar i (width ∝ value / max value) |
| `history_grid` | `eyebrow, title, subtitle, months[12], rows[{label,sub,cells[12] of ok/late/severe/none}], legend[{state,text}]` | `intro`; `row{index}` reveal row; `legend` show legend; `highlight` pulse the non-`ok` cells (or all cells if none are late) |
| `list` | `eyebrow, title, subtitle, rows[{icon,title,sub,value,value_sub,tone}]` | `intro`; `item{index}` reveal row |
| `action_plan` | `eyebrow, title, current, target, target_label, range[min,max], gap_label, steps[{icon,title,text}], note` | `intro`/`reveal`: a track from range min → max with the current marker moving to target; `step{index}` reveal step i |

Items whose beat hasn't fired yet are laid out but invisible (opacity 0, slight offset), so nothing jumps.

**Tones → colours:** `good` #12B76A, `warn` #F79009, `bad` #F04438, `neutral` #8B72FF.
**Themes (background haze):** `blue`, `green`, `amber`, `violet`.
**Icons** (map to Material/Lucide icons): `alert bolt bulb calendar card check clock doc home info mail phone search shield trend wallet x`. Unknown → `info`.

Design (dark, default): background #000000, surfaces #111113 / #1A1A1D, text #F5F5F7 / #C7C7CC / #8E8E93, hairlines white 9%, accent #1677FF, font Manrope (`google_fonts`), rounded 16–20 px cards, generous spacing. Portrait 9:16 layout; it must fit a 360×640 screen without overflow.

### 5. Player UI

- Screen states: **loading** (while the API works; show "Preparing your credit story…" and handle the long cold start) → **intro** (intro card + big Play) → **playing** (scene stage + captions + controls) → **end card**.
- Controls: play/pause, a seek bar with chapter ticks (drag to seek; show chapter name while dragging), elapsed / total, captions toggle, and a language switch when `languages.length > 1`. Switching language loads that language's `full.audio_url` + `json_url` and restarts from the same chapter's start.
- Download the JSON with `http` and parse it on a background isolate (`compute`) for the long CRIF stories (~90 KB). Stream the MP3 with `just_audio` (`setUrl`). Don't download it first.
- Pause on app background (`AppLifecycleState.paused`). Release the player on dispose.

### 6. Code layout

```
lib/credit_story/
  api/story_api.dart          // createStory(), createCrifStory(), getStory(), wakeUp(); models
  models/timeline.dart        // Timeline, Scene, Beat, Caption, Word, Chapter (fromJson)
  player/story_controller.dart // ChangeNotifier: AudioPlayer, position, active scene/caption, seek, language
  player/story_player_page.dart
  scenes/scene_view.dart      // switch(type) → widget; fallback card
  scenes/score_dial.dart, factor_overview.dart, factor_insight.dart, points.dart, stats.dart,
         bars.dart, history_grid.dart, list_scene.dart, action_plan.dart, title_scene.dart
  widgets/captions.dart, seek_bar.dart, accent_text.dart (*markup*), story_icons.dart
```

Packages: `just_audio`, `http`, `google_fonts`. Add no others without asking.

### 7. Mock mode for development

Add `const useMock = true` in the config. In mock mode, load `assets/mock/sample_full.en.json` as the timeline and play the MP3 from its live URL, or from any local MP3 of ~68 s. Also add a debug screen listing the request bodies from `assets/mock/sample_requests.json`, with a button per scenario that calls the live API and opens the player.

### 8. Done when

- Each scenario in `sample_requests.json` plays end to end on Android and iOS, and scenes and captions change on the spoken word.
- Seeking to any point shows the correct scene with the correct items revealed. Pausing and resuming stays in sync.
- A cold backend (first call) shows the loader and then plays. It never shows an error just because the call took 60 s.
- Unknown scene types or extra JSON fields don't crash.
