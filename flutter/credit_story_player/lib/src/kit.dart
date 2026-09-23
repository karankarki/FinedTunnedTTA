import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

import 'models.dart';

// Shared building blocks for scenes. Every size here is in canvas pixels (the 360x640 frame);
// the frame is scaled as a whole, so nothing in a scene reads the real screen size.

/// Where a scene is in its own timeline, and how far each beat has progressed.
class SceneClock {
  SceneClock(this.scene, this.local, this.palette, this.canvas);

  final Scene scene;

  /// Seconds since the scene started.
  final double local;
  final Palette palette;
  final StoryCanvas canvas;

  static double ease(double x) => Curves.easeOutCubic.transform(x.clamp(0.0, 1.0));

  double _p(double at, double dur) => ease((local - at) / dur);

  /// 0 → 1 as the scene's intro plays.
  double get intro {
    final b = scene.beat('intro');
    return _p(b?.at ?? 0, 0.55);
  }

  /// Progress of beat [action] ([index]). A beat the scene doesn't have follows the intro,
  /// unless [hiddenIfMissing] (for elements that must wait for their own cue).
  double beat(String action, {int? index, double dur = 0.5, bool hiddenIfMissing = false}) {
    final b = scene.beat(action, index);
    if (b == null) return hiddenIfMissing ? 0 : intro;
    return _p(b.at, b.duration ?? dur);
  }

  /// Seconds since beat [action] fired (negative before it).
  double since(String action, {int? index}) {
    final b = scene.beat(action, index);
    return b == null ? local : local - b.at;
  }

  /// Progress of list item [i] revealed by `action` beats. Items with no beat of their own
  /// appear with the closest earlier item; lists with no such beats appear with the intro.
  double item(int i, {String action = 'item', double dur = 0.5}) {
    if (!scene.hasAction(action)) return _p(0.08 * i, 0.55);
    Beat? best;
    for (final b in scene.beats) {
      if (b.action == action && b.index != null && b.index! <= i && (best == null || b.index! > best.index!)) best = b;
    }
    return best == null ? intro : _p(best.at, dur);
  }
}

/// Canvas text styles (Manrope, with Devanagari fallback for Hindi).
class StoryType {
  StoryType(this.p);
  final Palette p;

  /// Load Manrope / Noto Sans Devanagari through google_fonts (downloaded once, then cached).
  /// Set false if the app bundles these fonts itself (families 'Manrope', 'NotoSansDevanagari').
  static bool useGoogleFonts = true;

  static List<String>? _fallbackCache;
  static List<String> get _fallback => _fallbackCache ??= [
        useGoogleFonts ? (GoogleFonts.notoSansDevanagari().fontFamily ?? 'NotoSansDevanagari') : 'NotoSansDevanagari',
      ];

  TextStyle base(double size, FontWeight weight, Color color, {double height = 1.3, double spacing = 0}) =>
      (useGoogleFonts
              ? GoogleFonts.manrope(fontSize: size, fontWeight: weight, color: color, height: height, letterSpacing: spacing)
              : TextStyle(fontFamily: 'Manrope', fontSize: size, fontWeight: weight, color: color, height: height, letterSpacing: spacing))
          .copyWith(fontFamilyFallback: _fallback, decoration: TextDecoration.none);

  TextStyle get eyebrow => base(11.5, FontWeight.w800, p.text[2], spacing: 1.1);
  TextStyle get title => base(25, FontWeight.w800, p.text[0], height: 1.16, spacing: -0.4);
  TextStyle get subtitle => base(14, FontWeight.w500, p.text[2], height: 1.4);
  TextStyle get body => base(14.5, FontWeight.w700, p.text[0], height: 1.3);
  TextStyle get bodyDim => base(12.5, FontWeight.w500, p.text[2], height: 1.35);
  TextStyle get small => base(11.5, FontWeight.w600, p.text[2]);
  TextStyle number(double size, Color color) => base(size, FontWeight.w800, color, height: 1, spacing: -1)
      .copyWith(fontFeatures: const [FontFeature.tabularFigures()]);
}

/// Text where `*this part*` is drawn in the accent colour.
class AccentText extends StatelessWidget {
  const AccentText(this.text, {super.key, required this.style, required this.accent, this.maxLines, this.align});
  final String text;
  final TextStyle style;
  final Color accent;
  final int? maxLines;
  final TextAlign? align;

  @override
  Widget build(BuildContext context) {
    final parts = text.split('*');
    return Text.rich(
      TextSpan(children: [
        for (var i = 0; i < parts.length; i++)
          if (parts[i].isNotEmpty) TextSpan(text: parts[i], style: i.isOdd ? style.copyWith(color: accent) : style),
      ]),
      maxLines: maxLines,
      overflow: maxLines == null ? null : TextOverflow.ellipsis,
      textAlign: align,
    );
  }
}

const Map<String, IconData> _icons = {
  'alert': Icons.error_outline_rounded,
  'bolt': Icons.bolt_rounded,
  'bulb': Icons.lightbulb_outline_rounded,
  'calendar': Icons.calendar_month_rounded,
  'card': Icons.credit_card_rounded,
  'check': Icons.check_circle_outline_rounded,
  'clock': Icons.schedule_rounded,
  'doc': Icons.description_outlined,
  'home': Icons.home_outlined,
  'info': Icons.info_outline_rounded,
  'mail': Icons.mail_outline_rounded,
  'phone': Icons.phone_outlined,
  'search': Icons.search_rounded,
  'shield': Icons.verified_user_outlined,
  'trend': Icons.trending_up_rounded,
  'wallet': Icons.account_balance_wallet_outlined,
  'x': Icons.cancel_outlined,
};

IconData storyIcon(Object? name) => _icons[name] ?? Icons.info_outline_rounded;

/// Fades and lifts a child in as [p] goes 0 → 1.
class Reveal extends StatelessWidget {
  const Reveal(this.p, {super.key, required this.child, this.dy = 14, this.scale = false});
  final double p;
  final double dy;
  final bool scale;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    if (p <= 0) return Opacity(opacity: 0, child: child); // keep the layout, so nothing jumps
    Widget w = Transform.translate(offset: Offset(0, (1 - p) * dy), child: child);
    if (scale) w = Transform.scale(scale: 0.92 + 0.08 * p, child: w);
    return Opacity(opacity: p.clamp(0.0, 1.0), child: w);
  }
}

class StoryCard extends StatelessWidget {
  const StoryCard({super.key, required this.p, required this.child, this.padding = const EdgeInsets.all(14), this.border});
  final Palette p;
  final Widget child;
  final EdgeInsets padding;
  final Color? border;

  @override
  Widget build(BuildContext context) => Container(
        padding: padding,
        decoration: BoxDecoration(
          color: p.surface[0].withValues(alpha: 0.92),
          borderRadius: BorderRadius.circular(18),
          border: Border.all(color: border ?? p.line),
        ),
        child: child,
      );
}

class Pill extends StatelessWidget {
  const Pill(this.text, {super.key, required this.color, required this.type, this.icon});
  final String text;
  final Color color;
  final StoryType type;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(99)),
        child: Row(mainAxisSize: MainAxisSize.min, children: [
          if (icon != null) ...[Icon(icon, size: 13, color: color), const SizedBox(width: 5)],
          Text(text, style: type.base(11.5, FontWeight.w800, color, height: 1.1)),
        ]),
      );
}

class IconBadge extends StatelessWidget {
  const IconBadge(this.icon, {super.key, required this.color, this.size = 36});
  final Object? icon;
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        decoration: BoxDecoration(color: color.withValues(alpha: 0.15), borderRadius: BorderRadius.circular(size * 0.32)),
        child: Icon(storyIcon(icon), size: size * 0.52, color: color),
      );
}

/// The theme-tinted background of a scene (two soft glows on black).
class Haze extends StatelessWidget {
  const Haze({super.key, required this.colors, required this.background});
  final List<Color> colors;
  final Color background;

  @override
  Widget build(BuildContext context) => DecoratedBox(
        decoration: BoxDecoration(color: background),
        child: Stack(fit: StackFit.expand, children: [
          DecoratedBox(
            decoration: BoxDecoration(
              gradient: RadialGradient(
                center: const Alignment(-0.9, -1.0),
                radius: 1.1,
                colors: [colors[0].withValues(alpha: 0.42), colors[0].withValues(alpha: 0)],
              ),
            ),
          ),
          DecoratedBox(
            decoration: BoxDecoration(
              gradient: RadialGradient(
                center: const Alignment(1.0, 0.75),
                radius: 1.0,
                colors: [colors[1].withValues(alpha: 0.34), colors[1].withValues(alpha: 0)],
              ),
            ),
          ),
        ]),
      );
}

/// Standard scene layout inside the safe area: header (eyebrow, title, subtitle) and body as
/// one block, centred a little above the middle. The body is scaled down if it would not fit, so a scene can never
/// overflow, whatever the data.
class SceneScaffold extends StatelessWidget {
  const SceneScaffold({super.key, required this.clock, required this.body, this.eyebrowTrailing});
  final SceneClock clock;
  final Widget body;
  final Widget? eyebrowTrailing;

  @override
  Widget build(BuildContext context) {
    final p = clock.palette;
    final t = StoryType(p);
    final props = clock.scene.props;
    final eyebrow = props['eyebrow'] as String?;
    final title = props['title'] as String?;
    final subtitle = props['subtitle'] as String?;
    final width = clock.canvas.contentWidth;
    return Align(
      alignment: const Alignment(0, -0.3),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Reveal(
        clock.intro,
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          if (eyebrow != null || eyebrowTrailing != null)
            Row(children: [
              if (eyebrow != null)
                Flexible(child: Text(eyebrow.toUpperCase(), style: t.eyebrow, maxLines: 1, overflow: TextOverflow.ellipsis)),
              if (eyebrowTrailing != null) ...[const SizedBox(width: 8), eyebrowTrailing!],
            ]),
          if (title != null) ...[
            const SizedBox(height: 8),
            AccentText(title, style: t.title, accent: p.accent, maxLines: 3),
          ],
          if (subtitle != null && subtitle.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(subtitle, style: t.subtitle, maxLines: 2, overflow: TextOverflow.ellipsis),
          ],
        ]),
      ),
      const SizedBox(height: 18),
      Flexible(
        child: FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.topCenter,
          child: SizedBox(width: width, child: body),
        ),
      ),
      ]),
    );
  }
}

/// Paints an arc gauge: [bands] as coloured segments, or a single track + progress.
class ArcPainter extends CustomPainter {
  ArcPainter({required this.track, required this.segments, this.stroke = 14, this.sweep = math.pi, this.startAngle = math.pi});

  final Color track;
  final List<(double from, double to, Color color)> segments; // fractions 0..1 of the sweep
  final double stroke, sweep, startAngle;

  @override
  void paint(Canvas canvas, Size size) {
    final r = math.min(size.width / 2, sweep >= 2 * math.pi - 0.01 ? size.height / 2 : size.height) - stroke / 2;
    final c = Offset(size.width / 2, sweep >= 2 * math.pi - 0.01 ? size.height / 2 : size.height - stroke / 2);
    final rect = Rect.fromCircle(center: c, radius: r);
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(rect, startAngle, sweep, false, paint..color = track);
    for (final (from, to, color) in segments) {
      if (to <= from) continue;
      canvas.drawArc(rect, startAngle + sweep * from, sweep * (to - from), false, paint..color = color);
    }
  }

  @override
  bool shouldRepaint(ArcPainter old) => true;
}
