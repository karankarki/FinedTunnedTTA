import 'package:flutter/material.dart';

import 'controller.dart';
import 'kit.dart';
import 'models.dart';
import 'scenes.dart';

/// The "video frame" of a [StoryController]: the story drawn on its fixed design canvas
/// (360x640 by default), scaled to fit whatever space it gets and letterboxed, exactly like a
/// video. Nothing inside reads the real screen size, so it looks the same on every phone,
/// tablet and orientation.
class StoryStage extends StatelessWidget {
  const StoryStage({super.key, required this.controller, this.onClose});
  final StoryController controller;
  final VoidCallback? onClose;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final tl = c.timeline;
    switch (c.phase) {
      case StoryPhase.loading:
        return CanvasBox(canvas: tl?.canvas ?? const StoryCanvas(), child: _Message(
            canvas: tl?.canvas ?? const StoryCanvas(), palette: tl?.palette ?? const Palette(), busy: true, text: c.loadingMessage));
      case StoryPhase.error:
        return CanvasBox(canvas: tl?.canvas ?? const StoryCanvas(), child: _Message(
            canvas: tl?.canvas ?? const StoryCanvas(),
            palette: tl?.palette ?? const Palette(),
            text: c.error ?? 'Something went wrong.',
            action: ('Try again', c.retry)));
      case StoryPhase.intro:
        return StoryFrame(timeline: tl!, t: 0, mode: FrameMode.intro, onPlay: c.play);
      case StoryPhase.ended:
        return StoryFrame(timeline: tl!, t: tl.duration, mode: FrameMode.end, onReplay: c.replay, onClose: onClose);
      case StoryPhase.playing:
      case StoryPhase.paused:
        return ValueListenableBuilder<double>(
          valueListenable: c.time,
          builder: (context, t, _) => StoryFrame(timeline: tl!, t: t, captions: c.captionsOn),
        );
    }
  }
}

/// Scales a fixed-size design canvas to fit the available space (contain, letterboxed).
class CanvasBox extends StatelessWidget {
  const CanvasBox({super.key, required this.canvas, required this.child});
  final StoryCanvas canvas;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final frame = ColoredBox(
      color: canvas.background,
      child: FittedBox(
        fit: BoxFit.contain,
        alignment: Alignment.center,
        child: SizedBox(
          width: canvas.width,
          height: canvas.height,
          child: MediaQuery(
            // The canvas is scaled as a whole; system font scaling would break its layout.
            data: MediaQuery.of(context).copyWith(textScaler: TextScaler.noScaling),
            child: ClipRect(child: child),
          ),
        ),
      ),
    );
    return LayoutBuilder(builder: (context, box) {
      // Fill the space we're given (letterboxed like a video); with no bounds, be a 9:16 box.
      if (box.hasBoundedWidth && box.hasBoundedHeight) return SizedBox.expand(child: frame);
      return AspectRatio(aspectRatio: canvas.width / canvas.height, child: frame);
    });
  }
}

enum FrameMode { scene, intro, end }

/// One frame of the story at time [t], with no audio attached: the scene on screen (with its
/// crossfade), captions, or the intro / end card. Also usable for thumbnails and previews.
class StoryFrame extends StatelessWidget {
  const StoryFrame({
    super.key,
    required this.timeline,
    required this.t,
    this.mode = FrameMode.scene,
    this.captions = true,
    this.onPlay,
    this.onReplay,
    this.onClose,
  });

  final Timeline timeline;
  final double t;
  final FrameMode mode;
  final bool captions;
  final VoidCallback? onPlay, onReplay, onClose;

  static const _fade = 0.4; // seconds of crossfade between scenes

  @override
  Widget build(BuildContext context) {
    final tl = timeline;
    final Widget content = switch (mode) {
      FrameMode.intro => _IntroCard(timeline: tl, onPlay: onPlay ?? () {}),
      FrameMode.end => _EndCard(timeline: tl, onReplay: onReplay ?? () {}, onClose: onClose),
      FrameMode.scene => Stack(fit: StackFit.expand, children: [
          _scenes(tl, t),
          if (captions) _Captions(timeline: tl, t: t),
        ]),
    };
    return CanvasBox(canvas: tl.canvas, child: content);
  }

  Widget _scenes(Timeline tl, double t) {
    if (tl.scenes.isEmpty) return Haze(colors: tl.palette.theme('blue'), background: tl.canvas.background);
    final i = tl.sceneIndexAt(t);
    final scene = tl.scenes[i];
    final local = t - scene.start;
    final current = _layer(tl, scene, t);
    if (i == 0 || local >= _fade || local < 0) return current;
    final prev = tl.scenes[i - 1];
    // A scene that continues the previous one (same visual) swaps in with no transition.
    if (scene.continues || (prev.id == scene.id && prev.type == scene.type)) return current;
    final p = SceneClock.ease(local / _fade);
    return Stack(fit: StackFit.expand, children: [
      _layer(tl, prev, t),
      Opacity(opacity: p, child: Transform.translate(offset: Offset(0, (1 - p) * 18), child: current)),
    ]);
  }

  Widget _layer(Timeline tl, Scene scene, double t) {
    final c = tl.canvas;
    final clock = SceneClock(scene, t - scene.start, tl.palette, c);
    return Stack(fit: StackFit.expand, children: [
      Haze(colors: tl.palette.theme(scene.theme), background: c.background),
      Positioned(
        left: c.safeLeft,
        right: c.safeRight,
        top: c.safeTop,
        bottom: c.safeBottom,
        child: buildScene(clock),
      ),
    ]);
  }
}

// ------------------------------------------------------------------------------- captions

class _Captions extends StatelessWidget {
  const _Captions({required this.timeline, required this.t});
  final Timeline timeline;
  final double t;

  @override
  Widget build(BuildContext context) {
    final caption = timeline.captionAt(t);
    if (caption == null) return const SizedBox.shrink();
    final p = timeline.palette;
    final type = StoryType(p);
    final style = type.base(15, FontWeight.w700, p.text[0], height: 1.35);

    // Long sentences are shown a page (up to max_chars) at a time, like subtitles.
    final pages = caption.pages(timeline.canvas.captionMaxChars);
    InlineSpan span;
    if (pages.isEmpty) {
      span = TextSpan(text: caption.text, style: style);
    } else {
      var page = pages.first;
      for (final pg in pages) {
        if (pg.first.start <= t) page = pg;
      }
      span = TextSpan(children: [
        for (var i = 0; i < page.length; i++)
          TextSpan(
            text: i == 0 ? page[i].text : ' ${page[i].text}',
            style: page[i].start <= t ? style : style.copyWith(color: p.text[0].withValues(alpha: 0.42)),
          ),
      ]);
    }
    final c = timeline.canvas;
    return Positioned(
      left: 16,
      right: 16,
      bottom: c.captionBottom,
      child: Center(
        child: ConstrainedBox(
          constraints: BoxConstraints(maxHeight: c.safeBottom - c.captionBottom - 8),
          child: FittedBox(
            fit: BoxFit.scaleDown,
            child: Container(
              width: c.width - 32,
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: const Color(0xCC000000),
                borderRadius: BorderRadius.circular(14),
                border: Border.all(color: p.line),
              ),
              child: Text.rich(span, textAlign: TextAlign.center, maxLines: 3, overflow: TextOverflow.ellipsis),
            ),
          ),
        ),
      ),
    );
  }
}

// ------------------------------------------------------------------------- intro / end card

class _IntroCard extends StatelessWidget {
  const _IntroCard({required this.timeline, required this.onPlay});
  final Timeline timeline;
  final VoidCallback onPlay;

  @override
  Widget build(BuildContext context) {
    final p = timeline.palette;
    final t = StoryType(p);
    final c = timeline.canvas;
    final intro = timeline.intro;
    final badges = [for (final b in (intro['badges'] as List? ?? const [])) if (b is Map) b];
    return Stack(fit: StackFit.expand, children: [
      Haze(colors: p.theme('blue'), background: c.background),
      Padding(
        padding: EdgeInsets.fromLTRB(c.safeLeft + 4, c.safeTop + 24, c.safeRight + 4, 40),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (timeline.brand.isNotEmpty)
            Row(children: [
              IconBadge('shield', color: p.accent, size: 30),
              const SizedBox(width: 10),
              Text(timeline.brand, style: t.base(15, FontWeight.w800, p.text[0])),
            ]),
          const Spacer(),
          AccentText('${intro['title'] ?? ''}', style: t.title.copyWith(fontSize: 30), accent: p.accent, maxLines: 4),
          if (intro['subtitle'] != null) ...[
            const SizedBox(height: 12),
            Text('${intro['subtitle']}', style: t.subtitle.copyWith(fontSize: 15), maxLines: 3),
          ],
          const SizedBox(height: 22),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final b in badges) Pill('${b['text'] ?? ''}', color: p.text[1], type: t, icon: storyIcon(b['icon'])),
          ]),
          const SizedBox(height: 34),
          _Button(label: 'Play your story', icon: Icons.play_arrow_rounded, palette: p, onTap: onPlay),
          const SizedBox(height: 10),
          Center(child: Text(_mmss(timeline.duration), style: t.small)),
        ]),
      ),
    ]);
  }
}

class _EndCard extends StatelessWidget {
  const _EndCard({required this.timeline, required this.onReplay, this.onClose});
  final Timeline timeline;
  final VoidCallback onReplay;
  final VoidCallback? onClose;

  @override
  Widget build(BuildContext context) {
    final p = timeline.palette;
    final t = StoryType(p);
    final c = timeline.canvas;
    final end = timeline.endCard;
    final recap = [for (final r in (end['recap'] as List? ?? const [])) if (r is Map) r];
    return Stack(fit: StackFit.expand, children: [
      Haze(colors: p.theme('violet'), background: c.background),
      Padding(
        padding: EdgeInsets.fromLTRB(c.safeLeft, c.safeTop, c.safeRight, 24),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Expanded(
            child: FittedBox(
              fit: BoxFit.scaleDown,
              alignment: Alignment.topCenter,
              child: SizedBox(
                width: c.contentWidth,
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  AccentText('${end['title'] ?? ''}', style: t.title, accent: p.accent, maxLines: 3),
                  if (end['text'] != null) ...[const SizedBox(height: 8), Text('${end['text']}', style: t.subtitle)],
                  const SizedBox(height: 18),
                  if (recap.isNotEmpty)
                    StoryCard(
                      p: p,
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                      child: Column(children: [
                        for (var i = 0; i < recap.length; i++)
                          Container(
                            padding: const EdgeInsets.symmetric(vertical: 12),
                            decoration: BoxDecoration(border: i == 0 ? null : Border(top: BorderSide(color: p.line))),
                            child: Row(children: [
                              Container(width: 8, height: 8, decoration: BoxDecoration(shape: BoxShape.circle, color: p.tone(recap[i]['tone']))),
                              const SizedBox(width: 10),
                              Expanded(child: Text('${recap[i]['label'] ?? ''}', style: t.bodyDim.copyWith(fontSize: 13.5, color: p.text[1]))),
                              Text('${recap[i]['value'] ?? ''}', style: t.base(13.5, FontWeight.w800, p.text[0])),
                            ]),
                          ),
                      ]),
                    ),
                ]),
              ),
            ),
          ),
          const SizedBox(height: 16),
          _Button(label: '${end['primary_label'] ?? 'Replay story'}', icon: Icons.replay_rounded, palette: p, onTap: onReplay),
          if (onClose != null) ...[
            const SizedBox(height: 10),
            _Button(label: '${end['secondary_label'] ?? 'Close'}', palette: p, onTap: onClose!, filled: false),
          ],
          if (end['disclaimer'] != null) ...[
            const SizedBox(height: 14),
            Text('${end['disclaimer']}', style: t.small.copyWith(fontSize: 10), textAlign: TextAlign.center),
          ],
        ]),
      ),
    ]);
  }
}

class _Message extends StatelessWidget {
  const _Message({required this.canvas, required this.palette, required this.text, this.busy = false, this.action});
  final StoryCanvas canvas;
  final Palette palette;
  final String text;
  final bool busy;
  final (String, Future<void> Function())? action;

  @override
  Widget build(BuildContext context) {
    final t = StoryType(palette);
    return Stack(fit: StackFit.expand, children: [
      Haze(colors: palette.theme('blue'), background: canvas.background),
      Padding(
        padding: const EdgeInsets.all(32),
        child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
          if (busy)
            SizedBox(width: 34, height: 34, child: CircularProgressIndicator(strokeWidth: 3, color: palette.accent))
          else
            Icon(Icons.error_outline_rounded, size: 40, color: palette.tone('bad')),
          const SizedBox(height: 20),
          Text(text, style: t.body.copyWith(color: palette.text[1]), textAlign: TextAlign.center),
          if (action != null) ...[
            const SizedBox(height: 22),
            _Button(label: action!.$1, palette: palette, onTap: () => action!.$2()),
          ],
        ]),
      ),
    ]);
  }
}

class _Button extends StatelessWidget {
  const _Button({required this.label, required this.palette, required this.onTap, this.icon, this.filled = true});
  final String label;
  final Palette palette;
  final VoidCallback onTap;
  final IconData? icon;
  final bool filled;

  @override
  Widget build(BuildContext context) {
    final t = StoryType(palette);
    final fg = filled ? Colors.white : palette.text[0];
    return Material(
      color: filled ? palette.accent : Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: filled ? BorderSide.none : BorderSide(color: palette.text[2].withValues(alpha: 0.5)),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: SizedBox(
          height: 50,
          child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            if (icon != null) ...[Icon(icon, color: fg, size: 22), const SizedBox(width: 8)],
            Text(label, style: t.base(15, FontWeight.w800, fg)),
          ]),
        ),
      ),
    );
  }
}

String _mmss(double s) {
  final total = s.isFinite ? s.round() : 0;
  return '${total ~/ 60}:${(total % 60).toString().padLeft(2, '0')}';
}
