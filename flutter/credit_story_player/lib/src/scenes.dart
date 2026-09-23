import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'kit.dart';
import 'models.dart';

// One widget per scene `type` in the timeline JSON. Each builds purely from the scene clock
// (seconds since the scene started), so any frame can be drawn for any time.

Widget buildScene(SceneClock c) {
  switch (c.scene.type) {
    case 'title':
      return TitleScene(c);
    case 'score_dial':
      return ScoreDialScene(c);
    case 'factor_overview':
      return FactorOverviewScene(c);
    case 'factor_insight':
      return FactorInsightScene(c);
    case 'points':
      return PointsScene(c);
    case 'stats':
      return StatsScene(c);
    case 'bars':
      return BarsScene(c);
    case 'history_grid':
      return HistoryGridScene(c);
    case 'list':
      return ListScene(c);
    case 'action_plan':
      return ActionPlanScene(c);
    default:
      return SceneScaffold(clock: c, body: const SizedBox.shrink()); // unknown type: header only
  }
}

List<Map<String, dynamic>> _list(Object? v) =>
    [for (final e in (v is List ? v : const [])) if (e is Map) e.cast<String, dynamic>()];

Map<String, dynamic> _map(Object? v) => v is Map ? v.cast<String, dynamic>() : const {};

double _num(Object? v, [double fallback = 0]) => v is num ? v.toDouble() : fallback;

/// "100%" counted up as p goes 0 → 1; values that aren't a plain number are shown as they are.
String countUp(Object? value, double p) {
  final s = '${value ?? ''}';
  final m = RegExp(r'^(\D{0,2})(\d{1,6})(%?)$').firstMatch(s);
  if (m == null) return s;
  return '${m[1]}${(int.parse(m[2]!) * p).round()}${m[3]}';
}

// ------------------------------------------------------------------------------------ title

class TitleScene extends StatelessWidget {
  const TitleScene(this.c, {super.key});
  final SceneClock c;

  @override
  Widget build(BuildContext context) {
    final t = StoryType(c.palette);
    final chips = _list(c.scene.props['chips']);
    return SceneScaffold(
      clock: c,
      body: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const SizedBox(height: 8),
        for (var i = 0; i < chips.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Reveal(
              c.item(i, action: 'chip'),
              child: StoryCard(
                p: c.palette,
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                child: Row(children: [
                  IconBadge(chips[i]['icon'], color: c.palette.accent, size: 34),
                  const SizedBox(width: 12),
                  Expanded(child: Text('${chips[i]['text'] ?? ''}', style: t.body)),
                ]),
              ),
            ),
          ),
      ]),
    );
  }
}

// ------------------------------------------------------------------------------- score dial

class ScoreDialScene extends StatelessWidget {
  const ScoreDialScene(this.c, {super.key});
  final SceneClock c;

  @override
  Widget build(BuildContext context) {
    final p = c.palette;
    final t = StoryType(p);
    final props = c.scene.props;
    final min = _num(props['min'], 300), max = _num(props['max'], 900);
    final score = _num(props['score'], min);
    final from = _num(props['count_from'], min);
    final reveal = c.beat('reveal', dur: 2.0);
    final value = from + (score - from) * reveal;
    final frac = ((value - min) / (max - min)).clamp(0.0, 1.0);
    final status = _map(props['status']);
    final toneColor = p.tone(status['tone']);
    final bands = _list(props['bands']);
    final percentile = _map(props['percentile']);
    final lenders = _map(props['lenders']);
    final lenderItems = _list(lenders['items']);

    final segments = <(double, double, Color)>[];
    for (final b in bands) {
      final a = ((_num(b['from']) - min) / (max - min)).clamp(0.0, 1.0);
      final z = ((_num(b['to']) + 1 - min) / (max - min)).clamp(0.0, 1.0);
      final color = parseColor(b['color'], p.accent);
      segments.add((a + 0.004, z - 0.004, color.withValues(alpha: 0.22)));
      if (frac > a) segments.add((a + 0.004, math.min(frac, z - 0.004), color));
    }

    return SceneScaffold(
      clock: c,
      body: Column(children: [
        Reveal(
          c.intro,
          scale: true,
          child: SizedBox(
            width: 290,
            height: 168,
            child: Stack(clipBehavior: Clip.none, children: [
              Positioned.fill(
                child: CustomPaint(painter: _DialPainter(segments: segments, knob: frac, knobColor: toneColor, track: p.surface[1])),
              ),
              Positioned(
                left: 0,
                right: 0,
                bottom: 6,
                child: Column(children: [
                  Text(value.round().toString(), style: t.number(58, p.text[0])),
                  const SizedBox(height: 8),
                  Opacity(
                    opacity: SceneClock.ease((reveal - 0.85) / 0.15),
                    child: Pill('${status['text'] ?? ''}', color: toneColor, type: t),
                  ),
                ]),
              ),
              Positioned(left: 4, bottom: -18, child: Text(min.round().toString(), style: t.small)),
              Positioned(right: 4, bottom: -18, child: Text(max.round().toString(), style: t.small)),
            ]),
          ),
        ),
        const SizedBox(height: 34),
        if (percentile['text'] != null)
          Reveal(
            c.scene.hasAction('percentile') ? c.beat('percentile') : reveal,
            child: StoryCard(
              p: p,
              child: Row(children: [
                IconBadge('trend', color: p.accent),
                const SizedBox(width: 12),
                Expanded(child: AccentText('${percentile['text']}', style: t.body, accent: p.accent)),
              ]),
            ),
          ),
        if (lenderItems.isNotEmpty) ...[
          const SizedBox(height: 12),
          Reveal(
            c.beat('lenders', hiddenIfMissing: true),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${lenders['title'] ?? ''}'.toUpperCase(), style: t.eyebrow),
              const SizedBox(height: 8),
              Row(children: [
                for (var i = 0; i < lenderItems.length; i++) ...[
                  if (i > 0) const SizedBox(width: 8),
                  Expanded(
                    child: StoryCard(
                      p: p,
                      padding: const EdgeInsets.all(10),
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Icon(storyIcon(lenderItems[i]['icon']), size: 18, color: toneColor),
                        const SizedBox(height: 8),
                        Text('${lenderItems[i]['value'] ?? ''}',
                            style: t.base(14, FontWeight.w800, p.text[0]), maxLines: 1, overflow: TextOverflow.ellipsis),
                        Text('${lenderItems[i]['label'] ?? ''}', style: t.small, maxLines: 1, overflow: TextOverflow.ellipsis),
                      ]),
                    ),
                  ),
                ],
              ]),
            ]),
          ),
        ],
      ]),
    );
  }
}

class _DialPainter extends CustomPainter {
  _DialPainter({required this.segments, required this.knob, required this.knobColor, required this.track});
  final List<(double, double, Color)> segments;
  final double knob;
  final Color knobColor, track;

  @override
  void paint(Canvas canvas, Size size) {
    const stroke = 16.0;
    final r = size.width / 2 - stroke / 2;
    final c = Offset(size.width / 2, size.width / 2);
    final rect = Rect.fromCircle(center: c, radius: r);
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = stroke
      ..strokeCap = StrokeCap.butt;
    canvas.drawArc(rect, math.pi, math.pi, false, paint..color = track);
    for (final (a, z, color) in segments) {
      if (z > a) canvas.drawArc(rect, math.pi + math.pi * a, math.pi * (z - a), false, paint..color = color);
    }
    final angle = math.pi + math.pi * knob;
    final k = c + Offset(math.cos(angle), math.sin(angle)) * r;
    canvas.drawCircle(k, 11, Paint()..color = const Color(0xFFFFFFFF));
    canvas.drawCircle(k, 6.5, Paint()..color = knobColor);
  }

  @override
  bool shouldRepaint(_DialPainter old) => true;
}

// -------------------------------------------------------------------------- factor overview

class FactorOverviewScene extends StatelessWidget {
  const FactorOverviewScene(this.c, {super.key});
  final SceneClock c;

  @override
  Widget build(BuildContext context) {
    final p = c.palette;
    final t = StoryType(p);
    final items = _list(c.scene.props['items']);
    final note = c.scene.props['note'] as String?;
    return SceneScaffold(
      clock: c,
      body: Column(children: [
        for (var i = 0; i < items.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Reveal(
              c.item(i),
              child: StoryCard(
                p: p,
                child: Row(children: [
                  IconBadge(items[i]['icon'], color: p.tone(items[i]['tone']), size: 40),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('${items[i]['name'] ?? ''}', style: t.body, maxLines: 1, overflow: TextOverflow.ellipsis),
                      const SizedBox(height: 3),
                      Text('${items[i]['impact'] ?? ''}', style: t.small),
                    ]),
                  ),
                  const SizedBox(width: 8),
                  Text(countUp(items[i]['result'], c.item(i, dur: 1.0)), style: t.number(22, p.tone(items[i]['tone']))),
                ]),
              ),
            ),
          ),
        if (note != null) ...[
          const SizedBox(height: 4),
          Reveal(
            c.item(items.length - 1),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Icon(Icons.info_outline_rounded, size: 15, color: p.text[2]),
              const SizedBox(width: 8),
              Expanded(child: Text(note, style: t.bodyDim)),
            ]),
          ),
        ],
      ]),
    );
  }
}

// --------------------------------------------------------------------------- factor insight

class FactorInsightScene extends StatelessWidget {
  const FactorInsightScene(this.c, {super.key});
  final SceneClock c;

  @override
  Widget build(BuildContext context) {
    final p = c.palette;
    final t = StoryType(p);
    final props = c.scene.props;
    final metric = _map(props['metric']);
    final detail = _map(props['detail']);
    final status = _map(metric['status']);
    final tone = p.tone(status['tone']);
    final v = c.beat('value', dur: 1.2);
    final explainer = props['explainer'] as String?;

    final Widget visual;
    if (metric['visual'] == 'ring') {
      visual = SizedBox(
        width: 112,
        height: 112,
        child: Stack(alignment: Alignment.center, children: [
          Positioned.fill(
            child: CustomPaint(
              painter: ArcPainter(
                track: p.surface[2],
                segments: [(0, _num(metric['progress']).clamp(0.0, 1.0) * v, tone)],
                stroke: 11,
                sweep: 2 * math.pi - 0.0001,
                startAngle: -math.pi / 2,
              ),
            ),
          ),
          Text(countUp(metric['value'], v), style: t.number(26, p.text[0])),
        ]),
      );
    } else {
      visual = Container(
        width: 112,
        height: 112,
        alignment: Alignment.center,
        decoration: BoxDecoration(shape: BoxShape.circle, color: tone.withValues(alpha: 0.12), border: Border.all(color: tone, width: 3)),
        child: Text(countUp(metric['value'], v), style: t.number(40, p.text[0])),
      );
    }

    return SceneScaffold(
      clock: c,
      eyebrowTrailing: props['impact'] != null ? Pill('${props['impact']}', color: p.accent, type: t) : null,
      body: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (explainer != null) ...[
          Reveal(c.intro, child: Text(explainer, style: t.subtitle.copyWith(color: p.text[1]))),
          const SizedBox(height: 16),
        ],
        Reveal(
          c.scene.hasAction('value') ? c.beat('value') : c.intro,
          child: StoryCard(
            p: p,
            padding: const EdgeInsets.all(16),
            child: Row(children: [
              visual,
              const SizedBox(width: 16),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('${metric['label'] ?? ''}', style: t.body.copyWith(fontSize: 16)),
                  const SizedBox(height: 4),
                  Text('${metric['sublabel'] ?? ''}', style: t.bodyDim),
                  if (status['text'] != null) ...[const SizedBox(height: 10), Pill('${status['text']}', color: tone, type: t)],
                ]),
              ),
            ]),
          ),
        ),
        if (detail.isNotEmpty) ...[
          const SizedBox(height: 12),
          Reveal(c.beat('detail', hiddenIfMissing: false), child: _Detail(c: c, detail: detail)),
        ],
      ]),
    );
  }
}

class _Detail extends StatelessWidget {
  const _Detail({required this.c, required this.detail});
  final SceneClock c;
  final Map<String, dynamic> detail;

  @override
  Widget build(BuildContext context) {
    final p = c.palette;
    final t = StoryType(p);
    final d = c.beat('detail', dur: 0.9);
    Widget content;
    switch (detail['type']) {
      case 'months':
        final months = _list(detail['months']);
        content = Row(children: [
          for (var i = 0; i < months.length; i++)
            Expanded(
              child: Opacity(
                opacity: SceneClock.ease(d * months.length - i),
                child: Column(children: [
                  _MonthDot(state: months[i]['state'], p: p),
                  const SizedBox(height: 6),
                  Text('${months[i]['label'] ?? ''}', style: t.small),
                ]),
              ),
            ),
        ]);
      case 'meter':
        final value = detail['value'];
        final ideal = _num(detail['ideal_max'], 30);
        final fill = value is num ? (value.toDouble() / 100).clamp(0.0, 1.0) * d : 0.0;
        final over = value is num && value > ideal;
        content = Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(value is num ? '${value.round()}%' : '—', style: t.number(22, over ? p.tone('bad') : p.tone('good'))),
            const Spacer(),
            Text('Ideal under ${ideal.round()}%', style: t.small),
          ]),
          const SizedBox(height: 10),
          LayoutBuilder(builder: (context, box) {
            final w = box.maxWidth;
            return SizedBox(
              height: 22,
              child: Stack(clipBehavior: Clip.none, children: [
                Positioned(left: 0, right: 0, top: 6, height: 10, child: _bar(p.surface[2])),
                Positioned(left: 0, width: w * fill, top: 6, height: 10, child: _bar(over ? p.tone('bad') : p.tone('good'))),
                Positioned(left: w * ideal / 100 - 1, top: 0, width: 2, height: 22, child: ColoredBox(color: p.text[0])),
              ]),
            );
          }),
        ]);
      case 'slots':
        final value = _num(detail['value']).round();
        final limit = _num(detail['limit'], 3).round();
        final count = math.max(value, limit);
        content = Row(children: [
          for (var i = 0; i < count; i++) ...[
            if (i > 0) const SizedBox(width: 8),
            Expanded(
              child: Container(
                height: 34,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(10),
                  color: i < value
                      ? (i < limit ? p.accent : p.tone('bad')).withValues(alpha: SceneClock.ease(d * count - i))
                      : p.surface[2],
                  border: i >= limit ? Border.all(color: p.tone('bad').withValues(alpha: 0.6)) : null,
                ),
              ),
            ),
          ],
        ]);
      default:
        content = const SizedBox.shrink();
    }
    return StoryCard(
      p: p,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (detail['title'] != null) ...[Text('${detail['title']}'.toUpperCase(), style: t.eyebrow), const SizedBox(height: 12)],
        content,
        if (detail['summary'] != null) ...[
          const SizedBox(height: 12),
          Text('${detail['summary']}', style: t.bodyDim.copyWith(color: p.text[1])),
        ],
      ]),
    );
  }

  Widget _bar(Color color) => DecoratedBox(decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(99)));
}

class _MonthDot extends StatelessWidget {
  const _MonthDot({required this.state, required this.p});
  final Object? state;
  final Palette p;

  @override
  Widget build(BuildContext context) {
    final color = p.cell(state);
    final icon = switch (state) {
      'ok' => Icons.check_rounded,
      'late' => Icons.priority_high_rounded,
      'severe' => Icons.close_rounded,
      _ => Icons.remove_rounded,
    };
    return Container(
      width: 32,
      height: 32,
      decoration: BoxDecoration(shape: BoxShape.circle, color: color.withValues(alpha: state == 'none' ? 1 : 0.18)),
      child: Icon(icon, size: 17, color: state == 'none' ? const Color(0x99FFFFFF) : color),
    );
  }
}

// ----------------------------------------------------------------------------------- points

class PointsScene extends StatelessWidget {
  const PointsScene(this.c, {super.key});
  final SceneClock c;

  @override
  Widget build(BuildContext context) {
    final p = c.palette;
    final t = StoryType(p);
    final items = _list(c.scene.props['items']);
    return SceneScaffold(
      clock: c,
      body: Column(children: [
        for (var i = 0; i < items.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 9),
            child: Reveal(
              c.item(i),
              child: StoryCard(
                p: p,
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  IconBadge(items[i]['icon'], color: p.accent),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Expanded(child: Text('${items[i]['title'] ?? ''}', style: t.body)),
                        if (items[i]['tag'] != null) ...[
                          const SizedBox(width: 6),
                          Pill('${items[i]['tag']}', color: p.text[1], type: t),
                        ],
                      ]),
                      if (items[i]['text'] != null) ...[
                        const SizedBox(height: 3),
                        Text('${items[i]['text']}', style: t.bodyDim),
                      ],
                    ]),
                  ),
                ]),
              ),
            ),
          ),
      ]),
    );
  }
}

// ------------------------------------------------------------------------------------ stats

class StatsScene extends StatelessWidget {
  const StatsScene(this.c, {super.key});
  final SceneClock c;

  @override
  Widget build(BuildContext context) {
    final p = c.palette;
    final t = StoryType(p);
    final tiles = _list(c.scene.props['tiles']);
    final w = (c.canvas.contentWidth - 10) / 2;
    return SceneScaffold(
      clock: c,
      body: Wrap(spacing: 10, runSpacing: 10, children: [
        for (var i = 0; i < tiles.length; i++)
          SizedBox(
            width: w,
            height: 118,
            child: Reveal(
              c.item(i),
              scale: true,
              child: StoryCard(
                p: p,
                padding: const EdgeInsets.all(12),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  IconBadge(tiles[i]['icon'], color: tiles[i]['tone'] != null ? p.tone(tiles[i]['tone']) : p.accent, size: 30),
                  const Spacer(),
                  FittedBox(
                    fit: BoxFit.scaleDown,
                    alignment: Alignment.centerLeft,
                    child: Text(countUp(tiles[i]['value'], c.item(i, dur: 0.9)),
                        style: t.number(24, tiles[i]['tone'] != null ? p.tone(tiles[i]['tone']) : p.text[0])),
                  ),
                  const SizedBox(height: 4),
                  Text('${tiles[i]['label'] ?? ''}', style: t.base(12.5, FontWeight.w700, p.text[1]), maxLines: 1, overflow: TextOverflow.ellipsis),
                  if (tiles[i]['sub'] != null)
                    Text('${tiles[i]['sub']}', style: t.small.copyWith(fontSize: 10.5), maxLines: 1, overflow: TextOverflow.ellipsis),
                ]),
              ),
            ),
          ),
      ]),
    );
  }
}

// ------------------------------------------------------------------------------------- bars

class BarsScene extends StatelessWidget {
  const BarsScene(this.c, {super.key});
  final SceneClock c;

  @override
  Widget build(BuildContext context) {
    final p = c.palette;
    final t = StoryType(p);
    final bars = _list(c.scene.props['bars']);
    final top = bars.fold<double>(0, (m, b) => math.max(m, _num(b['value'])));
    return SceneScaffold(
      clock: c,
      body: StoryCard(
        p: p,
        padding: const EdgeInsets.fromLTRB(14, 14, 14, 4),
        child: Column(children: [
          for (var i = 0; i < bars.length; i++)
            Reveal(
              c.item(i),
              dy: 8,
              child: Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Row(children: [
                    IconBadge(bars[i]['icon'], color: p.accent, size: 28),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text('${bars[i]['label'] ?? ''}', style: t.body.copyWith(fontSize: 13.5), maxLines: 1, overflow: TextOverflow.ellipsis),
                        if (bars[i]['sub'] != null) Text('${bars[i]['sub']}', style: t.small.copyWith(fontSize: 10.5)),
                      ]),
                    ),
                    Text('${bars[i]['display'] ?? bars[i]['value'] ?? ''}', style: t.number(18, p.text[0])),
                  ]),
                  const SizedBox(height: 8),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(99),
                    child: SizedBox(
                      height: 8,
                      child: Stack(fit: StackFit.expand, children: [
                        ColoredBox(color: p.surface[2]),
                        FractionallySizedBox(
                          alignment: Alignment.centerLeft,
                          widthFactor: top <= 0 ? 0 : (_num(bars[i]['value']) / top) * c.item(i, dur: 0.9),
                          heightFactor: 1,
                          child: ColoredBox(color: p.accent),
                        ),
                      ]),
                    ),
                  ),
                ]),
              ),
            ),
        ]),
      ),
    );
  }
}

// ----------------------------------------------------------------------------- history grid

class HistoryGridScene extends StatelessWidget {
  const HistoryGridScene(this.c, {super.key});
  final SceneClock c;

  @override
  Widget build(BuildContext context) {
    final p = c.palette;
    final t = StoryType(p);
    final props = c.scene.props;
    final months = [for (final m in (props['months'] as List? ?? const [])) '$m'];
    final rows = _list(props['rows']);
    final legend = _list(props['legend']);
    const labelW = 86.0, gap = 3.0;
    final cellsW = c.canvas.contentWidth - 26 - labelW; // card padding 2x12 + border 2x1
    final n = math.max(1, months.length);
    final cell = (cellsW - gap * (n - 1)) / n;
    final hl = c.since('highlight');
    final highlighting = c.scene.hasAction('highlight') && hl >= 0 && hl < 2.6;
    final anyBad = rows.any((r) => (r['cells'] as List? ?? const []).any((s) => s == 'late' || s == 'severe'));
    final pulse = highlighting ? 0.5 + 0.5 * math.sin(hl * 2 * math.pi * 1.2) : 0.0;

    Widget cellBox(Object? state) {
      final color = p.cell(state);
      final hot = highlighting && (anyBad ? state == 'late' || state == 'severe' : state == 'ok');
      return Container(
        width: cell,
        height: cell,
        decoration: BoxDecoration(
          color: color,
          borderRadius: BorderRadius.circular(3.5),
          boxShadow: hot ? [BoxShadow(color: color.withValues(alpha: 0.9 * pulse), blurRadius: 8, spreadRadius: 1.5 * pulse)] : null,
        ),
      );
    }

    return SceneScaffold(
      clock: c,
      body: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        StoryCard(
          p: p,
          padding: const EdgeInsets.all(12),
          child: Column(children: [
            Row(children: [
              const SizedBox(width: labelW),
              for (var i = 0; i < months.length; i++) ...[
                if (i > 0) const SizedBox(width: gap),
                SizedBox(
                  width: cell,
                  child: FittedBox(
                    fit: BoxFit.scaleDown,
                    child: Text(months[i].length > 3 ? months[i].substring(0, 3) : months[i], style: t.small.copyWith(fontSize: 8.5)),
                  ),
                ),
              ],
            ]),
            const SizedBox(height: 8),
            for (var r = 0; r < rows.length; r++)
              Reveal(
                c.item(r, action: 'row'),
                dy: 6,
                child: Padding(
                  padding: const EdgeInsets.only(bottom: 9),
                  child: Row(children: [
                    SizedBox(
                      width: labelW,
                      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text('${rows[r]['label'] ?? ''}', style: t.base(11.5, FontWeight.w700, p.text[0]), maxLines: 1, overflow: TextOverflow.ellipsis),
                        if (rows[r]['sub'] != null)
                          Text('${rows[r]['sub']}', style: t.small.copyWith(fontSize: 9.5), maxLines: 1, overflow: TextOverflow.ellipsis),
                      ]),
                    ),
                    for (var i = 0; i < months.length; i++) ...[
                      if (i > 0) const SizedBox(width: gap),
                      cellBox(i < (rows[r]['cells'] as List? ?? const []).length ? (rows[r]['cells'] as List)[i] : 'none'),
                    ],
                  ]),
                ),
              ),
          ]),
        ),
        if (legend.isNotEmpty) ...[
          const SizedBox(height: 12),
          Reveal(
            c.beat('legend'),
            child: Wrap(spacing: 14, runSpacing: 8, children: [
              for (final l in legend)
                Row(mainAxisSize: MainAxisSize.min, children: [
                  Container(width: 10, height: 10, decoration: BoxDecoration(color: p.cell(l['state']), borderRadius: BorderRadius.circular(3))),
                  const SizedBox(width: 6),
                  Text('${l['text'] ?? ''}', style: t.small),
                ]),
            ]),
          ),
        ],
      ]),
    );
  }
}

// ------------------------------------------------------------------------------------- list

class ListScene extends StatelessWidget {
  const ListScene(this.c, {super.key});
  final SceneClock c;

  @override
  Widget build(BuildContext context) {
    final p = c.palette;
    final t = StoryType(p);
    final rows = _list(c.scene.props['rows']);
    return SceneScaffold(
      clock: c,
      body: Column(children: [
        for (var i = 0; i < rows.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 9),
            child: Reveal(
              c.item(i),
              child: StoryCard(
                p: p,
                padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                child: Row(children: [
                  IconBadge(rows[i]['icon'], color: rows[i]['tone'] != null && rows[i]['tone'] != 'neutral' ? p.tone(rows[i]['tone']) : p.accent),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('${rows[i]['title'] ?? ''}', style: t.body, maxLines: 1, overflow: TextOverflow.ellipsis),
                      if (rows[i]['sub'] != null) ...[
                        const SizedBox(height: 2),
                        Text('${rows[i]['sub']}', style: t.bodyDim.copyWith(fontSize: 11.5), maxLines: 2, overflow: TextOverflow.ellipsis),
                      ],
                    ]),
                  ),
                  if (rows[i]['value'] != null) ...[
                    const SizedBox(width: 10),
                    Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
                      Text('${rows[i]['value']}',
                          style: t.number(17, rows[i]['tone'] != null && rows[i]['tone'] != 'neutral' ? p.tone(rows[i]['tone']) : p.text[0])),
                      if (rows[i]['value_sub'] != null) ...[
                        const SizedBox(height: 3),
                        Text('${rows[i]['value_sub']}', style: t.small.copyWith(fontSize: 10.5)),
                      ],
                    ]),
                  ],
                ]),
              ),
            ),
          ),
      ]),
    );
  }
}

// ------------------------------------------------------------------------------ action plan

class ActionPlanScene extends StatelessWidget {
  const ActionPlanScene(this.c, {super.key});
  final SceneClock c;

  @override
  Widget build(BuildContext context) {
    final p = c.palette;
    final t = StoryType(p);
    final props = c.scene.props;
    final range = (props['range'] as List?)?.whereType<num>().map((e) => e.toDouble()).toList() ?? const [300.0, 900.0];
    final lo = range.isNotEmpty ? range.first : 300.0, hi = range.length > 1 ? range[1] : 900.0;
    final current = _num(props['current'], lo), target = _num(props['target'], hi);
    final steps = _list(props['steps']);
    final reveal = c.scene.hasAction('reveal') ? c.beat('reveal', dur: 1.6) : c.beat('intro', dur: 1.6);
    double frac(double v) => ((v - lo) / (hi - lo)).clamp(0.0, 1.0);
    final curF = frac(current), tgtF = frac(target);

    return SceneScaffold(
      clock: c,
      body: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Reveal(
          c.intro,
          child: StoryCard(
            p: p,
            padding: const EdgeInsets.fromLTRB(14, 12, 14, 10),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('NOW', style: t.eyebrow),
                  const SizedBox(height: 6),
                  Text(current.round().toString(), style: t.number(26, p.text[0])),
                ]),
                const SizedBox(width: 14),
                Padding(padding: const EdgeInsets.only(bottom: 6), child: Icon(Icons.arrow_forward_rounded, color: p.text[2], size: 20)),
                const SizedBox(width: 14),
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('GOAL', style: t.eyebrow),
                  const SizedBox(height: 6),
                  Text('${props['target_label'] ?? target.round()}', style: t.number(26, p.accent)),
                ]),
                const Spacer(),
                if (props['gap_label'] != null) Pill('${props['gap_label']}', color: p.tone('good'), type: t),
              ]),
              const SizedBox(height: 12),
              LayoutBuilder(builder: (context, box) {
                final w = box.maxWidth;
                final head = curF + (tgtF - curF) * reveal;
                return SizedBox(
                  height: 26,
                  child: Stack(clipBehavior: Clip.none, children: [
                    Positioned(left: 0, right: 0, top: 9, height: 8, child: _pill(p.surface[2])),
                    Positioned(left: 0, width: w * curF, top: 9, height: 8, child: _pill(p.text[2])),
                    Positioned(left: w * curF, width: math.max(0, w * (head - curF)), top: 9, height: 8, child: _pill(p.accent)),
                    Positioned(left: w * tgtF - 1, top: 2, width: 2, height: 22, child: ColoredBox(color: p.accent)),
                    Positioned(
                      left: w * head - 8,
                      top: 5,
                      child: Container(
                        width: 16,
                        height: 16,
                        decoration: BoxDecoration(shape: BoxShape.circle, color: Colors.white, border: Border.all(color: p.accent, width: 4)),
                      ),
                    ),
                  ]),
                );
              }),
              const SizedBox(height: 4),
              Row(children: [
                Text(lo.round().toString(), style: t.small),
                const Spacer(),
                Text(hi.round().toString(), style: t.small),
              ]),
            ]),
          ),
        ),
        const SizedBox(height: 10),
        for (var i = 0; i < steps.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Reveal(
              c.item(i, action: 'step'),
              child: StoryCard(
                p: p,
                padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Container(
                    width: 28,
                    height: 28,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(shape: BoxShape.circle, color: p.accent),
                    child: Text('${i + 1}', style: t.base(13, FontWeight.w800, Colors.white, height: 1)),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('${steps[i]['title'] ?? ''}', style: t.body.copyWith(fontSize: 14)),
                      if (steps[i]['text'] != null) ...[
                        const SizedBox(height: 2),
                        Text('${steps[i]['text']}', style: t.bodyDim.copyWith(fontSize: 12), maxLines: 2, overflow: TextOverflow.ellipsis),
                      ],
                    ]),
                  ),
                  const SizedBox(width: 8),
                  Icon(storyIcon(steps[i]['icon']), size: 18, color: p.text[2]),
                ]),
              ),
            ),
          ),
        if (props['note'] != null)
          Reveal(
            c.item(steps.length - 1, action: 'step'),
            child: Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text('${props['note']}', style: t.bodyDim, textAlign: TextAlign.center),
            ),
          ),
      ]),
    );
  }

  Widget _pill(Color color) => DecoratedBox(decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(99)));
}
