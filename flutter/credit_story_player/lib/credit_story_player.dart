/// Plays a credit story (one MP3 + one animation timeline JSON from the story engine) like a
/// video, on a fixed canvas that is scaled to fit any screen.
///
/// ```dart
/// final controller = StoryController();
/// controller.openCrif(crifJson); // the CRIF High Mark response, as a JSON string
/// Navigator.push(context, MaterialPageRoute(builder: (_) => Scaffold(
///   backgroundColor: Colors.black,
///   body: CreditStoryPlayer(controller: controller, onClose: () => Navigator.pop(context)),
/// )));
/// ```
library;

export 'src/api.dart' show StoryApi, StoryApiException, StoryMedia, StoryResponse, parseTimeline;
export 'src/controller.dart' show StoryController, StoryPhase;
export 'src/models.dart';
export 'src/player.dart' show CreditStoryPlayer, SeekBar;
export 'src/stage.dart' show CanvasBox, FrameMode, StoryFrame, StoryStage;
export 'src/kit.dart' show StoryType;
