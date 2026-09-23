import 'package:credit_story_player/credit_story_player.dart';
import 'package:flutter/material.dart';

// Demo: pick a dummy profile, the app calls the live story engine and plays the result.
// Run with: cd example && flutter run

const baseUrl = 'https://finedtunnedtta.onrender.com';

const scenarios = <String, Map<String, dynamic>>{
  'Karan · 776 · Hindi + English': {
    'customer_name': 'Karan',
    'customer_name_hi': 'करण',
    'credit_score': 776,
    'languages': ['hi', 'en'],
    'voice_speed': 0.95,
  },
  'Priya · 665 · missed payments': {
    'customer_name': 'Priya',
    'credit_score': 665,
    'missed_payments_count': 2,
    'active_credit_cards': 2,
    'credit_utilization_pct': 45,
    'languages': ['en'],
  },
  'Rahul · 795 · super prime': {
    'customer_name': 'Rahul',
    'credit_score': 795,
    'active_credit_cards': 3,
    'credit_utilization_pct': 15,
    'recent_inquiries': 1,
    'languages': ['en', 'hi'],
  },
  'Amit · 710 · no cards (Hindi)': {
    'customer_name': 'Amit',
    'customer_name_hi': 'अमित',
    'credit_score': 710,
    'languages': ['hi'],
  },
  'Vikram · 630 · maxed out': {
    'customer_name': 'Vikram',
    'credit_score': 630,
    'on_time_repayment_pct': 94,
    'missed_payments_count': 1,
    'active_credit_cards': 4,
    'credit_utilization_pct': 78,
    'recent_inquiries': 5,
    'languages': ['en'],
  },
};

void main() => runApp(const DemoApp());

class DemoApp extends StatelessWidget {
  const DemoApp({super.key});

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Credit story demo',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(colorSchemeSeed: const Color(0xFF1677FF), brightness: Brightness.dark, useMaterial3: true),
        home: const ScenarioList(),
      );
}

class ScenarioList extends StatefulWidget {
  const ScenarioList({super.key});

  @override
  State<ScenarioList> createState() => _ScenarioListState();
}

class _ScenarioListState extends State<ScenarioList> {
  final api = StoryApi(baseUrl: baseUrl);

  @override
  void initState() {
    super.initState();
    api.wakeUp(); // the server sleeps when idle; start waking it now
  }

  void _open(Map<String, dynamic> body) {
    final controller = StoryController(api: api)..openRequest(body);
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
        body: ListView(children: [
          for (final e in scenarios.entries)
            ListTile(
              leading: const Icon(Icons.play_circle_outline),
              title: Text(e.key),
              subtitle: Text('score ${e.value['credit_score']}'),
              onTap: () => _open(e.value),
            ),
        ]),
      );
}
