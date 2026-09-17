import 'dart:ui' as ui;

import 'package:flutter/material.dart';

import 'chat_background.dart';

/// Original, offline vector artwork, rasterized only when selected. Preview
/// and saved wallpaper use the same composition, without bundled large files.
enum CcbBackgroundPreset { mist, dunes, night }

class CcbBackgroundPresetPainter extends CustomPainter {
  const CcbBackgroundPresetPainter(this.preset);

  final CcbBackgroundPreset preset;

  @override
  void paint(Canvas canvas, Size size) {
    final colors = switch (preset) {
      CcbBackgroundPreset.mist => [
        const Color(0xffdcebf0),
        const Color(0xff819eaf),
      ],
      CcbBackgroundPreset.dunes => [
        const Color(0xfff1e6d5),
        const Color(0xffb99d80),
      ],
      CcbBackgroundPreset.night => [
        const Color(0xff17263c),
        const Color(0xff405777),
      ],
    };
    final rect = Offset.zero & size;
    canvas.drawRect(
      rect,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: colors,
        ).createShader(rect),
    );
    canvas.drawCircle(
      Offset(size.width * .77, size.height * .22),
      size.width * .13,
      Paint()..color = Colors.white.withValues(alpha: .15),
    );
    for (var i = 0; i < 3; i++) {
      final y = size.height * (.57 + i * .13);
      final path =
          Path()
            ..moveTo(0, y)
            ..cubicTo(
              size.width * .3,
              y - size.height * .16,
              size.width * .6,
              y + size.height * .15,
              size.width,
              y - size.height * .05,
            )
            ..lineTo(size.width, size.height)
            ..lineTo(0, size.height)
            ..close();
      canvas.drawPath(
        path,
        Paint()..color = colors.last.withValues(alpha: .22 + i * .16),
      );
    }
  }

  @override
  bool shouldRepaint(CcbBackgroundPresetPainter oldDelegate) =>
      oldDelegate.preset != preset;
}

Future<CcbChatBackgroundSelection> createCcbBackgroundPreset(
  CcbBackgroundPreset preset,
) async {
  final recorder = ui.PictureRecorder();
  CcbBackgroundPresetPainter(
    preset,
  ).paint(Canvas(recorder), const Size(720, 1280));
  final picture = recorder.endRecording();
  try {
    final image = await picture.toImage(720, 1280);
    try {
      final data = await image.toByteData(format: ui.ImageByteFormat.png);
      if (data == null) throw StateError('Could not render background');
      return CcbChatBackgroundSelection(
        fileName: '${preset.name}.png',
        bytes: data.buffer.asUint8List(),
      );
    } finally {
      image.dispose();
    }
  } finally {
    picture.dispose();
  }
}
