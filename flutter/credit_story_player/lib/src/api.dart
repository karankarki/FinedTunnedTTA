import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart' show compute;
import 'package:http/http.dart' as http;

import 'models.dart';

/// One language of a finished story: its MP3 and its timeline JSON (inline when the API sent it).
class StoryMedia {
  const StoryMedia({required this.language, required this.label, required this.audioUrl, required this.jsonUrl, this.json});
  final String language, label, audioUrl, jsonUrl;
  final Map<String, dynamic>? json;
}

class StoryResponse {
  StoryResponse(this.storyId, this.status, this.error, this.languages, this.raw);
  final String storyId, status;
  final String? error;

  /// Languages whose MP3 + JSON are ready, first = default.
  final List<StoryMedia> languages;
  final Map<String, dynamic> raw;

  bool get ready => status == 'ready' && languages.isNotEmpty;

  /// `{"status", "languages": ["hi", "en"], "hi": {"audio_url", "json_url", "json"}, "en": {...}}`
  factory StoryResponse.fromJson(Map<String, dynamic> j) {
    final langs = <StoryMedia>[];
    for (final code in (j['languages'] as List? ?? const [])) {
      final m = j['$code'];
      if (m is! Map || m['audio_url'] == null) continue;
      langs.add(StoryMedia(
        language: '$code',
        label: '${m['label'] ?? code}',
        audioUrl: '${m['audio_url']}',
        jsonUrl: '${m['json_url']}',
        json: (m['json'] as Map?)?.cast<String, dynamic>(),
      ));
    }
    return StoryResponse('${j['story_id']}', '${j['status']}', j['error'] as String?, langs, j);
  }
}

class StoryApiException implements Exception {
  StoryApiException(this.message, {this.retryable = false});
  final String message;
  final bool retryable;
  @override
  String toString() => message;
}

/// Client for the story engine. The backend sleeps when idle (Render free tier), so the first
/// call can take up to a minute: timeouts are generous and one timeout is retried.
class StoryApi {
  StoryApi({this.baseUrl = 'https://finedtunnedtta.onrender.com', http.Client? client})
      : _client = client ?? http.Client();

  final String baseUrl;
  final http.Client _client;

  Uri _u(String path, [Map<String, String>? query]) => Uri.parse('$baseUrl$path').replace(queryParameters: query);

  /// Call when the feature opens, so the server is awake by the time the user asks for a story.
  Future<void> wakeUp() async {
    try {
      await _client.get(_u('/api/status')).timeout(const Duration(seconds: 90));
    } catch (_) {}
  }

  /// Generate (or reuse) the story for a CRIF High Mark report, passed through unchanged
  /// (the bureau API response as a JSON string). Returns both languages with their timelines.
  Future<StoryResponse> createStory(String crifJson, {List<String> languages = const ['hi', 'en'], String? customerName}) =>
      _post('/api/story', crifJson, {
        'languages': languages.join(','),
        if (customerName != null) 'customer_name': customerName,
      });

  Future<StoryResponse> getStory(String storyId) async {
    final res = await _client.get(_u('/api/story/$storyId')).timeout(const Duration(seconds: 60));
    return _parse(res);
  }

  Future<StoryResponse> _post(String path, String body, Map<String, String> query) async {
    for (var attempt = 0;; attempt++) {
      try {
        final res = await _client
            .post(_u(path, query), headers: {'Content-Type': 'application/json'}, body: body)
            .timeout(const Duration(seconds: 300));
        var story = await _parse(res);
        // A story that outlasts the server's wait comes back 202 "generating"; finish by polling.
        final deadline = DateTime.now().add(const Duration(minutes: 3));
        while (!story.ready && story.status == 'generating' && DateTime.now().isBefore(deadline)) {
          await Future<void>.delayed(const Duration(seconds: 2));
          story = await getStory(story.storyId);
        }
        if (!story.ready) {
          throw StoryApiException(story.error ?? 'The story could not be generated.', retryable: true);
        }
        return story;
      } on TimeoutException {
        if (attempt >= 1) throw StoryApiException('The server is taking too long. Please try again.', retryable: true);
      } on http.ClientException catch (e) {
        if (attempt >= 1) throw StoryApiException('Network error: ${e.message}', retryable: true);
      }
    }
  }

  Future<StoryResponse> _parse(http.Response res) async {
    Object? data;
    try {
      data = await compute(_decode, res.bodyBytes); // ~250 KB with both timelines: decode off the UI thread
    } catch (_) {}
    if (res.statusCode >= 400 || data is! Map<String, dynamic>) {
      final detail = data is Map ? data['detail'] : null;
      final message = detail is String
          ? detail
          : detail is List && detail.isNotEmpty
              ? '${(detail.first as Map)['msg']}'
              : 'Server error (${res.statusCode})';
      throw StoryApiException(message, retryable: res.statusCode >= 500);
    }
    return StoryResponse.fromJson(data);
  }

  /// The timeline of [media]: from the response when it came inline, else downloaded.
  Future<Timeline> timelineFor(StoryMedia media) async =>
      media.json != null ? Timeline.fromJson(media.json!) : loadTimeline(media.jsonUrl);

  /// Download and parse a timeline JSON (parsed off the UI thread; CRIF timelines are ~100 KB).
  Future<Timeline> loadTimeline(String jsonUrl) async {
    final res = await _client.get(Uri.parse(jsonUrl)).timeout(const Duration(seconds: 60));
    if (res.statusCode != 200) {
      throw StoryApiException('Could not load the story (${res.statusCode}). It may have expired.', retryable: true);
    }
    return compute(parseTimeline, utf8.decode(res.bodyBytes));
  }
}

Object? _decode(List<int> bytes) => jsonDecode(utf8.decode(bytes));

Timeline parseTimeline(String json) => Timeline.fromJson(jsonDecode(json) as Map<String, dynamic>);
