import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';
import 'package:just_audio/just_audio.dart';

import 'api.dart';
import 'models.dart';

enum StoryPhase { loading, intro, playing, paused, ended, error }

/// Owns the audio and the story clock. Everything on screen is a pure function of [time], so
/// seeking, pausing and resuming never desync the animation from the voice.
class StoryController extends ChangeNotifier {
  StoryController({StoryApi? api}) : api = api ?? StoryApi() {
    _ticker = Ticker(_onTick);
    _stateSub = _player.playerStateStream.listen(_onPlayerState);
  }

  final StoryApi api;
  final AudioPlayer _player = AudioPlayer();
  late final Ticker _ticker;
  late final StreamSubscription<PlayerState> _stateSub;

  /// Story time in seconds (MP3 position), updated every frame while playing.
  final ValueNotifier<double> time = ValueNotifier(0);

  StoryPhase phase = StoryPhase.loading;
  String loadingMessage = 'Preparing your credit story…';
  String? error;
  Timeline? timeline;
  List<StoryMedia> languages = const [];
  int languageIndex = 0;
  bool captionsOn = true;
  Future<void> Function()? _retry;
  bool _disposed = false;

  double get duration => timeline?.duration ?? 0;
  bool get isPlaying => phase == StoryPhase.playing;

  // ----- loading

  /// Generate a quick-summary story from [body] and open it.
  Future<void> openRequest(Map<String, dynamic> body) =>
      _guard(() async => openStory(await api.createStory(body)), () => openRequest(body));

  /// Open a story the API already returned.
  Future<void> openStory(StoryResponse story) async {
    languages = story.languages;
    languageIndex = 0;
    await _guard(() => _load(languages.first, 0), () => openStory(story));
  }

  /// Open one MP3 + timeline URL pair directly (e.g. saved from an earlier response).
  Future<void> openMedia(StoryMedia media) async {
    languages = [media];
    languageIndex = 0;
    await _guard(() => _load(media, 0), () => openMedia(media));
  }

  /// Mock mode: a timeline you already have (e.g. from an asset) plus its MP3 URL.
  Future<void> openTimeline(Timeline t, String audioUrl) async {
    languages = [StoryMedia(language: t.language, label: t.language, audioUrl: audioUrl, jsonUrl: '')];
    languageIndex = 0;
    await _guard(() async {
      await _player.setUrl(audioUrl);
      _ready(t, 0);
    }, () => openTimeline(t, audioUrl));
  }

  Future<void> _load(StoryMedia media, double at) async {
    _setLoading('Loading your story…');
    final t = await api.loadTimeline(media.jsonUrl);
    await _player.setUrl(media.audioUrl);
    _ready(t, at);
  }

  void _ready(Timeline t, double at) {
    timeline = t;
    time.value = at;
    phase = at > 0 ? StoryPhase.paused : StoryPhase.intro;
    _notify();
    if (at > 0) {
      _player.seek(Duration(milliseconds: (at * 1000).round()));
      play();
    }
  }

  Future<void> _guard(Future<void> Function() job, Future<void> Function() retry) async {
    _retry = retry;
    error = null;
    if (phase != StoryPhase.loading) _setLoading('Preparing your credit story…');
    final slow = Timer(const Duration(seconds: 8), () {
      if (phase == StoryPhase.loading) _setLoading('Waking up the story server… this can take up to a minute.');
    });
    try {
      await job();
    } catch (e) {
      error = e is StoryApiException ? e.message : 'Something went wrong: $e';
      phase = StoryPhase.error;
      _notify();
    } finally {
      slow.cancel();
    }
  }

  Future<void> retry() async => _retry?.call();

  void _setLoading(String message) {
    phase = StoryPhase.loading;
    loadingMessage = message;
    _notify();
  }

  // ----- playback

  Future<void> play() async {
    if (timeline == null) return;
    if (phase == StoryPhase.ended) await seek(0);
    phase = StoryPhase.playing;
    if (!_ticker.isActive) _ticker.start();
    _notify();
    unawaited(_player.play());
  }

  Future<void> pause() async {
    if (phase != StoryPhase.playing) return;
    phase = StoryPhase.paused;
    _ticker.stop();
    _notify();
    await _player.pause();
  }

  void toggle() => isPlaying ? pause() : play();

  Future<void> seek(double seconds) async {
    final t = seconds.clamp(0.0, duration).toDouble();
    time.value = t;
    if (phase == StoryPhase.ended) {
      phase = StoryPhase.paused;
      _notify();
    }
    await _player.seek(Duration(milliseconds: (t * 1000).round()));
  }

  Future<void> replay() async {
    await seek(0);
    await play();
  }

  void toggleCaptions() {
    captionsOn = !captionsOn;
    _notify();
  }

  /// Switch narration language, continuing from the start of the current chapter.
  Future<void> setLanguage(int index) async {
    if (index == languageIndex || index < 0 || index >= languages.length) return;
    final chapterId = timeline?.chapterAt(time.value)?.id;
    await pause();
    languageIndex = index;
    await _guard(() async {
      _setLoading('Switching language…');
      final t = await api.loadTimeline(languages[index].jsonUrl);
      await _player.setUrl(languages[index].audioUrl);
      final match = t.chapters.where((c) => c.id == chapterId);
      _ready(t, match.isEmpty ? 0 : match.first.start);
    }, () => setLanguage(index));
  }

  void _onTick(Duration _) {
    final t = _player.position.inMicroseconds / 1e6;
    if ((t - time.value).abs() > 0.001) time.value = t;
  }

  void _onPlayerState(PlayerState s) {
    if (s.processingState == ProcessingState.completed && phase == StoryPhase.playing) {
      _ticker.stop();
      time.value = duration;
      phase = StoryPhase.ended;
      _player.pause();
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _ticker.dispose();
    _stateSub.cancel();
    _player.dispose();
    time.dispose();
    super.dispose();
  }
}
