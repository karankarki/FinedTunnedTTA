import 'dart:ui' show Color;

/// Timeline of one story language (`full.<lang>.json`). All times are seconds from the start
/// of that language's MP3.
class Timeline {
  Timeline({
    required this.language,
    required this.duration,
    required this.canvas,
    required this.palette,
    required this.intro,
    required this.endCard,
    required this.chapters,
    required this.scenes,
    required this.captions,
    required this.brand,
  });

  final String language;
  final double duration;
  final StoryCanvas canvas;
  final Palette palette;
  final Map<String, dynamic> intro;
  final Map<String, dynamic> endCard;
  final List<Chapter> chapters;
  final List<Scene> scenes;
  final List<Caption> captions;
  final String brand;

  factory Timeline.fromJson(Map<String, dynamic> j) {
    final scenes = [for (final s in (j['scenes'] as List? ?? const [])) Scene.fromJson(s as Map<String, dynamic>)]
      ..sort((a, b) => a.start.compareTo(b.start));
    return Timeline(
      language: j['language'] as String? ?? 'en',
      duration: _d(j['duration']),
      canvas: StoryCanvas.fromJson(j['canvas'] as Map<String, dynamic>?),
      palette: Palette.fromJson(j['palette'] as Map<String, dynamic>?),
      intro: (j['intro'] as Map?)?.cast<String, dynamic>() ?? const {},
      endCard: (j['end_card'] as Map?)?.cast<String, dynamic>() ?? const {},
      chapters: [for (final c in (j['chapters'] as List? ?? const [])) Chapter.fromJson(c as Map<String, dynamic>)],
      scenes: scenes,
      captions: [for (final c in (j['captions'] as List? ?? const [])) Caption.fromJson(c as Map<String, dynamic>)],
      brand: ((j['brand'] as Map?)?['name'] as String?) ?? '',
    );
  }

  /// Index of the scene on screen at time [t] (the last one that has started).
  int sceneIndexAt(double t) {
    var lo = 0, hi = scenes.length - 1, found = 0;
    while (lo <= hi) {
      final mid = (lo + hi) >> 1;
      if (scenes[mid].start <= t) {
        found = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return found;
  }

  Chapter? chapterAt(double t) {
    Chapter? found;
    for (final c in chapters) {
      if (c.start <= t) found = c;
    }
    return found;
  }

  Caption? captionAt(double t) {
    for (final c in captions) {
      if (c.start <= t && t < c.end + 0.25) return c;
      if (c.start > t) break;
    }
    return null;
  }
}

/// The fixed design frame every scene is drawn on; the frame is scaled to fit the screen.
class StoryCanvas {
  const StoryCanvas({
    this.width = 360,
    this.height = 640,
    this.background = const Color(0xFF000000),
    this.safeTop = 56,
    this.safeRight = 20,
    this.safeBottom = 116,
    this.safeLeft = 20,
    this.captionBottom = 20,
    this.captionMaxChars = 84,
  });

  final double width, height, safeTop, safeRight, safeBottom, safeLeft, captionBottom;
  final int captionMaxChars;
  final Color background;

  double get contentWidth => width - safeLeft - safeRight;

  factory StoryCanvas.fromJson(Map<String, dynamic>? j) {
    if (j == null) return const StoryCanvas();
    final safe = (j['safe_area'] as Map?) ?? const {};
    final cap = (j['captions'] as Map?) ?? const {};
    return StoryCanvas(
      width: _d(j['width'], 360),
      height: _d(j['height'], 640),
      background: parseColor(j['background'], const Color(0xFF000000)),
      safeTop: _d(safe['top'], 56),
      safeRight: _d(safe['right'], 20),
      safeBottom: _d(safe['bottom'], 116),
      safeLeft: _d(safe['left'], 20),
      captionBottom: _d(cap['bottom'], 20),
      captionMaxChars: (cap['max_chars'] as num?)?.toInt() ?? 84,
    );
  }
}

class Palette {
  const Palette({
    this.accent = const Color(0xFF1677FF),
    this.text = const [Color(0xFFF5F5F7), Color(0xFFC7C7CC), Color(0xFF8E8E93)],
    this.surface = const [Color(0xFF111113), Color(0xFF1A1A1D), Color(0xFF242428)],
    this.line = const Color(0x17FFFFFF),
    this.tones = const {
      'good': Color(0xFF12B76A),
      'warn': Color(0xFFF79009),
      'bad': Color(0xFFF04438),
      'neutral': Color(0xFF8B72FF),
    },
    this.cells = const {
      'ok': Color(0xFF12B76A),
      'late': Color(0xFFF79009),
      'severe': Color(0xFFF04438),
      'none': Color(0x24FFFFFF),
    },
    this.themes = const {
      'blue': [Color(0xFF1D4ED8), Color(0xFF1E3A8A)],
      'green': [Color(0xFF1D4ED8), Color(0xFF065F46)],
      'amber': [Color(0xFF92400E), Color(0xFF1E3A8A)],
      'violet': [Color(0xFF3730A3), Color(0xFF1E3A8A)],
    },
  });

  final Color accent, line;
  final List<Color> text, surface;
  final Map<String, Color> tones, cells;
  final Map<String, List<Color>> themes;

  Color tone(Object? name) => tones[name] ?? text[1];
  Color cell(Object? state) => cells[state] ?? cells['none']!;
  List<Color> theme(Object? name) => themes[name] ?? themes['blue']!;

  factory Palette.fromJson(Map<String, dynamic>? j) {
    const d = Palette();
    if (j == null) return d;
    Map<String, Color> colors(Object? m, Map<String, Color> fallback) => m is Map
        ? {...fallback, for (final e in m.entries) '${e.key}': parseColor(e.value, fallback['${e.key}'] ?? d.text[1])}
        : fallback;
    List<Color> list(Object? l, List<Color> fallback) =>
        l is List && l.length >= fallback.length ? [for (var i = 0; i < l.length; i++) parseColor(l[i], fallback[i % fallback.length])] : fallback;
    final themes = j['themes'];
    return Palette(
      accent: parseColor(j['accent'], d.accent),
      text: list(j['text'], d.text),
      surface: list(j['surface'], d.surface),
      line: parseColor(j['line'], d.line),
      tones: colors(j['tones'], d.tones),
      cells: colors(j['cells'], d.cells),
      themes: themes is Map
          ? {...d.themes, for (final e in themes.entries) '${e.key}': list(e.value, d.themes['blue']!)}
          : d.themes,
    );
  }
}

class Chapter {
  Chapter(this.id, this.title, this.start, this.end);
  final String id, title;
  final double start, end;

  factory Chapter.fromJson(Map<String, dynamic> j) =>
      Chapter('${j['id']}', '${j['chapter'] ?? ''}', _d(j['start']), _d(j['end']));
}

class Beat {
  Beat(this.at, this.action, this.index, this.duration);
  final double at;
  final String action;
  final int? index;
  final double? duration;

  factory Beat.fromJson(Map<String, dynamic> j) => Beat(
        _d(j['at']),
        '${j['action']}',
        (j['index'] as num?)?.toInt(),
        j['duration'] is num ? (j['duration'] as num).toDouble() : null,
      );
}

class Scene {
  Scene({
    required this.id,
    required this.type,
    required this.chapter,
    required this.theme,
    required this.start,
    required this.end,
    required this.props,
    required this.beats,
    required this.continues,
  });

  final String id, type, chapter, theme;
  final double start, end;
  final Map<String, dynamic> props;
  final List<Beat> beats;
  final bool continues;

  factory Scene.fromJson(Map<String, dynamic> j) => Scene(
        id: '${j['id']}',
        type: '${j['type']}',
        chapter: '${j['chapter'] ?? ''}',
        theme: '${j['theme'] ?? 'blue'}',
        start: _d(j['start']),
        end: _d(j['end']),
        props: (j['props'] as Map?)?.cast<String, dynamic>() ?? const {},
        beats: [for (final b in (j['beats'] as List? ?? const [])) Beat.fromJson(b as Map<String, dynamic>)],
        continues: j['continues'] == true,
      );

  /// Time (relative to the scene start) of the beat [action] (and [index]), or null if absent.
  Beat? beat(String action, [int? index]) {
    for (final b in beats) {
      if (b.action == action && (index == null || b.index == index)) return b;
    }
    return null;
  }

  bool hasAction(String action) => beats.any((b) => b.action == action);
}

class Word {
  Word(this.text, this.start, this.end);
  final String text;
  final double start, end;
}

class Caption {
  Caption(this.text, this.start, this.end, this.words);
  final String text;
  final double start, end;
  final List<Word> words;

  factory Caption.fromJson(Map<String, dynamic> j) => Caption(
        '${j['text'] ?? ''}',
        _d(j['start']),
        _d(j['end']),
        [
          for (final w in (j['words'] as List? ?? const []))
            Word('${(w as Map)['text']}', _d(w['start']), _d(w['end'])),
        ],
      );

  /// The sentence split into subtitle pages of at most [maxChars] characters, as word lists.
  /// Pages are balanced (a 100-character sentence becomes two of ~50, not 84 + 16).
  List<List<Word>> pages(int maxChars) {
    final total = words.fold<int>(0, (n, w) => n + w.text.length + 1);
    if (words.isEmpty) return const [];
    final count = (total / maxChars).ceil();
    final target = total / count;
    final out = <List<Word>>[];
    var page = <Word>[];
    var len = 0;
    for (final w in words) {
      final add = w.text.length + 1;
      final full = len + add > maxChars || (len >= target && out.length < count - 1);
      if (page.isNotEmpty && full) {
        out.add(page);
        page = [];
        len = 0;
      }
      page.add(w);
      len += add;
    }
    if (page.isNotEmpty) out.add(page);
    return out;
  }
}

/// `#RRGGBB` or `#AARRGGBB`.
Color parseColor(Object? v, Color fallback) {
  if (v is! String || !v.startsWith('#')) return fallback;
  var hex = v.substring(1);
  if (hex.length == 6) hex = 'FF$hex';
  final n = int.tryParse(hex, radix: 16);
  return hex.length == 8 && n != null ? Color(n) : fallback;
}

double _d(Object? v, [double fallback = 0]) => v is num ? v.toDouble() : fallback;
