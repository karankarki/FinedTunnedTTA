import 'dart:io';

import 'package:credit_story_player/credit_story_player.dart';
import 'package:flutter_test/flutter_test.dart';

// POST /api/story response: both languages, each with its MP3 URL and inline timeline JSON.
void main() {
  test('parses both languages with inline timelines', () {
    final timeline = parseTimeline(File('test/data/quick_en.json').readAsStringSync());
    expect(timeline.scenes, isNotEmpty);
    final raw = {
      'story_id': 'abc',
      'status': 'ready',
      'languages': ['hi', 'en'],
      'hi': {'label': 'हिंदी', 'audio_url': 'https://x/full.hi.mp3', 'json_url': 'https://x/full.hi.json', 'json': {'duration': 10, 'scenes': []}},
      'en': {'label': 'English', 'audio_url': 'https://x/full.en.mp3', 'json_url': 'https://x/full.en.json'},
    };
    final r = StoryResponse.fromJson(raw);
    expect(r.ready, isTrue);
    expect(r.languages.map((l) => l.language), ['hi', 'en']);
    expect(r.languages.first.json, isNotNull);
    expect(r.languages.last.json, isNull); // ?include_json=false: the player downloads json_url instead
  });

  test('a 202 "generating" response has no playable languages yet', () {
    final r = StoryResponse.fromJson({'story_id': 'abc', 'status': 'generating', 'languages': ['hi', 'en'], 'hi': null, 'en': null});
    expect(r.ready, isFalse);
    expect(r.languages, isEmpty);
  });
}
