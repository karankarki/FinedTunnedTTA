# credit_story_player

Flutter player for the credit story engine. The server returns **one MP3 + one timeline JSON**; this package plays them **like a video**.

## How it fits every screen

Every scene is laid out on one fixed design canvas: **360 × 640** logical px, from `canvas` in the JSON. That canvas is scaled as a whole to fit the space it gets (`BoxFit.contain`, letterboxed on black), the same way a video player scales a 9:16 clip. Nothing inside a scene reads the real screen size, so:

- placement is identical on a 320 px phone, a 412 px Android, a tablet, or in landscape;
- there are no overflow errors: each scene body is also `scaleDown`-fitted inside its safe area, so even unusually long data shrinks slightly instead of breaking;
- system font scaling is disabled inside the canvas, because the canvas itself is scaled.

The control bars (close, chapter, captions, language, play/pause, seek) sit **outside** the canvas, above and below it, so they never cover the story.

Every frame is a pure function of the audio position `t`. Scenes, beats (reveals, count-ups, bars filling) and captions are computed from `t`, never from running animations. Seeking, pausing, backgrounding and language switching can't desync the picture from the voice.

## Use it

```yaml
# pubspec.yaml of your app
dependencies:
  credit_story_player:
    git:
      url: https://github.com/karankarki/FinedTunnedTTA.git
      path: flutter/credit_story_player
```

```dart
import 'package:credit_story_player/credit_story_player.dart';

final api = StoryApi(baseUrl: 'https://finedtunnedtta.onrender.com');
api.wakeUp(); // when the feature opens: the free server sleeps when idle

final controller = StoryController(api: api)
  ..openCrif(crifJson); // the CRIF High Mark response (bureau API JSON) as a String

Navigator.push(context, MaterialPageRoute(builder: (_) => Scaffold(
  backgroundColor: Colors.black,
  body: CreditStoryPlayer(controller: controller, onClose: () => Navigator.pop(context)),
)));
// dispose the controller when the route is popped
```

`POST /api/story` returns Hindi and English together, each with its MP3 URL and the full timeline JSON, so the player downloads only the audio and switching language is instant.

Other ways to open a story:

| Call | When |
|---|---|
| `controller.openCrif(crifJson)` | Generate from a CRIF report (`POST /api/story`) |
| `controller.openStory(await api.createStory(crifJson))` | Same, when you want the response first |
| `controller.openMedia(StoryMedia(...))` | You already have an `audio_url` + `json_url` |
| `controller.openTimeline(timeline, mp3Url)` | Mock mode: timeline parsed from an asset |

Widgets:

- `CreditStoryPlayer`: the full player (bars + video frame). Fill any box with it: full screen, a sheet, or a card.
- `StoryStage`: the video frame only, driven by a controller. Use it to build your own controls.
- `StoryFrame(timeline:, t:)`: a single still frame at time `t`, with no audio. Use it for thumbnails and previews.

Platform setup (from `just_audio`): on iOS, nothing extra is needed. On Android, add `<uses-permission android:name="android.permission.INTERNET"/>` to `AndroidManifest.xml`. The server URLs are https, so no cleartext config is needed.

Fonts: Manrope, with Noto Sans Devanagari for Hindi. They're loaded with `google_fonts` on first run. To bundle them instead, add them as families `Manrope` and `NotoSansDevanagari` and set `StoryType.useGoogleFonts = false`.

## Scene types

`title`, `score_dial`, `factor_overview`, `factor_insight` (ring or count metric, plus a months, meter or slots detail), `points`, `stats`, `bars`, `history_grid`, `list`, `action_plan`. An unknown type shows its title only and never crashes.

## Develop

```bash
flutter test                   # renders every scene on 5 screen sizes; fails on any overflow
flutter test --update-goldens --dart-define=SCREENSHOTS=true  # also writes screenshots to test/goldens/
cd example && flutter run      # demo app: paste a CRIF report, plays it from the live server
```
