import 'dart:convert';

import 'package:credit_story_player/credit_story_player.dart';
import 'package:flutter/material.dart';

// Demo: paste a CRIF High Mark report (the bureau API response), the app calls the live story
// engine and plays the result in Hindi and English.
// Run with: cd example && flutter run

const baseUrl = 'https://finedtunnedtta.onrender.com';

void main() => runApp(const DemoApp());

class DemoApp extends StatelessWidget {
  const DemoApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Credit story demo',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(colorSchemeSeed: const Color(0xFF1677FF), brightness: Brightness.dark, useMaterial3: true),
        home: const CrifInput(),
      );
}

class CrifInput extends StatefulWidget {
  const CrifInput({super.key});

  @override
  State<CrifInput> createState() => _CrifInputState();
}

class _CrifInputState extends State<CrifInput> {
  final api = StoryApi(baseUrl: baseUrl);
  final text = TextEditingController();
  String? problem;

  @override
  void initState() {
    super.initState();
    api.wakeUp(); // the server sleeps when idle; start waking it now
  }

  @override
  void dispose() {
    text.dispose();
    super.dispose();
  }

  void _play() {
    final crif = text.text.trim();
    try {
      jsonDecode(crif);
    } catch (_) {
      setState(() => problem = 'That is not valid JSON.');
      return;
    }
    setState(() => problem = null);
    final controller = StoryController(api: api)..openCrif(crif);
    Navigator.of(context).push(MaterialPageRoute(
      builder: (context) => Scaffold(
        backgroundColor: Colors.black,
        body: CreditStoryPlayer(controller: controller, onClose: () => Navigator.of(context).pop()),
      ),
    )).then((_) => controller.dispose());
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Credit story demo')),
        body: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            const Text('Paste the CRIF High Mark response (JSON):'),
            const SizedBox(height: 8),
            Expanded(
              child: TextField(
                controller: text,
                maxLines: null,
                expands: true,
                textAlignVertical: TextAlignVertical.top,
                style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                decoration: InputDecoration(border: const OutlineInputBorder(), errorText: problem),
              ),
            ),
            const SizedBox(height: 12),
            FilledButton.icon(onPressed: _play, icon: const Icon(Icons.play_arrow), label: const Text('Generate & play')),
          ]),
        ),
      );
}
