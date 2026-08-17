import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

const ccbChatBackgroundMaxBytes = 20 * 1024 * 1024;
const ccbDefaultWorkspaceSurfaceOpacity = 0.62;
const ccbMinWorkspaceSurfaceOpacity = 0.28;
const ccbMaxWorkspaceSurfaceOpacity = 0.92;

enum CcbChatBackgroundFailure { tooLarge, unsupportedImage, unreadable }

class CcbChatBackgroundException implements Exception {
  const CcbChatBackgroundException(this.failure);

  final CcbChatBackgroundFailure failure;

  @override
  String toString() => 'CcbChatBackgroundException(${failure.name})';
}

@immutable
class CcbChatBackgroundPreference {
  const CcbChatBackgroundPreference({
    this.imagePath,
    this.surfaceOpacity = ccbDefaultWorkspaceSurfaceOpacity,
  });

  final String? imagePath;
  final double surfaceOpacity;

  CcbChatBackgroundPreference copyWith({
    String? imagePath,
    double? surfaceOpacity,
  }) {
    return CcbChatBackgroundPreference(
      imagePath: imagePath ?? this.imagePath,
      surfaceOpacity: surfaceOpacity ?? this.surfaceOpacity,
    );
  }
}

@immutable
class CcbChatBackgroundSelection {
  const CcbChatBackgroundSelection({
    required this.fileName,
    required this.bytes,
  });

  final String fileName;
  final Uint8List bytes;
}

typedef CcbChatBackgroundPicker =
    Future<CcbChatBackgroundSelection?> Function();

Future<CcbChatBackgroundSelection?> pickCcbChatBackgroundImage() async {
  final result = await FilePicker.pickFiles(
    allowMultiple: false,
    type: FileType.image,
    withData: true,
  );
  if (result == null || result.files.isEmpty) {
    return null;
  }
  final file = result.files.single;
  if (file.size > ccbChatBackgroundMaxBytes) {
    throw const CcbChatBackgroundException(CcbChatBackgroundFailure.tooLarge);
  }
  Uint8List? bytes = file.bytes;
  final path = file.path;
  if (bytes == null && path != null && path.isNotEmpty) {
    try {
      bytes = await File(path).readAsBytes();
    } on FileSystemException {
      throw const CcbChatBackgroundException(
        CcbChatBackgroundFailure.unreadable,
      );
    }
  }
  if (bytes == null) {
    throw const CcbChatBackgroundException(CcbChatBackgroundFailure.unreadable);
  }
  return CcbChatBackgroundSelection(fileName: file.name, bytes: bytes);
}

abstract class CcbChatBackgroundStore {
  Future<CcbChatBackgroundPreference?> read();

  Future<CcbChatBackgroundPreference> save(
    CcbChatBackgroundSelection selection, {
    double surfaceOpacity = ccbDefaultWorkspaceSurfaceOpacity,
  });

  Future<CcbChatBackgroundPreference?> updateSurfaceOpacity(double opacity);

  Future<void> clear();
}

class FlutterCcbChatBackgroundStore implements CcbChatBackgroundStore {
  FlutterCcbChatBackgroundStore({
    Future<Directory> Function()? directoryProvider,
  }) : _directoryProvider =
           directoryProvider ?? _defaultChatBackgroundDirectory;

  static const _filePrefix = 'chat-background-';
  static const _fileSuffix = '.img';
  static const _metadataFileName = 'chat-background.json';

  final Future<Directory> Function() _directoryProvider;

  @override
  Future<CcbChatBackgroundPreference?> read() async {
    final directory = await _directoryProvider();
    if (!await directory.exists()) {
      return null;
    }
    final files =
        await directory
            .list(followLinks: false)
            .where((entry) => entry is File && _isManagedFile(entry.path))
            .cast<File>()
            .toList();
    File? selected;
    if (files.isNotEmpty) {
      files.sort((left, right) => right.path.compareTo(left.path));
      selected = files.first;
    }
    final metadata = File(p.join(directory.path, _metadataFileName));
    if (selected == null && !await metadata.exists()) {
      return null;
    }
    return CcbChatBackgroundPreference(
      imagePath: selected?.path,
      surfaceOpacity: await _readSurfaceOpacity(directory),
    );
  }

  @override
  Future<CcbChatBackgroundPreference> save(
    CcbChatBackgroundSelection selection, {
    double surfaceOpacity = ccbDefaultWorkspaceSurfaceOpacity,
  }) async {
    final bytes = selection.bytes;
    if (bytes.length > ccbChatBackgroundMaxBytes) {
      throw const CcbChatBackgroundException(CcbChatBackgroundFailure.tooLarge);
    }
    if (!_hasSupportedImageSignature(bytes)) {
      throw const CcbChatBackgroundException(
        CcbChatBackgroundFailure.unsupportedImage,
      );
    }
    final directory = await _directoryProvider();
    await directory.create(recursive: true);
    final digest = sha256.convert(bytes).toString();
    final target = File(
      p.join(directory.path, '$_filePrefix$digest$_fileSuffix'),
    );
    if (!await target.exists()) {
      final temporary = File('${target.path}.tmp');
      await temporary.writeAsBytes(bytes, flush: true);
      await temporary.rename(target.path);
    }
    await for (final entry in directory.list(followLinks: false)) {
      if (entry is File &&
          entry.path != target.path &&
          (_isManagedFile(entry.path) || entry.path.endsWith('.tmp'))) {
        await entry.delete();
      }
    }
    final normalizedOpacity = _normalizeSurfaceOpacity(surfaceOpacity);
    await _writeSurfaceOpacity(directory, normalizedOpacity);
    return CcbChatBackgroundPreference(
      imagePath: target.path,
      surfaceOpacity: normalizedOpacity,
    );
  }

  @override
  Future<CcbChatBackgroundPreference?> updateSurfaceOpacity(
    double opacity,
  ) async {
    final directory = await _directoryProvider();
    await directory.create(recursive: true);
    final current = await read();
    final normalizedOpacity = _normalizeSurfaceOpacity(opacity);
    await _writeSurfaceOpacity(directory, normalizedOpacity);
    return (current ?? const CcbChatBackgroundPreference()).copyWith(
      surfaceOpacity: normalizedOpacity,
    );
  }

  @override
  Future<void> clear() async {
    final directory = await _directoryProvider();
    if (await directory.exists()) {
      await directory.delete(recursive: true);
    }
  }

  static Future<Directory> _defaultChatBackgroundDirectory() async {
    final documents = await getApplicationDocumentsDirectory();
    return Directory(p.join(documents.path, 'chat-background'));
  }

  static bool _isManagedFile(String path) {
    final name = p.basename(path);
    return name.startsWith(_filePrefix) && name.endsWith(_fileSuffix);
  }

  static Future<double> _readSurfaceOpacity(Directory directory) async {
    final metadata = File(p.join(directory.path, _metadataFileName));
    if (!await metadata.exists()) {
      return ccbDefaultWorkspaceSurfaceOpacity;
    }
    try {
      final decoded = jsonDecode(await metadata.readAsString());
      if (decoded is Map<String, dynamic>) {
        return _normalizeSurfaceOpacity(decoded['surface_opacity']);
      }
    } catch (_) {
      // A missing or corrupt preference must not hide the saved image.
    }
    return ccbDefaultWorkspaceSurfaceOpacity;
  }

  static Future<void> _writeSurfaceOpacity(
    Directory directory,
    double opacity,
  ) async {
    final metadata = File(p.join(directory.path, _metadataFileName));
    await metadata.writeAsString(
      jsonEncode(<String, Object>{'surface_opacity': opacity}),
      flush: true,
    );
  }

  static double _normalizeSurfaceOpacity(Object? value) {
    final parsed =
        value is num ? value.toDouble() : ccbDefaultWorkspaceSurfaceOpacity;
    return parsed
        .clamp(ccbMinWorkspaceSurfaceOpacity, ccbMaxWorkspaceSurfaceOpacity)
        .toDouble();
  }
}

class CcbChatBackgroundScope extends InheritedWidget {
  const CcbChatBackgroundScope({
    required this.preference,
    required this.onChoose,
    required this.onClear,
    required this.onSurfaceOpacityChanged,
    required super.child,
    super.key,
  });

  final CcbChatBackgroundPreference? preference;
  final Future<void> Function() onChoose;
  final Future<void> Function() onClear;
  final Future<void> Function(double opacity) onSurfaceOpacityChanged;

  static CcbChatBackgroundScope? maybeOf(BuildContext context) {
    return context.dependOnInheritedWidgetOfExactType<CcbChatBackgroundScope>();
  }

  @override
  bool updateShouldNotify(CcbChatBackgroundScope oldWidget) {
    return preference?.imagePath != oldWidget.preference?.imagePath ||
        preference?.surfaceOpacity != oldWidget.preference?.surfaceOpacity;
  }
}

bool ccbWorkspaceBackgroundEnabled(BuildContext context) {
  return CcbChatBackgroundScope.maybeOf(context)?.preference?.imagePath != null;
}

Color ccbWorkspaceSurfaceColor(BuildContext context, Color color) {
  final preference = CcbChatBackgroundScope.maybeOf(context)?.preference;
  final surfaceOpacity = preference?.surfaceOpacity;
  if (preference?.imagePath == null || surfaceOpacity == null) {
    return color;
  }
  return color.withValues(alpha: surfaceOpacity);
}

class CcbWorkspaceBackground extends StatelessWidget {
  const CcbWorkspaceBackground({
    required this.child,
    this.terminal = false,
    super.key,
  });

  final Widget child;
  final bool terminal;

  @override
  Widget build(BuildContext context) {
    final preference = CcbChatBackgroundScope.maybeOf(context)?.preference;
    final imagePath = preference?.imagePath;
    if (imagePath == null) {
      return child;
    }
    final colorScheme = Theme.of(context).colorScheme;
    final isDark = colorScheme.brightness == Brightness.dark;
    final scrim =
        terminal
            ? Colors.black.withValues(alpha: 0.34)
            : isDark
            ? Colors.black.withValues(alpha: 0.20)
            : Colors.black.withValues(alpha: 0.10);
    return Stack(
      key: const ValueKey('ccb-workspace-background'),
      fit: StackFit.expand,
      children: [
        Positioned.fill(
          child: IgnorePointer(
            child: Stack(
              fit: StackFit.expand,
              children: [
                ColoredBox(color: colorScheme.surface),
                Image.file(
                  File(imagePath),
                  key: const ValueKey('ccb-workspace-background-image'),
                  fit: BoxFit.cover,
                  filterQuality: FilterQuality.medium,
                  errorBuilder:
                      (context, error, stackTrace) => const SizedBox.shrink(),
                ),
                ColoredBox(
                  key: const ValueKey('ccb-workspace-background-scrim'),
                  color: scrim,
                ),
              ],
            ),
          ),
        ),
        Positioned.fill(child: child),
      ],
    );
  }
}

bool _hasSupportedImageSignature(Uint8List bytes) {
  if (bytes.length >= 8 &&
      bytes[0] == 0x89 &&
      bytes[1] == 0x50 &&
      bytes[2] == 0x4e &&
      bytes[3] == 0x47 &&
      bytes[4] == 0x0d &&
      bytes[5] == 0x0a &&
      bytes[6] == 0x1a &&
      bytes[7] == 0x0a) {
    return true;
  }
  if (bytes.length >= 3 &&
      bytes[0] == 0xff &&
      bytes[1] == 0xd8 &&
      bytes[2] == 0xff) {
    return true;
  }
  if (bytes.length >= 6) {
    final gif = String.fromCharCodes(bytes.take(6));
    if (gif == 'GIF87a' || gif == 'GIF89a') {
      return true;
    }
  }
  if (bytes.length >= 12 &&
      String.fromCharCodes(bytes.take(4)) == 'RIFF' &&
      String.fromCharCodes(bytes.skip(8).take(4)) == 'WEBP') {
    return true;
  }
  return bytes.length >= 2 && bytes[0] == 0x42 && bytes[1] == 0x4d;
}
