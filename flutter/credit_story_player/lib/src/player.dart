import 'package:flutter/material.dart';

import 'controller.dart';
import 'kit.dart';
import 'models.dart';
import 'stage.dart';

/// Full player: top bar (close, chapter, captions, language), the video frame, and a control
/// bar (play/pause, chapter-aware seek bar, time). The bars sit outside the frame, so they never
/// cover the story; the frame letterboxes into whatever space is left, on any screen size.
class CreditStoryPlayer extends StatefulWidget {
  const CreditStoryPlayer({super.key, required this.controller, this.onClose});
  final StoryController controller;
  final VoidCallback? onClose;

  @override
  State<CreditStoryPlayer> createState() => _CreditStoryPlayerState();
}

class _CreditStoryPlayerState extends State<CreditStoryPlayer> with WidgetsBindingObserver {
  StoryController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused || state == AppLifecycleState.inactive) c.pause();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final tl = c.timeline;
        final palette = tl?.palette ?? const Palette();
        final showControls = tl != null && (c.phase == StoryPhase.playing || c.phase == StoryPhase.paused);
        return ColoredBox(
          color: tl?.canvas.background ?? Colors.black,
          child: SafeArea(
            child: Column(children: [
              _TopBar(controller: c, palette: palette, onClose: widget.onClose, showMeta: showControls),
              Expanded(
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: showControls ? c.toggle : null,
                  child: Stack(fit: StackFit.expand, children: [
                    StoryStage(controller: c, onClose: widget.onClose),
                    if (c.phase == StoryPhase.paused) const _PausedBadge(),
                  ]),
                ),
              ),
              if (showControls) _ControlBar(controller: c, palette: palette),
            ]),
          ),
        );
      },
    );
  }
}

class _PausedBadge extends StatelessWidget {
  const _PausedBadge();

  @override
  Widget build(BuildContext context) => IgnorePointer(
        child: Center(
          child: Container(
            width: 72,
            height: 72,
            decoration: const BoxDecoration(shape: BoxShape.circle, color: Color(0x99000000)),
            child: const Icon(Icons.play_arrow_rounded, color: Colors.white, size: 44),
          ),
        ),
      );
}

class _TopBar extends StatelessWidget {
  const _TopBar({required this.controller, required this.palette, required this.showMeta, this.onClose});
  final StoryController controller;
  final Palette palette;
  final bool showMeta;
  final VoidCallback? onClose;

  @override
  Widget build(BuildContext context) {
    final t = StoryType(palette);
    final c = controller;
    return SizedBox(
      height: 52,
      child: Row(children: [
        const SizedBox(width: 4),
        if (onClose != null)
          IconButton(onPressed: onClose, icon: Icon(Icons.close_rounded, color: palette.text[0]), tooltip: 'Close')
        else
          const SizedBox(width: 12),
        Expanded(
          child: showMeta
              ? ValueListenableBuilder<double>(
                  valueListenable: c.time,
                  builder: (context, time, _) => Text(
                    c.timeline?.chapterAt(time)?.title ?? '',
                    style: t.base(14, FontWeight.w700, palette.text[1]),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                )
              : const SizedBox.shrink(),
        ),
        if (showMeta) ...[
          if (c.languages.length > 1)
            for (var i = 0; i < c.languages.length; i++)
              Padding(
                padding: const EdgeInsets.only(right: 6),
                child: _Chip(
                  label: c.languages[i].label,
                  selected: i == c.languageIndex,
                  palette: palette,
                  onTap: () => c.setLanguage(i),
                ),
              ),
          IconButton(
            onPressed: c.toggleCaptions,
            tooltip: 'Captions',
            icon: Icon(c.captionsOn ? Icons.closed_caption_rounded : Icons.closed_caption_off_outlined, color: palette.text[0]),
          ),
        ],
        const SizedBox(width: 4),
      ]),
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({required this.label, required this.selected, required this.palette, required this.onTap});
  final String label;
  final bool selected;
  final Palette palette;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: selected ? palette.text[0] : palette.surface[1],
            borderRadius: BorderRadius.circular(99),
          ),
          child: Text(label, style: StoryType(palette).base(12, FontWeight.w800, selected ? Colors.black : palette.text[1], height: 1.1)),
        ),
      );
}

class _ControlBar extends StatelessWidget {
  const _ControlBar({required this.controller, required this.palette});
  final StoryController controller;
  final Palette palette;

  @override
  Widget build(BuildContext context) {
    final c = controller;
    final t = StoryType(palette);
    return Padding(
      padding: const EdgeInsets.fromLTRB(8, 4, 16, 10),
      child: Row(children: [
        IconButton(
          onPressed: c.toggle,
          iconSize: 34,
          icon: Icon(c.isPlaying ? Icons.pause_rounded : Icons.play_arrow_rounded, color: palette.text[0]),
          tooltip: c.isPlaying ? 'Pause' : 'Play',
        ),
        const SizedBox(width: 4),
        Expanded(child: SeekBar(controller: c, palette: palette)),
        const SizedBox(width: 12),
        ValueListenableBuilder<double>(
          valueListenable: c.time,
          builder: (context, time, _) => Text(
            '${_mmss(time)} / ${_mmss(c.duration)}',
            style: t.base(12, FontWeight.w700, palette.text[2]).copyWith(fontFeatures: const [FontFeature.tabularFigures()]),
          ),
        ),
      ]),
    );
  }
}

/// One continuous line with a gap at each chapter; tap or drag anywhere to seek. While dragging,
/// a bubble shows the time and chapter under the finger, and the story jumps there on release.
class SeekBar extends StatefulWidget {
  const SeekBar({super.key, required this.controller, required this.palette});
  final StoryController controller;
  final Palette palette;

  @override
  State<SeekBar> createState() => _SeekBarState();
}

class _SeekBarState extends State<SeekBar> {
  double? _drag; // seconds under the finger while dragging

  double _at(double dx, double width) => (dx / width).clamp(0.0, 1.0) * widget.controller.duration;

  @override
  Widget build(BuildContext context) {
    final c = widget.controller;
    final p = widget.palette;
    final total = c.duration <= 0 ? 1.0 : c.duration;
    final chapters = c.timeline?.chapters ?? const <Chapter>[];
    return LayoutBuilder(builder: (context, box) {
      final w = box.maxWidth;
      return GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTapUp: (d) => c.seek(_at(d.localPosition.dx, w)),
        onHorizontalDragStart: (d) => setState(() => _drag = _at(d.localPosition.dx, w)),
        onHorizontalDragUpdate: (d) => setState(() => _drag = _at(d.localPosition.dx, w)),
        onHorizontalDragEnd: (_) {
          final to = _drag;
          setState(() => _drag = null);
          if (to != null) c.seek(to);
        },
        child: SizedBox(
          height: 40,
          child: ValueListenableBuilder<double>(
            valueListenable: c.time,
            builder: (context, time, _) {
              final shown = _drag ?? time;
              final x = w * (shown / total).clamp(0.0, 1.0);
              return Stack(clipBehavior: Clip.none, children: [
                Positioned(left: 0, right: 0, top: 18, height: 4, child: _track(w, total, chapters, shown)),
                Positioned(
                  left: x - (_drag != null ? 9 : 7),
                  top: _drag != null ? 11 : 13,
                  child: Container(
                    width: _drag != null ? 18 : 14,
                    height: _drag != null ? 18 : 14,
                    decoration: const BoxDecoration(shape: BoxShape.circle, color: Colors.white),
                  ),
                ),
                if (_drag != null)
                  Positioned(
                    left: (x - 70).clamp(0.0, w - 140),
                    top: -34,
                    width: 140,
                    child: _Bubble(
                      text: '${_mmss(_drag!)} · ${c.timeline?.chapterAt(_drag!)?.title ?? ''}',
                      palette: p,
                    ),
                  ),
              ]);
            },
          ),
        ),
      );
    });
  }

  Widget _track(double w, double total, List<Chapter> chapters, double shown) {
    final p = widget.palette;
    final starts = chapters.isEmpty ? [0.0] : [for (final ch in chapters) ch.start];
    final pieces = <Widget>[];
    for (var i = 0; i < starts.length; i++) {
      final a = starts[i];
      final z = i + 1 < starts.length ? starts[i + 1] : total;
      final fill = ((shown - a) / (z - a)).clamp(0.0, 1.0);
      pieces.add(Expanded(
        flex: ((z - a) * 1000).round().clamp(1, 1 << 30),
        child: Padding(
          padding: EdgeInsets.only(right: i + 1 < starts.length ? 2 : 0),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(2),
            child: Stack(fit: StackFit.expand, children: [
              ColoredBox(color: p.text[0].withValues(alpha: 0.22)),
              FractionallySizedBox(
                alignment: Alignment.centerLeft,
                widthFactor: fill,
                heightFactor: 1,
                child: ColoredBox(color: p.accent),
              ),
            ]),
          ),
        ),
      ));
    }
    return Row(children: pieces);
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.text, required this.palette});
  final String text;
  final Palette palette;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
        decoration: BoxDecoration(color: palette.surface[2], borderRadius: BorderRadius.circular(8)),
        child: Text(
          text,
          textAlign: TextAlign.center,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: StoryType(palette).base(11, FontWeight.w700, palette.text[0], height: 1.1),
        ),
      );
}

String _mmss(double s) {
  final total = s.isFinite ? s.round() : 0;
  return '${total ~/ 60}:${(total % 60).toString().padLeft(2, '0')}';
}
