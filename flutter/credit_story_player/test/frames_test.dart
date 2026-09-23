import 'dart:io';
import 'dart:typed_data';

import 'package:credit_story_player/credit_story_player.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart' show FontLoader;
import 'package:flutter_test/flutter_test.dart';

// Renders every scene of the sample stories on several screen sizes. Any overflow or layout
// error fails the test. Screenshots (written to test/goldens/, not committed):
//   flutter test --update-goldens --dart-define=SCREENSHOTS=true

const sizes = <String, Size>{
  'small_320x568': Size(320, 568),
  'iphone_390x844': Size(390, 844),
  'android_412x915': Size(412, 915),
  'tablet_834x1194': Size(834, 1194),
  'landscape_844x390': Size(844, 390),
};

Future<void> loadFonts() async {
  StoryType.useGoogleFonts = false;
  for (final (family, file) in [('Manrope', 'Manrope.ttf'), ('NotoSansDevanagari', 'NotoSansDevanagari.ttf')]) {
    final bytes = File('test/fonts/$file').readAsBytesSync();
    await (FontLoader(family)..addFont(Future.value(ByteData.sublistView(bytes)))).load();
  }
  // Material icons, from the Flutter SDK running the test
  final sdk = Platform.environment['FLUTTER_ROOT'] ?? '';
  final icons = File('$sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf');
  if (icons.existsSync()) {
    await (FontLoader('MaterialIcons')..addFont(Future.value(ByteData.sublistView(icons.readAsBytesSync())))).load();
  }
}

Timeline? load(String name) {
  final f = File('test/data/$name.json');
  return f.existsSync() ? parseTimeline(f.readAsStringSync()) : null; // crif sample stays local only
}

Future<void> pump(WidgetTester tester, Size size, Widget child) async {
  tester.view.physicalSize = size * 2;
  tester.view.devicePixelRatio = 2;
  await tester.pumpWidget(MaterialApp(debugShowCheckedModeBanner: false, home: Scaffold(backgroundColor: Colors.black, body: child)));
  await tester.pump();
}

const screenshots = bool.fromEnvironment('SCREENSHOTS');

void main() {
  setUpAll(loadFonts);

  for (final name in ['quick_en', 'quick_hi', 'crif_en']) {
    final tl = load(name);
    if (tl == null) continue;

    for (final entry in sizes.entries) {
      testWidgets('$name on ${entry.key}: every scene fits', (tester) async {
        addTearDown(tester.view.reset);
        // Each scene with everything revealed, plus mid-transition and mid-animation frames.
        final times = <double>[
          for (final s in tl.scenes) ...[s.start + 0.2, (s.start + s.end) / 2, s.end - 0.05],
        ];
        for (final t in times) {
          await pump(tester, entry.value, StoryFrame(timeline: tl, t: t));
          expect(tester.takeException(), isNull, reason: 't=$t');
        }
        await pump(tester, entry.value, StoryFrame(timeline: tl, t: 0, mode: FrameMode.intro));
        expect(tester.takeException(), isNull, reason: 'intro');
        await pump(tester, entry.value, StoryFrame(timeline: tl, t: tl.duration, mode: FrameMode.end, onClose: () {}));
        expect(tester.takeException(), isNull, reason: 'end card');
      });
    }

    testWidgets('$name screenshots', skip: !screenshots, (tester) async {
      addTearDown(tester.view.reset);
      const size = Size(390, 844);
      await pump(tester, size, StoryFrame(timeline: tl, t: 0, mode: FrameMode.intro));
      await expectLater(find.byType(StoryFrame), matchesGoldenFile('goldens/${name}_00_intro.png'));
      for (var i = 0; i < tl.scenes.length; i++) {
        final s = tl.scenes[i];
        // late in the scene, so all its beats have fired and a caption is on screen
        final t = s.end - 1.0 > s.start ? s.end - 1.0 : s.end - 0.05;
        await pump(tester, size, StoryFrame(timeline: tl, t: t));
        await expectLater(find.byType(StoryFrame),
            matchesGoldenFile('goldens/${name}_${(i + 1).toString().padLeft(2, '0')}_${s.type}.png'));
      }
      await pump(tester, size, StoryFrame(timeline: tl, t: tl.duration, mode: FrameMode.end, onClose: () {}));
      await expectLater(find.byType(StoryFrame), matchesGoldenFile('goldens/${name}_99_end.png'));
      for (final entry in sizes.entries) {
        final s = tl.scenes[tl.scenes.length > 2 ? 2 : 0];
        await pump(tester, entry.value, StoryFrame(timeline: tl, t: s.end - 0.05));
        await expectLater(find.byType(StoryFrame), matchesGoldenFile('goldens/${name}_size_${entry.key}.png'));
      }
    });
  }
}
