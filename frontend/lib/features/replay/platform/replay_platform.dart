import 'replay_platform_stub.dart'
    if (dart.library.html) 'replay_platform_web.dart' as delegate;
import 'replay_platform_types.dart';

Future<PickedReplayZip?> pickReplayZipFile() => delegate.pickReplayZipFile();

ReplayAudioController createReplayAudioController() =>
    delegate.createReplayAudioController();
