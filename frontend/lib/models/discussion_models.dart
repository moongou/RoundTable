/// 讨论会话数据模型

class Topic {
  final String id;
  final String title;
  final String description;
  final String category;
  final String ageRange;
  final List<String> guideQuestions;
  final List<String> tags;

  const Topic({
    required this.id,
    required this.title,
    required this.description,
    required this.category,
    this.ageRange = '8-12',
    this.guideQuestions = const [],
    this.tags = const [],
  });

  factory Topic.fromJson(Map<String, dynamic> json) => Topic(
        id: json['id'],
        title: json['title'],
        description: json['description'],
        category: json['category'],
        ageRange: json['age_range'] ?? '8-12',
        guideQuestions: List<String>.from(json['guide_questions'] ?? []),
        tags: List<String>.from(json['tags'] ?? []),
      );
}

enum ParticipantType { moderator, aiCharacter, human }

class Participant {
  final String name;
  final ParticipantType type;
  final String description;
  final String avatar;

  const Participant({
    required this.name,
    required this.type,
    this.description = '',
    this.avatar = '',
  });

  factory Participant.fromJson(Map<String, dynamic> json) => Participant(
        name: json['name'],
        type: ParticipantType.values.firstWhere(
          (e) => e.name == json['type'],
          orElse: () => ParticipantType.human,
        ),
        description: json['description'] ?? '',
        avatar: json['avatar'] ?? '',
      );
}

enum DiscussionStatus { waiting, active, paused, ended }

class SessionResponse {
  final String sessionId;
  final Topic topic;
  final List<Participant> participants;
  final DiscussionStatus status;

  const SessionResponse({
    required this.sessionId,
    required this.topic,
    required this.participants,
    this.status = DiscussionStatus.waiting,
  });

  factory SessionResponse.fromJson(Map<String, dynamic> json) => SessionResponse(
        sessionId: json['session_id'],
        topic: Topic.fromJson(json['topic']),
        participants: (json['participants'] as List)
            .map((p) => Participant.fromJson(p))
            .toList(),
        status: DiscussionStatus.values.firstWhere(
          (e) => e.name == json['status'],
          orElse: () => DiscussionStatus.waiting,
        ),
      );
}

class ChatMessage {
  final String source;
  final String content;
  final String type;

  const ChatMessage({
    required this.source,
    required this.content,
    this.type = 'text',
  });
}

class CharacterTemplate {
  final String id;
  final String name;
  final String displayName;
  final String avatar;
  final String personalityType;
  final String description;

  const CharacterTemplate({
    required this.id,
    required this.name,
    required this.displayName,
    required this.avatar,
    required this.personalityType,
    required this.description,
  });

  factory CharacterTemplate.fromJson(Map<String, dynamic> json) =>
      CharacterTemplate(
        id: json['id'],
        name: json['name'],
        displayName: json['display_name'],
        avatar: json['avatar'],
        personalityType: json['personality_type'],
        description: json['description'],
      );
}