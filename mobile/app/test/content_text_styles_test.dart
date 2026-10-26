import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/features/agent_chat/content_text_styles.dart';
import 'package:ccb_mobile/features/agent_chat/terminal_history_presentation.dart';

void main() {
  testWidgets('markdown style colors headings links code and quotes', (
    tester,
  ) async {
    late BuildContext capturedContext;
    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: Colors.teal),
          useMaterial3: true,
        ),
        home: Builder(
          builder: (context) {
            capturedContext = context;
            return const SizedBox.shrink();
          },
        ),
      ),
    );

    final colorScheme = Theme.of(capturedContext).colorScheme;
    final markdownTheme = ccbMarkdownTheme(capturedContext);
    final styleSheet = markdownTheme.styleSheet;

    expect(markdownTheme.h1?.color, colorScheme.primary);
    expect(markdownTheme.h2?.color, colorScheme.secondary);
    expect(markdownTheme.h3?.color, colorScheme.tertiary);
    expect(styleSheet.link?.color, colorScheme.secondary);
    expect(markdownTheme.inlineCode.color, colorScheme.tertiary);
    expect(styleSheet.codeBlock?.backgroundColor, isNotNull);
    expect(styleSheet.blockQuote?.barColor, colorScheme.secondary);
    expect(styleSheet.blockQuote?.backgroundColor, isNotNull);
  });

  test('terminal history style varies by block type', () {
    const colorScheme = ColorScheme.dark(
      primary: Color(0xff3be1b0),
      secondary: Color(0xff8ecae6),
      tertiary: Color(0xffe7c06b),
      error: Color(0xffffb4ab),
      onSurfaceVariant: Color(0xffc0c8cb),
    );
    const textTheme = TextTheme(bodyMedium: TextStyle(fontSize: 14));

    final commandStyle = terminalBlockTextStyle(
      textTheme: textTheme,
      colorScheme: colorScheme,
      type: 'command',
    );
    final errorStyle = terminalBlockTextStyle(
      textTheme: textTheme,
      colorScheme: colorScheme,
      type: 'error',
    );
    final logStyle = terminalBlockTextStyle(
      textTheme: textTheme,
      colorScheme: colorScheme,
      type: 'log',
    );

    expect(commandStyle.color, colorScheme.primary);
    expect(commandStyle.fontFamily, 'monospace');
    expect(errorStyle.color, colorScheme.error);
    expect(logStyle.color, colorScheme.onSurfaceVariant);
    expect(logStyle.fontFamily, isNull);
    expect(
      terminalBlockBackgroundColor(colorScheme, 'code'),
      isNot(colorScheme.surface),
    );
  });
}
