import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

const ccbMobileDefaultVersion = '8.6.10+8060010';
const ccbMobileDefaultApkDownloadUrl =
    'https://github.com/SeemSeam/claude_codex_bridge/releases/latest';
const ccbMobileReleaseApiUrl =
    'https://api.github.com/repos/SeemSeam/claude_codex_bridge/releases/latest';
const ccbMobileLatestManifestUrl =
    'https://github.com/SeemSeam/claude_codex_bridge/releases/latest/download/ccb-mobile-latest.json';

const ccbMobileCurrentVersion = String.fromEnvironment(
  'CCB_MOBILE_VERSION',
  defaultValue: ccbMobileDefaultVersion,
);

const ccbMobileApkDownloadUrl = String.fromEnvironment(
  'CCB_MOBILE_APK_URL',
  defaultValue: ccbMobileDefaultApkDownloadUrl,
);

const ccbMobileGithubProxyPrefixes = <String>[
  'https://gh-proxy.com/',
  'https://ghfast.top/',
  'https://ghproxy.net/',
];

class CcbMobileUpdateInfo {
  const CcbMobileUpdateInfo({
    this.version = ccbMobileCurrentVersion,
    this.apkDownloadUrl = ccbMobileApkDownloadUrl,
  });

  final String version;
  final String apkDownloadUrl;
}

class CcbMobileRelease {
  const CcbMobileRelease({
    required this.version,
    required this.versionCode,
    required this.apkDownloadUrl,
    required this.sha256,
    required this.sizeBytes,
    required this.releasePageUrl,
  });

  final String version;
  final int versionCode;
  final String apkDownloadUrl;
  final String sha256;
  final int sizeBytes;
  final String releasePageUrl;
}

class CcbMobileUpdateCheckResult {
  const CcbMobileUpdateCheckResult({
    required this.currentVersion,
    this.release,
  });

  final String currentVersion;
  final CcbMobileRelease? release;

  bool get updateAvailable => release != null;
}

class CcbMobileUpdateException implements Exception {
  const CcbMobileUpdateException(this.message);

  final String message;

  @override
  String toString() => message;
}

typedef CcbMobileUpdateBytesFetcher =
    Future<List<int>> Function(Uri uri, int maxBytes);
typedef CcbMobileUpdateFileDownloader =
    Future<void> Function(Uri uri, File target, int maxBytes);

class CcbMobileUpdateService {
  CcbMobileUpdateService({
    this.currentVersion = ccbMobileCurrentVersion,
    List<String>? proxyPrefixes,
    CcbMobileUpdateBytesFetcher? fetchBytes,
    CcbMobileUpdateFileDownloader? downloadFile,
    Future<Directory> Function()? downloadDirectory,
  }) : proxyPrefixes = proxyPrefixes ?? ccbMobileGithubProxyPrefixes,
       _fetchBytes = fetchBytes ?? _httpGetBytes,
       _downloadFile = downloadFile ?? _httpDownloadFile,
       _downloadDirectory = downloadDirectory ?? getTemporaryDirectory;

  final String currentVersion;
  final List<String> proxyPrefixes;
  final CcbMobileUpdateBytesFetcher _fetchBytes;
  final CcbMobileUpdateFileDownloader _downloadFile;
  final Future<Directory> Function() _downloadDirectory;

  Future<CcbMobileUpdateCheckResult> checkForUpdate() async {
    Object? lastError;
    try {
      final uri = Uri.parse(ccbMobileReleaseApiUrl);
      final releasePayload = _jsonObject(
        await _fetchUpdateSource(uri, 2 * 1024 * 1024),
        source: uri,
      );
      _validateGithubReleasePayload(releasePayload);
      final release = await _releaseFromGithubPayload(releasePayload);
      return _checkResult(release);
    } on _CcbMobileUpdateSourceUnavailable catch (error) {
      lastError = error;
    } catch (error) {
      throw CcbMobileUpdateException(
        'Rejected GitHub release metadata: $error',
      );
    }
    try {
      final uri = _trustedManifestUri(ccbMobileLatestManifestUrl);
      final manifest = _jsonObject(
        await _fetchUpdateSource(uri, 256 * 1024),
        source: uri,
      );
      final version = _requiredVersion(manifest['version'], 'release version');
      final release = _parseManifest(
        manifest,
        expectedVersion: version,
        releasePageUrl: ccbMobileDefaultApkDownloadUrl,
      );
      return _checkResult(release);
    } on _CcbMobileUpdateSourceUnavailable catch (error) {
      lastError = error;
    } catch (error) {
      throw CcbMobileUpdateException(
        'Rejected CCB Mobile release metadata: $error',
      );
    }
    throw CcbMobileUpdateException(
      'Unable to check the CCB Mobile release: $lastError',
    );
  }

  Future<List<int>> _fetchUpdateSource(Uri uri, int maxBytes) async {
    try {
      return await _fetchBytes(uri, maxBytes);
    } on IOException catch (error) {
      throw _CcbMobileUpdateSourceUnavailable(uri, error);
    } on TimeoutException catch (error) {
      throw _CcbMobileUpdateSourceUnavailable(uri, error);
    } on CcbMobileUpdateException catch (error) {
      throw _CcbMobileUpdateSourceUnavailable(uri, error);
    }
  }

  Future<File> downloadApk(CcbMobileRelease release) async {
    late final String version;
    try {
      version = _requiredVersion(release.version, 'release version');
      final sha = release.sha256.toLowerCase();
      if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(sha)) {
        throw const CcbMobileUpdateException('Invalid APK checksum');
      }
      final sizeBytes = release.sizeBytes;
      if (sizeBytes <= 0 || sizeBytes > _maximumAllowedApkBytes) {
        throw const CcbMobileUpdateException(
          'APK size is outside the allowed range',
        );
      }
      final apkUri = _requiredCcbReleaseUri(release.apkDownloadUrl, 'APK URL');
      final apkSegments = apkUri.pathSegments;
      if (apkSegments[3] != 'download' ||
          apkSegments[4] != 'v$version' ||
          apkSegments[5] != 'ccb-mobile-v$version.apk') {
        throw const CcbMobileUpdateException(
          'APK URL does not match release version',
        );
      }
    } on CcbMobileUpdateException {
      rethrow;
    } catch (error) {
      throw CcbMobileUpdateException('Invalid APK release metadata: $error');
    }
    Object? lastError;
    final directory = await _downloadDirectory();
    await directory.create(recursive: true);
    final file = File(p.join(directory.path, 'ccb-mobile-v$version.apk'));
    late final List<Uri> sourceUris;
    try {
      sourceUris = _apkSourceUris(
        release.apkDownloadUrl,
      ).toList(growable: false);
    } catch (error) {
      throw CcbMobileUpdateException(
        'Invalid APK update source configuration: $error',
      );
    }
    for (final uri in sourceUris) {
      try {
        await _downloadFile(uri, file, _maximumApkBytes(release));
        if (release.sizeBytes > 0 && await file.length() != release.sizeBytes) {
          throw const CcbMobileUpdateException('Downloaded APK size mismatch');
        }
        final actualDigest =
            (await sha256.bind(file.openRead()).first).toString();
        if (actualDigest.toLowerCase() != release.sha256.toLowerCase()) {
          throw const CcbMobileUpdateException(
            'Downloaded APK checksum mismatch',
          );
        }
        return file;
      } catch (error) {
        lastError = error;
        if (await file.exists()) {
          await file.delete();
        }
      }
    }
    throw CcbMobileUpdateException(
      'Unable to download the CCB Mobile APK: ${lastError ?? 'no download source available'}',
    );
  }

  Future<CcbMobileRelease> _releaseFromGithubPayload(
    Map<String, Object?> payload,
  ) async {
    final tag = _requiredText(payload['tag_name'], 'release tag');
    final version = _requiredVersion(
      tag.startsWith('v') ? tag.substring(1) : tag,
      'release version',
    );
    final assets = payload['assets'];
    if (assets is! List) {
      throw const FormatException('release assets are missing');
    }
    final manifestName = 'ccb-mobile-$tag.json';
    final manifestUrl = _assetUrl(assets, manifestName);
    final apkEvidence = _trustedApkEvidence(
      assets,
      expectedName: 'ccb-mobile-$tag.apk',
    );
    final uri = _trustedManifestUri(manifestUrl);
    final manifest = _jsonObject(
      await _fetchUpdateSource(uri, 256 * 1024),
      source: uri,
    );
    return _parseManifest(
      manifest,
      expectedVersion: version,
      releasePageUrl:
          _optionalCcbReleasePageUrl(payload['html_url'], version) ??
          ccbMobileDefaultApkDownloadUrl,
      trustedApkEvidence: apkEvidence,
    );
  }

  CcbMobileRelease _parseManifest(
    Map<String, Object?> manifest, {
    required String expectedVersion,
    required String releasePageUrl,
    _TrustedApkEvidence? trustedApkEvidence,
  }) {
    if (manifest['schema_version'] != 1 ||
        manifest['version']?.toString() != expectedVersion) {
      throw const FormatException('mobile release manifest version mismatch');
    }
    final android = manifest['android'];
    if (android is! Map ||
        android['application_id'] != 'io.ccb.mobile.ccb_mobile') {
      throw const FormatException(
        'mobile release manifest is not for this app',
      );
    }
    final sha = _requiredText(android['sha256'], 'APK checksum').toLowerCase();
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(sha)) {
      throw const FormatException('invalid APK checksum');
    }
    final versionName = _requiredText(
      android['version_name'],
      'Android version',
    );
    if (versionName != expectedVersion) {
      throw const FormatException('Android version does not match release tag');
    }
    final apkUrl = _requiredCcbReleaseUrl(android['download_url'], 'APK URL');
    final apkUri = Uri.parse(apkUrl);
    final apkSegments = apkUri.pathSegments;
    if (apkSegments[3] != 'download' ||
        apkSegments[4] != 'v$expectedVersion' ||
        apkSegments[5] != 'ccb-mobile-v$expectedVersion.apk') {
      throw const FormatException('APK URL does not match release tag');
    }
    final versionCode = _requiredPositiveInt(
      android['version_code'],
      'Android version code',
    );
    final sizeBytes = _requiredPositiveInt(android['size_bytes'], 'APK size');
    if (sizeBytes > _maximumAllowedApkBytes) {
      throw const FormatException('APK size is outside the allowed range');
    }
    if (trustedApkEvidence != null &&
        (apkUrl != trustedApkEvidence.downloadUrl ||
            sha != trustedApkEvidence.sha256 ||
            sizeBytes != trustedApkEvidence.sizeBytes)) {
      throw const FormatException(
        'release manifest does not match the GitHub APK asset',
      );
    }
    return CcbMobileRelease(
      version: versionName,
      versionCode: versionCode,
      apkDownloadUrl: apkUrl,
      sha256: sha,
      sizeBytes: sizeBytes,
      releasePageUrl: releasePageUrl,
    );
  }

  bool _isNewer(CcbMobileRelease release) {
    final currentCode = _buildCode(currentVersion);
    if (currentCode != null) {
      return release.versionCode > currentCode;
    }
    return compareCcbMobileVersions(release.version, currentVersion) > 0;
  }

  CcbMobileUpdateCheckResult _checkResult(CcbMobileRelease release) =>
      CcbMobileUpdateCheckResult(
        currentVersion: currentVersion,
        release: _isNewer(release) ? release : null,
      );

  Iterable<Uri> _apkSourceUris(String original) sync* {
    final uri = _requiredCcbReleaseUri(original, 'APK URL');
    yield uri;
    for (final prefix in proxyPrefixes) {
      final normalized = prefix.endsWith('/') ? prefix : '$prefix/';
      final prefixUri = Uri.parse(normalized);
      if (prefixUri.scheme != 'https' ||
          prefixUri.userInfo.isNotEmpty ||
          prefixUri.host.isEmpty ||
          prefixUri.hasPort ||
          prefixUri.hasQuery ||
          prefixUri.hasFragment) {
        throw FormatException(
          'update proxy must be a plain HTTPS prefix: $prefix',
        );
      }
      final proxyUri = Uri.parse('$normalized$original');
      if (proxyUri.scheme != 'https' ||
          proxyUri.userInfo.isNotEmpty ||
          proxyUri.host.isEmpty ||
          proxyUri.hasPort ||
          proxyUri.hasQuery ||
          proxyUri.hasFragment) {
        throw FormatException('update proxy must use HTTPS: $prefix');
      }
      yield proxyUri;
    }
  }
}

class _CcbMobileUpdateSourceUnavailable implements Exception {
  const _CcbMobileUpdateSourceUnavailable(this.source, this.cause);

  final Uri source;
  final Object cause;

  @override
  String toString() => '$source: $cause';
}

void _validateGithubReleasePayload(Map<String, Object?> payload) {
  final tag = _requiredText(payload['tag_name'], 'release tag');
  final assets = payload['assets'];
  if (assets is! List) {
    throw const FormatException('release assets are missing');
  }
  _assetUrl(assets, 'ccb-mobile-$tag.json');
}

Future<void> installCcbMobileApk(File apk) async {
  final result = await OpenFilex.open(
    apk.path,
    type: 'application/vnd.android.package-archive',
  );
  if (result.type != ResultType.done) {
    throw CcbMobileUpdateException(result.message);
  }
}

int compareCcbMobileVersions(String left, String right) {
  final leftParts = _versionParts(left);
  final rightParts = _versionParts(right);
  final length =
      leftParts.length > rightParts.length
          ? leftParts.length
          : rightParts.length;
  for (var index = 0; index < length; index += 1) {
    final leftValue = index < leftParts.length ? leftParts[index] : 0;
    final rightValue = index < rightParts.length ? rightParts[index] : 0;
    if (leftValue != rightValue) {
      return leftValue.compareTo(rightValue);
    }
  }
  return 0;
}

List<int> _versionParts(String value) => value
    .split('+')
    .first
    .split('.')
    .map((part) => int.tryParse(part) ?? 0)
    .toList(growable: false);

int? _buildCode(String value) {
  final parts = value.split('+');
  return parts.length == 2 ? int.tryParse(parts.last) : null;
}

int _maximumApkBytes(CcbMobileRelease release) {
  if (release.sizeBytes <= 0 || release.sizeBytes > _maximumAllowedApkBytes) {
    throw const FormatException('APK size is outside the allowed range');
  }
  return release.sizeBytes + 1;
}

const _maximumAllowedApkBytes = 256 * 1024 * 1024;

Map<String, Object?> _jsonObject(List<int> bytes, {required Uri source}) {
  final decoded = jsonDecode(utf8.decode(bytes));
  if (decoded is! Map) {
    throw FormatException('expected a JSON object from $source');
  }
  return {
    for (final entry in decoded.entries) entry.key.toString(): entry.value,
  };
}

String _assetUrl(List<Object?> assets, String expectedName) {
  for (final asset in assets) {
    if (asset is Map && asset['name'] == expectedName) {
      return _assetDownloadUrl(asset, expectedName);
    }
  }
  throw FormatException('release asset is missing: $expectedName');
}

class _TrustedApkEvidence {
  const _TrustedApkEvidence({
    required this.downloadUrl,
    required this.sha256,
    required this.sizeBytes,
  });

  final String downloadUrl;
  final String sha256;
  final int sizeBytes;
}

_TrustedApkEvidence _trustedApkEvidence(
  List<Object?> assets, {
  required String expectedName,
}) {
  for (final asset in assets) {
    if (asset is! Map || asset['name'] != expectedName) {
      continue;
    }
    final digest = _requiredText(asset['digest'], 'GitHub APK digest');
    if (!digest.startsWith('sha256:')) {
      throw const FormatException('GitHub APK digest is invalid');
    }
    final sha256 = digest.substring('sha256:'.length).toLowerCase();
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(sha256)) {
      throw const FormatException('GitHub APK digest is invalid');
    }
    final sizeBytes = _requiredPositiveInt(asset['size'], 'GitHub APK size');
    if (sizeBytes > _maximumAllowedApkBytes) {
      throw const FormatException(
        'GitHub APK size is outside the allowed range',
      );
    }
    return _TrustedApkEvidence(
      downloadUrl: _assetDownloadUrl(asset, expectedName),
      sha256: sha256,
      sizeBytes: sizeBytes,
    );
  }
  throw FormatException('release asset is missing: $expectedName');
}

String _assetDownloadUrl(Map<Object?, Object?> asset, String expectedName) {
  final url = _requiredCcbReleaseUrl(
    asset['browser_download_url'],
    expectedName,
  );
  if (Uri.parse(url).pathSegments.last != expectedName) {
    throw FormatException('release asset URL does not match: $expectedName');
  }
  return url;
}

String _requiredText(Object? value, String name) {
  final text = value?.toString().trim() ?? '';
  if (text.isEmpty) {
    throw FormatException('$name is missing');
  }
  return text;
}

String _requiredVersion(Object? value, String name) {
  final version = _requiredText(value, name);
  if (!RegExp(r'^\d+\.\d+\.\d+$').hasMatch(version)) {
    throw FormatException('$name is invalid');
  }
  return version;
}

int _requiredPositiveInt(Object? value, String name) {
  final parsed = value is int ? value : int.tryParse(value?.toString() ?? '');
  if (parsed == null || parsed <= 0) {
    throw FormatException('$name is invalid');
  }
  return parsed;
}

String _requiredGithubUrl(Object? value, String name) {
  final text = _requiredText(value, name);
  final uri = Uri.parse(text);
  if (!_isAllowedGithubUri(uri)) {
    throw FormatException('$name is not an allowed GitHub URL');
  }
  return text;
}

String _requiredCcbReleaseUrl(Object? value, String name) {
  final text = _requiredGithubUrl(value, name);
  _requiredCcbReleaseUri(text, name);
  return text;
}

Uri _requiredCcbReleaseUri(String value, String name) {
  final uri = Uri.parse(value);
  if (uri.scheme != 'https' ||
      uri.userInfo.isNotEmpty ||
      uri.host != 'github.com' ||
      uri.hasPort ||
      !_isCcbReleaseAssetPath(uri.pathSegments) ||
      uri.hasQuery ||
      uri.hasFragment) {
    throw FormatException('$name is not a CCB Mobile release URL');
  }
  return uri;
}

bool _isCcbReleaseAssetPath(List<String> segments) {
  if (segments.length != 6 ||
      segments[0] != 'SeemSeam' ||
      segments[1] != 'claude_codex_bridge' ||
      segments[2] != 'releases' ||
      segments[5].isEmpty) {
    return false;
  }
  if (segments[3] == 'latest') {
    return segments[4] == 'download';
  }
  return segments[3] == 'download' &&
      RegExp(r'^v\d+\.\d+\.\d+$').hasMatch(segments[4]);
}

Uri _trustedManifestUri(String value) {
  final uri = _requiredCcbReleaseUri(value, 'release manifest URL');
  if (!uri.path.endsWith('.json')) {
    throw const FormatException('release manifest URL must name a JSON asset');
  }
  return uri;
}

String? _optionalCcbReleasePageUrl(Object? value, String version) {
  final text = value?.toString().trim() ?? '';
  if (text.isEmpty) {
    return null;
  }
  final uri = Uri.tryParse(text);
  if (uri == null ||
      uri.scheme != 'https' ||
      uri.userInfo.isNotEmpty ||
      uri.host != 'github.com' ||
      uri.hasPort ||
      uri.hasQuery ||
      uri.hasFragment ||
      uri.path != '/SeemSeam/claude_codex_bridge/releases/tag/v$version') {
    return null;
  }
  return text;
}

bool _isAllowedGithubUri(Uri uri) =>
    uri.scheme == 'https' &&
    uri.userInfo.isEmpty &&
    !uri.hasPort &&
    (uri.host == 'github.com' || uri.host == 'api.github.com');

Future<List<int>> _httpGetBytes(Uri uri, int maxBytes) async {
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 8);
  try {
    final request = await client
        .getUrl(uri)
        .timeout(const Duration(seconds: 10));
    request.headers.set(HttpHeaders.acceptHeader, 'application/json, */*');
    request.headers.set(
      HttpHeaders.userAgentHeader,
      'CCB-Mobile/$ccbMobileCurrentVersion',
    );
    final response = await request.close().timeout(const Duration(seconds: 15));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      await response.drain<void>();
      throw HttpException('HTTP ${response.statusCode}', uri: uri);
    }
    if (response.contentLength > maxBytes) {
      await response.drain<void>();
      throw const CcbMobileUpdateException('Update response is too large');
    }
    final bytes = <int>[];
    await for (final chunk in response.timeout(const Duration(seconds: 30))) {
      bytes.addAll(chunk);
      if (bytes.length > maxBytes) {
        throw const CcbMobileUpdateException('Update response is too large');
      }
    }
    return bytes;
  } finally {
    client.close(force: true);
  }
}

Future<void> _httpDownloadFile(Uri uri, File target, int maxBytes) async {
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 8);
  IOSink? sink;
  try {
    final request = await client
        .getUrl(uri)
        .timeout(const Duration(seconds: 10));
    request.headers.set(HttpHeaders.acceptHeader, 'application/octet-stream');
    request.headers.set(
      HttpHeaders.userAgentHeader,
      'CCB-Mobile/$ccbMobileCurrentVersion',
    );
    final response = await request.close().timeout(const Duration(seconds: 15));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      await response.drain<void>();
      throw HttpException('HTTP ${response.statusCode}', uri: uri);
    }
    if (response.contentLength > maxBytes) {
      await response.drain<void>();
      throw const CcbMobileUpdateException('APK response is too large');
    }
    sink = target.openWrite();
    var received = 0;
    await for (final chunk in response.timeout(const Duration(seconds: 30))) {
      received += chunk.length;
      if (received > maxBytes) {
        throw const CcbMobileUpdateException('APK response is too large');
      }
      sink.add(chunk);
    }
    await sink.flush();
  } finally {
    await sink?.close();
    client.close(force: true);
  }
}
