// Stage-by-stage playback: starts in ~2 s and plays every stage back to back with no gap.
//
//   final flow = StageFlow(baseUrl: 'http://192.168.31.11:8000', language: 'hi');
//   await flow.start(crifJson);            // POST /api/story/stages -> stage 1, then fetches the rest
//   flow.player.play();
//   // each frame: final (stage, t) = flow.position;  draw stage.json scenes/captions at time t
import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:just_audio/just_audio.dart';

class Stage {
  Stage(this.number, this.chapter, this.audioUrl, this.duration, this.json);
  final int number;
  final String chapter;
  final String audioUrl;
  final double duration;
  final Map<String, dynamic> json; // {"duration", "scenes", "captions"} on this stage's own clock
}

class StageFlow {
  StageFlow({required this.baseUrl, this.language = 'hi'});

  final String baseUrl;
  final String language; // "hi" or "en"
  final player = AudioPlayer();
  final stages = <Stage>[];
  final _playlist = ConcatenatingAudioSource(children: [], useLazyPreparation: true);
  Map<String, dynamic> story = {}; // intro, end_card, canvas, palette
  int totalStages = 0;

  /// Sends the CRIF report, queues stage 1, then fetches the remaining stages in the background.
  Future<void> start(String crifJson) async {
    final res = await http
        .post(Uri.parse('$baseUrl/api/story/stages'), headers: {'Content-Type': 'application/json'}, body: crifJson)
        .timeout(const Duration(minutes: 3));
    final first = _check(res);
    story = (first['story'] as Map).cast<String, dynamic>();
    totalStages = first['total_stages'] as int;
    await _add(first);
    await player.setAudioSource(_playlist);
    unawaited(_fetchRest(first['next_url'] as String?));
  }

  Future<void> _fetchRest(String? url) async {
    while (url != null) {
      final res = await http.get(Uri.parse(url)).timeout(const Duration(minutes: 3));
      if (res.statusCode == 202) {
        await Future<void>.delayed(const Duration(seconds: 2)); // still recording: ask again
        continue;
      }
      final stage = _check(res);
      await _add(stage);
      url = stage['next_url'] as String?;
    }
  }

  Future<void> _add(Map<String, dynamic> body) async {
    final media = (body[language] as Map).cast<String, dynamic>();
    stages.add(Stage(body['stage'] as int, '${body['chapter']}', '${media['audio_url']}',
        (media['duration'] as num).toDouble(), (media['json'] as Map).cast<String, dynamic>()));
    await _playlist.add(AudioSource.uri(Uri.parse('${media['audio_url']}'))); // gapless: appended to the queue
  }

  /// The stage playing now and the time inside it (seconds) - drive the animation from this.
  (Stage?, double) get position {
    final i = player.currentIndex ?? 0;
    return (i < stages.length ? stages[i] : null, player.position.inMicroseconds / 1e6);
  }

  /// True once the last stage has finished: show story['end_card'].
  bool get finished =>
      stages.length == totalStages && player.processingState == ProcessingState.completed;

  Map<String, dynamic> _check(http.Response res) {
    final body = jsonDecode(utf8.decode(res.bodyBytes));
    if (res.statusCode != 200) {
      throw Exception(body is Map ? '${body['detail']}' : 'HTTP ${res.statusCode}');
    }
    return (body as Map).cast<String, dynamic>();
  }

  Future<void> dispose() => player.dispose();
}
