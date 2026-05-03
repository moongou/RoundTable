import 'package:flutter_test/flutter_test.dart';
import 'package:roundtable/features/home/immersive_home_screen.dart';
import 'package:roundtable/models/config_models.dart';
import 'package:roundtable/models/discussion_models.dart';
import 'package:roundtable/services/saved_topics_store.dart';

void main() {
  test('home ASR keeps direct capswriter URL', () {
    expect(
      resolveHomeAsrProviderUrl(
        providerId: 'capswriter',
        url: 'http://localhost:6016',
        defaultUrl: '',
      ),
      'http://localhost:6016',
    );
  });

  test('home ASR does not rewrite capswriter host/port', () {
    expect(
      resolveHomeAsrProviderUrl(
        providerId: 'capswriter',
        url: 'http://192.168.0.8:6016',
        defaultUrl: '',
      ),
      'http://192.168.0.8:6016',
    );
  });

  test('home ASR keeps non gateway providers unchanged', () {
    expect(
      resolveHomeAsrProviderUrl(
        providerId: 'funasr',
        url: 'ws://localhost:10095',
        defaultUrl: 'ws://localhost:10095',
      ),
      'ws://localhost:10095',
    );
  });

  test('home free-topic no longer forces server proxy for capswriter', () {
    expect(
      shouldPreferServerProxyForHomeAsr(
        providerId: 'capswriter',
        streamingEnabled: true,
      ),
      isFalse,
    );
    expect(
      shouldPreferServerProxyForHomeAsr(
        providerId: 'browser',
        streamingEnabled: false,
      ),
      isFalse,
    );
    expect(
      shouldPreferServerProxyForHomeAsr(
        providerId: 'funasr',
        streamingEnabled: false,
      ),
      isTrue,
    );
  });

  test('home free-topic prefers capswriter over browser when available', () {
    expect(
      resolvePreferredHomeAsrProviderId(
        preferredProviderId: 'browser',
        providers: const [
          SpeechProviderInfo(
            id: 'browser',
            name: 'Browser',
            isActive: true,
            available: true,
          ),
          SpeechProviderInfo(
            id: 'capswriter',
            name: 'CapsWriter',
            isActive: false,
            available: true,
          ),
        ],
      ),
      'capswriter',
    );
  });

  test('home categories insert spark right after tech', () {
    final categories = mergeHomeTopicCategoriesWithSpark([
      {'id': 'science', 'name': '科学', 'count': 3},
      {'id': 'tech', 'name': '科技', 'count': 2},
      {'id': 'society', 'name': '社会', 'count': 5},
    ]);

    expect(categories.map((item) => item['id']),
        ['science', 'tech', 'spark', 'society']);
  });

  test('saved spark topics become second-column topics', () {
    final topics = buildHomeSparkTopicsFromSavedItems([
      SavedTopicItem(
        title: '小学生要不要拥有自己的手机？',
        originalContent: '我想讨论的是现在很多小学生已经开始使用手机，这到底是方便还是分心。',
        savedAt: DateTime.parse('2025-03-01T10:00:00Z'),
      ),
    ]);

    expect(topics, hasLength(1));
    expect(topics.single.category, 'spark');
    expect(topics.single.title, '小学生要不要拥有自己的手机？');
    expect(topics.single.description, contains('很多小学生已经开始使用手机'));
    expect(topics.single.id, 'spark_saved_1740823200000');
  });

  test('spark topics are normalized to free-topic sessions on start', () {
    const sparkTopic = Topic(
      id: 'spark_saved_1740823200000',
      title: '小学生要不要拥有自己的手机？',
      description: '我想讨论的是现在很多小学生已经开始使用手机，这到底是方便还是分心。',
      category: 'spark',
      tags: ['火花', '自由话题'],
    );

    final effective = normalizeHomeTopicForDiscussion(sparkTopic);

    expect(effective.id, 'free_topic');
    expect(effective.category, 'spark');
    expect(effective.title, sparkTopic.title);
    expect(effective.description, sparkTopic.description);
  });

  test('preset topics keep original ids for discussion', () {
    const topic = Topic(
      id: 'topic_1',
      title: '为什么要上学？',
      description: '讨论学校教育的意义。',
      category: 'education',
    );

    final effective = normalizeHomeTopicForDiscussion(topic);

    expect(identical(effective, topic), isTrue);
  });

  test('free topic draft keeps source detail after condense', () {
    const draft = FreeTopicDraftValue(
      title: '小学生该不该限制刷短视频？',
      sourceText: '我想讨论的是现在很多小学生放学以后会刷很久短视频，影响作业和睡觉。',
      isCondensed: true,
    );

    expect(draft.hasText, isTrue);
    expect(draft.effectiveTitle, '小学生该不该限制刷短视频？');
    expect(draft.effectiveSourceText, contains('很多小学生'));
  });

  test('home account labels keep phone and nickname fallback text', () {
    expect(formatHomeAccountPhoneLabel('13912345678'), '登录 13912345678');
    expect(formatHomeAccountPhoneLabel(''), '登录 未登录');

    expect(formatHomeAccountNicknameLabel('小袁'), '(小袁)');
    expect(formatHomeAccountNicknameLabel(''), '(同学)');
  });

  test('home seat title uses nickname in non observer mode', () {
    expect(
      formatHomeSeatBadgeTitle(humanName: '小袁', observerMode: false),
      '小袁的席位',
    );
    expect(
      formatHomeSeatBadgeTitle(humanName: '', observerMode: false),
      '同学的席位',
    );
    expect(
      formatHomeSeatBadgeTitle(humanName: '任意', observerMode: true),
      '旁听席',
    );
  });
}
