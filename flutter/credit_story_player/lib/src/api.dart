import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart' show compute;
import 'package:http/http.dart' as http;

import 'models.dart';

/// One language of a finished story: its MP3 and its timeline JSON.
class StoryMedia {
  const StoryMedia({required this.language, required this.label, required this.audioUrl, required this.jsonUrl});
  final String language, label, audioUrl, jsonUrl;
}

class StoryResponse {
  StoryResponse(this.storyId, this.status, this.error, this.languages, this.raw);
  final String storyId, status;
  final String? error;

  /// Languages whose MP3 + JSON are ready, first = default.
  final List<StoryMedia> languages;
  final Map<String, dynamic> raw;

  bool get ready => status == 'ready' && languages.isNotEmpty;

  factory StoryResponse.fromJson(Map<String, dynamic> j) {
    final langs = <StoryMedia>[];
    for (final l in (j['languages'] as List? ?? const [])) {
      final full = (l as Map)['full'] as Map?;
      if (full == null) continue;
      langs.add(StoryMedia(
        language: '${l['code']}',
        label: '${l['label'] ?? l['code']}',
        audioUrl: '${full['audio_url']}',
        jsonUrl: '${full['json_url']}',
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

  /// Quick summary story. [body]: credit_score (required), customer_name, customer_name_hi,
  /// missed_payments_count, active_credit_cards, credit_utilization_pct, recent_inquiries,
  /// languages, voice_speed…
  Future<StoryResponse> createStory(Map<String, dynamic> body) =>
      _post('/api/story', jsonEncode(body), {'complete': 'true'});

  /// Detailed story from a CRIF High Mark response, passed through unchanged.
  Future<StoryResponse> createCrifStory(String crifJson, {List<String> languages = const ['hi', 'en'], String? customerName}) =>
      _post('/api/story/crif', crifJson, {
        'complete': 'true',
        'languages': languages.join(','),
        if (customerName != null) 'customer_name': customerName,
      });

  Future<StoryResponse> getStory(String storyId) async {
    final res = await _client.get(_u('/api/story/$storyId')).timeout(const Duration(seconds: 30));
    return _parse(res);
  }

  Future<StoryResponse> _post(String path, String body, Map<String, String> query) async {
    for (var attempt = 0;; attempt++) {
      try {
        final res = await _client
            .post(_u(path, query), headers: {'Content-Type': 'application/json'}, body: body)
            .timeout(const Duration(seconds: 150));
        var story = _parse(res);
        // Long stories can outlast the server's wait; finish by polling.
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

  StoryResponse _parse(http.Response res) {
    final text = utf8.decode(res.bodyBytes);
    Object? data;
    try {
      data = jsonDecode(text);
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

  /// Download and parse a timeline JSON (parsed off the UI thread; CRIF timelines are ~100 KB).
  Future<Timeline> loadTimeline(String jsonUrl) async {
    final res = await _client.get(Uri.parse(jsonUrl)).timeout(const Duration(seconds: 60));
    if (res.statusCode != 200) {
      throw StoryApiException('Could not load the story (${res.statusCode}). It may have expired.', retryable: true);
    }
    return compute(parseTimeline, utf8.decode(res.bodyBytes));
  }
}

Timeline parseTimeline(String json) => Timeline.fromJson(jsonDecode(json) as Map<String, dynamic>);
