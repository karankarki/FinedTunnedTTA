// Credit story player for Flutter apps: shows /player/ in a WebView and talks to it through a
// JavaScript channel. Copy this file into your app and add to pubspec.yaml:
//
//   webview_flutter: ^4.10.0
//   webview_flutter_android: ^4.0.0
//   webview_flutter_wkwebview: ^3.16.0
//
// Usage:
//
//   final player = GlobalKey<CreditStoryPlayerState>();
//   Scaffold(
//     backgroundColor: Colors.black,
//     body: SafeArea(
//       child: CreditStoryPlayer(
//         key: player,
//         baseUrl: 'https://your-backend.example.com',
//         storyUrl: storyUrlFromApi,           // story_url from POST /api/story/crif
//         onClose: () => Navigator.of(context).pop(),
//         onEvent: (e) => debugPrint('${e['type']} $e'),
//       ),
//     ),
//   );
//   player.currentState?.pause();  player.currentState?.seek(120);
//
// Pass storyUrl: null to show the player's loader straight away, then call
// player.currentState?.load(storyUrl) when your API call returns.
import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';
import 'package:webview_flutter_android/webview_flutter_android.dart';
import 'package:webview_flutter_wkwebview/webview_flutter_wkwebview.dart';

class CreditStoryPlayer extends StatefulWidget {
  const CreditStoryPlayer({
    super.key,
    required this.baseUrl,
    this.storyUrl,
    this.theme = 'dark',
    this.autoplay = true,
    this.showClose = true,
    this.onClose,
    this.onEvent,
  });

  /// Where the backend runs, e.g. https://your-backend.example.com
  final String baseUrl;

  /// `story_url` from the story API, e.g. /stories/<id>/story.hi.json. Null shows the loader.
  final String? storyUrl;

  /// 'dark' or 'light'.
  final String theme;

  /// Start playing without a tap (the WebView is set up to allow it).
  final bool autoplay;

  final bool showClose;

  /// The viewer tapped the close button in the player.
  final VoidCallback? onClose;

  /// Every player event: ready, loaded, play, pause, timeupdate, chapter, generation, ended, close, error, ...
  final void Function(Map<String, dynamic> event)? onEvent;

  @override
  State<CreditStoryPlayer> createState() => CreditStoryPlayerState();
}

class CreditStoryPlayerState extends State<CreditStoryPlayer> with WidgetsBindingObserver {
  static const _channel = 'StoryPlayerBridge';
  late final WebViewController _controller;
  Completer<void> _ready = Completer<void>();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);

    // Let the narration play inline and without an extra tap on both platforms.
    final PlatformWebViewControllerCreationParams params =
        WebViewPlatform.instance is WebKitWebViewPlatform
            ? WebKitWebViewControllerCreationParams(
                allowsInlineMediaPlayback: true,
                mediaTypesRequiringUserAction: const <PlaybackMediaTypes>{},
              )
            : const PlatformWebViewControllerCreationParams();
    final controller = WebViewController.fromPlatformCreationParams(params)
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(widget.theme == 'light' ? const Color(0xFFF5F7FB) : const Color(0xFF000000))
      ..addJavaScriptChannel(_channel, onMessageReceived: _onMessage);
    if (controller.platform is AndroidWebViewController) {
      (controller.platform as AndroidWebViewController).setMediaPlaybackRequiresUserGesture(false);
    }
    _controller = controller;
    _open(widget.storyUrl);
  }

  @override
  void didUpdateWidget(CreditStoryPlayer old) {
    super.didUpdateWidget(old);
    if (widget.storyUrl != old.storyUrl && widget.storyUrl != null) load(widget.storyUrl!);
  }

  void _open(String? storyUrl) {
    final base = widget.baseUrl.replaceAll(RegExp(r'/+$'), '');
    final url = Uri.parse('$base/player/').replace(queryParameters: {
      'embed': '1',
      'theme': widget.theme,
      'bridge': _channel,
      if (storyUrl != null) 'story': storyUrl,
      if (widget.autoplay) 'autoplay': '1',
      if (!widget.showClose) 'close': '0',
    });
    _ready = Completer<void>();
    _controller.loadRequest(url);
  }

  void _onMessage(JavaScriptMessage message) {
    final Object? decoded = jsonDecode(message.message);
    if (decoded is! Map<String, dynamic>) return;
    if (decoded['type'] == 'ready' && !_ready.isCompleted) _ready.complete();
    widget.onEvent?.call(decoded);
    if (decoded['type'] == 'close') widget.onClose?.call();
  }

  Future<void> _run(String js) async {
    await _ready.future;
    await _controller.runJavaScript(js);
  }

  // Controls. They wait until the player page has loaded.
  Future<void> play() => _run('StoryPlayer.play()');
  Future<void> pause() => _run('StoryPlayer.pause()');
  Future<void> toggle() => _run('StoryPlayer.toggle()');
  Future<void> seek(double seconds) => _run('StoryPlayer.seek($seconds)');
  Future<void> setLanguage(String code) => _run('StoryPlayer.setLanguage(${jsonEncode(code)})');
  Future<void> setCaptions(bool on) => _run('StoryPlayer.setCaptions($on)');
  Future<void> load(String storyUrl, {bool play = true}) =>
      _run('StoryPlayer.load(${jsonEncode(storyUrl)}, {play: $play})');

  // Stop the narration when the app goes to the background.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused && _ready.isCompleted) pause();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => WebViewWidget(controller: _controller);
}
