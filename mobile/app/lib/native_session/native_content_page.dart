/// One immutable, UTF-8-safe page of an original native conversation item.
class NativeContentPage {
  const NativeContentPage({
    required this.text,
    required this.offset,
    required this.totalBytes,
    required this.version,
    this.nextCursor,
  });

  final String text;
  final String? nextCursor;
  final int offset;
  final int totalBytes;
  final String version;

  factory NativeContentPage.fromJson(Map<String, Object?> json) =>
      NativeContentPage(
        text: json['text'] as String,
        nextCursor: json['next_cursor'] as String?,
        offset: json['offset'] as int,
        totalBytes: json['total_bytes'] as int,
        version: json['version'] as String,
      );

  Map<String, Object?> toJson() => {
    'text': text,
    'next_cursor': nextCursor,
    'offset': offset,
    'total_bytes': totalBytes,
    'version': version,
  };
}
