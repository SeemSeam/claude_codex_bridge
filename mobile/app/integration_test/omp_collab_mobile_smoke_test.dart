import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:ccb_mobile/features/agent_chat/conversation_bubble.dart';
import 'package:ccb_mobile/features/agent_chat/conversation_timeline.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace.dart';
import 'package:ccb_mobile/features/provider_control/provider_control_sheet.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';
import 'package:ccb_mobile/repository/gateway_mobile_ccb_repository.dart';
import 'package:ccb_mobile/transport/http_gateway_transport.dart';
import 'package:ccb_mobile/transport/route_provider.dart';
import 'package:ccb_mobile/transport/relay_socket_gateway_transport.dart';

const _url = String.fromEnvironment('OMP_FIXTURE_URL');
const _token = String.fromEnvironment('OMP_FIXTURE_TOKEN');
const _relayUrl = String.fromEnvironment('OMP_RELAY_URL');
const _longHistory = bool.fromEnvironment('OMP_FIXTURE_LONG');

// Scenario controls generate explicit fixture content, not model-quality proof.
// All user-visible data uses the production repository over HTTP or encrypted RELAY.
Future<Map<String, dynamic>> _fixture(
  String action, [
  Map<String, Object?> body = const {},
]) async {
  final client = HttpClient();
  try {
    final request =
        action == 'stats'
            ? await client.getUrl(Uri.parse('$_url/__fixture/stats'))
            : await client.postUrl(Uri.parse('$_url/__fixture/$action'));
    request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $_token');
    if (action != 'stats') {
      final encoded = utf8.encode(jsonEncode(body));
      request.contentLength = encoded.length;
      request.add(encoded);
    }
    final response = await request.close();
    final text = await utf8.decoder.bind(response).join();
    expect(response.statusCode, 200, reason: text);
    return jsonDecode(text) as Map<String, dynamic>;
  } finally {
    client.close(force: true);
  }
}

Future<void> _until(
  WidgetTester tester,
  bool Function() predicate, {
  String reason = 'expected UI state',
}) async {
  final deadline = DateTime.now().add(const Duration(seconds: 30));
  while (!predicate() && DateTime.now().isBefore(deadline)) {
    await tester.pump(const Duration(milliseconds: 100));
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 100)),
    );
  }
  expect(predicate(), isTrue, reason: reason);
}

List<ConversationBubble> _replies(WidgetTester tester) =>
    tester
        .widgetList<ConversationBubble>(find.byType(ConversationBubble))
        .where(
          (bubble) => bubble.item.kind == CcbConversationItemKind.agentReply,
        )
        .toList();

Future<void> _tap(WidgetTester tester, Finder finder) async {
  // Native refresh/auto-follow can move a disclosure after ensureVisible.
  // Let pending layout/scroll work finish, then recenter and hit-test the
  // actual target. Never invoke callbacks directly or ignore missed taps.
  for (var attempt = 0; attempt < 12; attempt++) {
    await tester.ensureVisible(finder);
    await tester.pump(const Duration(milliseconds: 300));
    await Scrollable.ensureVisible(
      tester.element(finder),
      alignment: 0.5,
      duration: Duration.zero,
    );
    await tester.pump(const Duration(milliseconds: 100));
    final target = finder.hitTestable();
    if (target.evaluate().isNotEmpty) {
      await tester.tap(target);
      await tester.pump(const Duration(milliseconds: 300));
      return;
    }
  }
  fail('Target never became genuinely hit-testable after scrolling: $finder');
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  WidgetController.hitTestWarningShouldBeFatal = true;
  testWidgets('real OMP Collab mobile isolated deep smoke', (tester) async {
    tester.testTextInput.register();
    addTearDown(tester.testTextInput.unregister);
    expect(_url, isNotEmpty);
    expect(_token, isNotEmpty);
    final relayContext = SecurityContext(withTrustedRoots: false);
    RelaySocketGatewayTransport? relayTransport;
    if (_relayUrl.isNotEmpty) {
      final relayUri = Uri.parse(_relayUrl);
      expect(relayUri.scheme, 'wss');
      expect(relayUri.host, '127.0.0.1');
      relayContext.setTrustedCertificatesBytes(base64Decode(
        const String.fromEnvironment('OMP_RELAY_CA_B64')));
      relayTransport = RelaySocketGatewayTransport(
        profile: GatewayHostProfile(
          hostId: const String.fromEnvironment('OMP_RELAY_HOST'),
          deviceId: const String.fromEnvironment('OMP_RELAY_DEVICE'),
          routeProvider: RouteProvider(
            kind: RouteProviderKind.relay,
            gatewayUrl: relayUri.replace(scheme: 'https'),
            websocketUrl: relayUri,
            hostFingerprint: const String.fromEnvironment('OMP_RELAY_FINGERPRINT'),
            relayAccess: const RelayPhoneAccessCredentials(
              accessGrant: String.fromEnvironment('OMP_RELAY_GRANT'),
              phoneAuthPrivateKeyB64: String.fromEnvironment('OMP_RELAY_PHONE_KEY')),
            capabilities: const {'relay.forward', 'relay_reconnect'},
          ),
          scopes: const {'view', 'message_submit', 'provider_settings'},
        ),
        deviceToken: _token,
        httpClient: HttpClient(context: relayContext),
        timeout: const Duration(seconds: 60),
      );
      addTearDown(() => relayTransport!.close(force: true));
    }
    final repository = GatewayMobileCcbRepository(
      transport: relayTransport ?? HttpGatewayTransport(
        profile: GatewayHostProfile(
          hostId: 'omp-fixture',
          deviceId: 'fixture-device',
          routeProvider: RouteProvider(
            kind: RouteProviderKind.lan,
            gatewayUrl: Uri.parse(_url),
          ),
          scopes: const {'view', 'message_submit', 'provider_settings'},
        ),
        deviceToken: _token,
      ),
    );
    String? originalId;
    String? readableId;
    final initialHistoryIds = <String>{};
    if (_relayUrl.isNotEmpty || _longHistory) {
      await tester.runAsync(() async {
        final history = (await _fixture('stats'))['history'] as Map<String, dynamic>;
        expect(history['raw_history_bytes'], greaterThan(2 * 1024 * 1024));
        if (_longHistory) {
          expect(history['raw_history_bytes'], greaterThan(48 * 1024 * 1024));
          expect(history['wire_history_bytes'], greaterThan(48 * 1024 * 1024));
          expect(history['entry_count'], greaterThan(28000));
          expect(history['max_metadata_gap'], greaterThan(16384));
        }
        originalId = history['original_id'] as String;
        readableId = history['readable_id'] as String;
        final ids = <String>{};
        final bodies = <String>[];
        final cursors = <String>{};
        CcbConversationItem? original;
        String? cursor;
        var historyPages = 0;
        do {
          final page = await repository.getAgentConversation(
            projectId: 'omp-fixture', agent: 'mobile', namespaceEpoch: 4, cursor: cursor);
          historyPages++;
          expect(page.items, isNotEmpty);
          for (final item in page.items) {
            expect(ids.add(item.id), isTrue, reason: 'history must not duplicate ${item.id}');
            if (item.id == originalId) original = item;
            bodies.add(item.body);
          }
          cursor = page.nextCursor;
          if (cursor != null) expect(cursors.add(cursor), isTrue);
        } while (cursor != null);
        expect(historyPages, greaterThan(1));
        expect(ids.length, history['item_count']);
        final sortedIds = ids.toList()..sort();
        expect(sha256.convert(utf8.encode(sortedIds.join('\n'))).toString(), history['ids_sha256']);
        initialHistoryIds.addAll(ids);
        expect(bodies, contains('Fixture initial native snapshot'));
        if (_longHistory) {
          expect(bodies, contains('Fixture earliest history answer'));
          expect(bodies, contains('Fixture middle history answer'));
        }
        expect(original, isNotNull);
        expect(original!.nativeTurn!.entries, isNotEmpty);
        expect(original.nativeTurn!.toJson()['inline_window'], isTrue);
        expect(original.nativeTurn!.answerText, contains('Fixture oversized complete'));
        cursor = original.nativeTurn!.readCursor;
        expect(cursor, isNotNull);
        final fragments = <String, BytesBuilder>{};
        final metadata = <String, Map<String, dynamic>>{};
        final order = <String>[];
        final finished = <String>{};
        final contentCursors = <String>{};
        var contentPages = 0;
        final expectedBlocks = (history['semantic_blocks'] as List).cast<Map<String, dynamic>>();
        do {
          expect(contentCursors.add(cursor!), isTrue);
          final page = await repository.getAgentConversation(
            projectId: 'omp-fixture', agent: 'mobile', namespaceEpoch: 4, cursor: cursor);
          expect(page.items, isEmpty);
          final chunk = page.nativeContent!;
          expect(chunk.toJson()['schema_version'], 2);
          expect(chunk.pageIndex, contentPages);
          expect(chunk.totalBlocks, expectedBlocks.length);
          expect(chunk.version, history['original_version']);
          expect(chunk.blocks, isNotEmpty);
          var pageBytes = 0;
          for (final block in chunk.blocks) {
            if (block.type == 'tool_call' || block.type == 'tool_result') {
              expect(block.name, 'read');
              expect(block.callId, 'fixture-oversize-call');
            }
            if (block.type == 'tool_result') expect(block.isError, isFalse);
            final key = '${block.entryId}:${block.blockIndex}';
            final bytes = utf8.encode(block.text);
            pageBytes += bytes.length;
            expect(finished, isNot(contains(key)), reason: 'completed block must never repeat');
            expect(block.continuedBefore, fragments.containsKey(key));
            if (!fragments.containsKey(key)) {
              order.add(key);
              fragments[key] = BytesBuilder(copy: false);
              metadata[key] = {'entry_id':block.entryId, 'block_index':block.blockIndex,
                'role':block.role, 'type':block.type};
            } else {
              expect(key, order.last, reason: 'continued fragments must stay contiguous');
            }
            fragments[key]!.add(bytes);
            if (!block.continuedAfter) finished.add(key);
          }
          expect(pageBytes, lessThanOrEqualTo(32 * 1024));
          cursor = chunk.nextCursor;
          contentPages++;
        } while (cursor != null);
        expect(finished.length, expectedBlocks.length);
        final reconstructed = <Map<String, dynamic>>[];
        for (final key in order) {
          final bytes = fragments[key]!.takeBytes();
          reconstructed.add({...metadata[key]!, 'bytes':bytes.length,
            'sha256':sha256.convert(bytes).toString()});
        }
        // Exact per-source-block hashes prove Unicode fidelity, answer-first order,
        // and that item.body was not duplicated beside native assistant prose.
        expect(reconstructed, expectedBlocks);
        expect(contentPages, greaterThan(16));
        await _fixture('proof', {'history_pages':historyPages, 'history_items':ids.length,
          'content_pages':contentPages, 'semantic_blocks':reconstructed});
      });
    }
    final view = await tester.runAsync(
      () => repository.getProjectView('omp-fixture'),
    );
    expect(view, isNotNull);
    final agent = view!.agentByName('mobile')!;
    expect(agent.providerControl?.runtimeSource, 'collab');
    final controller = SelectedAgentWorkspaceController();
    late BuildContext appContext;
    await tester.pumpWidget(
      MaterialApp(
        locale: const Locale('en'),
        home: Builder(
          builder: (context) {
            appContext = context;
            return Scaffold(
              appBar: AppBar(title: const Text('OMP isolated smoke')),
              body: SelectedAgentWorkspace(
                repository: repository,
                terminalTransport: null,
                // OMP must select Collab despite the generic pane-input preference.
                usePaneInputForMessages: true,
                view: view,
                agent: agent,
                enableComposerCollapse: false,
                onRefreshView: () async => view,
                controller: controller,
              ),
            );
          },
        ),
      ),
    );
    await _until(
      tester,
      () => _replies(
        tester,
      ).any((b) => b.item.body == 'Fixture initial native snapshot'),
    );
    await tester.runAsync(() => _fixture('screenshot', {'name': 'initial'}));
    if (originalId != null) {
      Future<Finder> reachBubble(String id) async {
        final target = find.byWidgetPredicate((widget) =>
          widget is ConversationBubble && widget.item.id == id);
        ConversationTimeline timeline() => tester.widget<ConversationTimeline>(find.byType(ConversationTimeline));
        for (var attempt = 0; attempt < 12 && target.evaluate().isEmpty; attempt++) {
          final current = timeline();
          final targetIndex = current.items.indexWhere((item) => item.id == id);
          final visibleIndices = tester.widgetList<ConversationBubble>(find.byType(ConversationBubble))
            .map((bubble) => current.items.indexWhere((item) => item.id == bubble.item.id))
            .where((index) => index >= 0).toList()..sort();
          // Missing IDs require older pages. Already loaded newer IDs may be
          // below the current viewport after reading an earlier oversized turn.
          final newer = targetIndex >= 0 && visibleIndices.isNotEmpty && targetIndex > visibleIndices.last;
          await tester.drag(find.byKey(const ValueKey('agent-chat-timeline')),
            Offset(0, newer ? -1600 : 1600));
          await tester.pump(const Duration(milliseconds: 100));
          await _until(tester, () {
            final state = timeline();
            return !state.isLoading && state.controller.hasClients &&
              !state.controller.position.isScrollingNotifier.value;
          }, reason: 'history load and genuine timeline scroll settle before locating $id');
        }
        expect(target, findsOneWidget,
          reason: 'signed item $id is reachable through bounded UI history pages');
        return target;
      }
      final bubble = await reachBubble(originalId!);
      final expand = find.byKey(ValueKey('conversation-expand-$originalId'));
      if (!tester.widget<ConversationBubble>(bubble).expanded && expand.evaluate().isNotEmpty) {
        await _tap(tester, expand);
      }
      final readable = tester.widget<ConversationBubble>(bubble).item.nativeTurn!;
      expect(readable.entries, isNotEmpty);
      expect(readable.answerText, contains('Fixture oversized complete'));
      expect(readable.processBlocks.any((block) => block.block.name == 'read'), isTrue);
      await tester.ensureVisible(bubble);
      await tester.pump(const Duration(milliseconds: 300));
      await tester.runAsync(() => _fixture('screenshot', {'name': 'bounded-bubble'}));
      final read = find.descendant(of: bubble,
        matching: find.byKey(const ValueKey('native-read-original')));
      final expandedBeforeReader = tester.widget<ConversationBubble>(bubble).expanded;
      await _tap(tester, read);
      final reader = find.byKey(const ValueKey('native-original-reader'));
      final markdown = find.descendant(of: reader, matching: find.byType(MarkdownBody));
      String visibleOriginal() => [
        ...tester.widgetList<MarkdownBody>(markdown).map((body) => body.data),
        ...tester.widgetList<SelectableText>(find.descendant(of: reader,
          matching: find.byType(SelectableText))).map((text) => text.data ?? ''),
      ].join();
      await _until(tester, () => markdown.evaluate().isNotEmpty);
      final firstPage = visibleOriginal();
      expect(firstPage, contains('Fixture oversized original\n'));
      expect(firstPage, contains('## Readable Markdown'));
      expect(firstPage, isNot(contains('"native_turn"')));
      expect(firstPage, isNot(contains(r'\n')));
      final rendered = find.descendant(of: reader, matching: find.byType(RichText));
      final painted = [
        ...tester.widgetList<RichText>(rendered).map((text) => text.text.toPlainText()),
        ...tester.widgetList<SelectableText>(find.descendant(of: reader,
          matching: find.byType(SelectableText))).map((text) =>
            text.textSpan?.toPlainText() ?? text.data ?? ''),
      ].join('\n');
      expect(painted, contains('Readable Markdown'));
      expect(painted, isNot(contains('## Readable Markdown')));
      expect(painted, isNot(contains('**Unicode proof**')));
      expect(painted, contains('original line one'));
      await tester.runAsync(() => _fixture('screenshot', {'name':'original'}));
      expect(tester.widget<TextButton>(find.byKey(const ValueKey('native-original-previous'))).onPressed, isNull);
      await _tap(tester, find.byKey(const ValueKey('native-original-next')));
      await _until(tester, () => visibleOriginal().contains('原文🌍') && visibleOriginal() != firstPage);
      // Exact source-byte limits are checked in repository reconstruction above;
      // Markdown fence context is display-only and may add rendering bytes.
      expect(markdown, findsOneWidget);
      expect(visibleOriginal(), isNot(contains('Fixture oversized original')),
        reason: 'reader retains only the active bounded page, not previous page text');
      expect(find.textContaining('Continued'), findsWidgets);
      await tester.runAsync(() => _fixture('screenshot', {'name':'original-next'}));
      await _tap(tester, find.byKey(const ValueKey('native-original-previous')));
      await _until(tester, () => markdown.evaluate().isNotEmpty && visibleOriginal() == firstPage);
      expect(tester.widget<ConversationBubble>(bubble).expanded, expandedBeforeReader,
        reason: 'reading pages must preserve bubble expansion state');
      await _tap(tester, find.byKey(const ValueKey('native-original-close')));
      await _until(tester, () => find.byKey(const ValueKey('native-original-reader')).evaluate().isEmpty);
      final readableBubble = await reachBubble(readableId!);
      final readableExpand = find.byKey(ValueKey('conversation-expand-$readableId'));
      if (!tester.widget<ConversationBubble>(readableBubble).expanded && readableExpand.evaluate().isNotEmpty) {
        await _tap(tester, readableExpand);
      }
      await _tap(tester, find.descendant(of: readableBubble,
        matching: find.byKey(const ValueKey('native-read-original'))));
      await _until(tester, () => find.descendant(of: reader,
        matching: find.byType(MarkdownBody)).evaluate().isNotEmpty);
      expect(visibleOriginal(), contains('## Fixture readable answer'));
      expect(visibleOriginal(), isNot(contains('Fixture readable reasoning')));
      expect(visibleOriginal(), isNot(contains('fixture-readable.txt')));
      expect(visibleOriginal(), isNot(contains('Fixture readable tool result')));
      final disclosures = find.descendant(of: reader, matching: find.byWidgetPredicate((widget) =>
        widget.key is ValueKey<String> &&
        (widget.key! as ValueKey<String>).value.startsWith('native-content-execution-')));
      expect(disclosures, findsNWidgets(3));
      for (var index = 0; index < 3; index++) {
        final disclosure = disclosures.at(index);
        await _tap(tester, find.descendant(of: disclosure, matching: find.byType(TextButton)));
        final displayed = visibleOriginal();
        expect(displayed, contains([
          'Fixture readable reasoning\nCheck the source',
          '"path": "fixture-readable.txt"',
          'Fixture readable tool result\nA second readable result line.',
        ][index]));
        expect(displayed, isNot(contains(r'\n')));
        expect(displayed, isNot(contains('"native_turn"')));
        await _tap(tester, find.descendant(of: disclosure, matching: find.byType(TextButton)));
      }
      await _tap(tester, find.byKey(const ValueKey('native-original-close')));
      await _until(tester, () => reader.evaluate().isEmpty);
      final olderBefore = (await tester.runAsync(() => _fixture('stats')))!['older_page_reads'] as int;
      var loadedOlder = false;
      for (var attempt = 0; attempt < 8 && !loadedOlder; attempt++) {
        await tester.drag(find.byKey(const ValueKey('agent-chat-timeline')), const Offset(0, 1600));
        await tester.pump(const Duration(milliseconds: 400));
        final stats = (await tester.runAsync(() => _fixture('stats')))!;
        loadedOlder = (stats['older_page_reads'] as int) > olderBefore;
      }
      expect(loadedOlder, isTrue, reason:'A genuine Android timeline drag loads an older repository page');
      await tester.runAsync(() => _fixture('screenshot', {'name':'history'}));
      controller.refreshLatest();
    }
    final before = (await tester.runAsync(() => _fixture('stats')))!;

    Future<void> send(String body, int count) async {
      final input = find.byKey(const ValueKey('agent-message-composer'));
      await tester.ensureVisible(input);
      await tester.enterText(input, body);
      expect(
        tester.widget<TextField>(input).controller!.text,
        body,
        reason: 'Integration input immediately reaches composer',
      );
      FocusManager.instance.primaryFocus?.unfocus();
      await SystemChannels.textInput.invokeMethod<void>('TextInput.hide');
      await tester.pump(const Duration(milliseconds: 400));
      expect(
        tester.widget<TextField>(input).controller!.text,
        body,
        reason: 'Android input must preserve draft before Send',
      );
      await _until(
        tester,
        () =>
            tester
                .widget<IconButton>(
                  find.byKey(const ValueKey('agent-message-send-button')),
                )
                .onPressed !=
            null,
      );
      await _tap(
        tester,
        find.byKey(const ValueKey('agent-message-send-button')),
      );
      await tester.runAsync(() => _fixture('screenshot', {'name': 'live'}));
      final remainingDraft = tester.widget<TextField>(input).controller!.text;
      expect(
        remainingDraft,
        isEmpty,
        reason: 'Send must consume the visible native draft',
      );
      await _until(
        tester,
        () => tester
            .widgetList<ConversationBubble>(find.byType(ConversationBubble))
            .any((b) => b.item.body.contains(body)),
        reason: 'visible submitted input $count',
      );
      FocusManager.instance.primaryFocus?.unfocus();
      await tester.pump(const Duration(milliseconds: 300));
      Map<String, dynamic> stats = {};
      final deadline = DateTime.now().add(const Duration(seconds: 10));
      do {
        stats = (await tester.runAsync(() => _fixture('stats')))!;
        if (stats['prompts'] == count) break;
        await tester.pump(const Duration(milliseconds: 100));
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 100)),
        );
      } while (DateTime.now().isBefore(deadline));
      expect(stats['prompts'], count);
      expect(stats['terminal_calls'], 0);
    }

    await send('Fixture mobile first question', 1);
    await tester.runAsync(() => _fixture('live'));
    await _until(
      tester,
      () => _replies(tester).any((b) => b.item.body == 'Fixture live turn 1'),
    );
    final liveId =
        _replies(
          tester,
        ).singleWhere((b) => b.item.body == 'Fixture live turn 1').item.id;
    await tester.runAsync(() => _fixture('screenshot', {'name': 'live'}));
    await tester.runAsync(() => _fixture('large-live-one'));
    controller.refreshLatest();
    await _until(tester, () => _replies(tester).any((b) =>
      b.item.nativeTurn?.answerText.contains('BOUNDED LIVE LATEST ONE') == true));
    var boundedLive = _replies(tester).singleWhere((b) =>
      b.item.nativeTurn?.answerText.contains('BOUNDED LIVE LATEST ONE') == true);
    expect(boundedLive.item.id, liveId);
    expect(boundedLive.item.nativeTurn!.toJson()['inline_window'], isTrue);
    expect(utf8.encode(jsonEncode(boundedLive.item.toJson())).length, lessThan(32 * 1024));
    await tester.runAsync(() => _fixture('large-live-two'));
    controller.refreshLatest();
    await _until(tester, () => _replies(tester).any((b) =>
      b.item.nativeTurn?.answerText.contains('BOUNDED LIVE LATEST TWO') == true));
    boundedLive = _replies(tester).singleWhere((b) =>
      b.item.nativeTurn?.answerText.contains('BOUNDED LIVE LATEST TWO') == true);
    expect(boundedLive.item.id, liveId);
    expect(boundedLive.item.nativeTurn!.answerText, isNot(contains('BOUNDED LIVE LATEST ONE')));
    await tester.runAsync(() => _fixture('screenshot', {'name': 'bounded-bubble'}));
    await tester.runAsync(() => _fixture('finish'));
    await _until(
      tester,
      () =>
          _replies(tester).any((b) => b.item.nativeTurn?.answerText.contains('Fixture durable turn 1') == true),
    );
    final durable =
        _replies(
          tester,
        ).where((b) => b.item.nativeTurn?.answerText.contains('Fixture durable turn 1') == true).toList();
    expect(durable, hasLength(1));
    expect(
      durable.single.item.id,
      liveId,
      reason: 'live text becomes the same durable reply bubble',
    );
    expect(
      _replies(tester).where((b) => b.item.body == 'Fixture live turn 1'),
      isEmpty,
    );
    await tester.runAsync(() => _fixture('screenshot', {'name': 'durable'}));

    // Tool output is not built until both process and tool disclosures open.
    expect(find.textContaining('Fixture tool result 1'), findsNothing);
    final process = find.text('Execution process');
    await _tap(tester, process.last);
    final resultLabel = find.textContaining('Tool result');
    await _until(tester, () => resultLabel.evaluate().isNotEmpty);
    await _tap(tester, resultLabel.last);
    await _until(
      tester,
      () => find.textContaining('Fixture tool result 1').evaluate().isNotEmpty,
    );
    await tester.runAsync(() => _fixture('screenshot', {'name': 'tools'}));
    await _tap(tester, process.last);
    expect(find.textContaining('Fixture tool result 1'), findsNothing);

    await send('Fixture mobile second question', 2);
    await tester.runAsync(() => _fixture('live'));
    await _until(
      tester,
      () => _replies(tester).any((b) => b.item.body == 'Fixture live turn 2'),
    );
    expect(
      _replies(tester).where((b) => b.item.nativeTurn?.answerText.contains('Fixture durable turn 1') == true),
      hasLength(1),
    );
    await tester.runAsync(() => _fixture('finish'));
    await _until(
      tester,
      () =>
          _replies(tester).any((b) => b.item.body == 'Fixture durable turn 2'),
    );
    expect(
      _replies(tester).where((b) => b.item.body == 'Fixture durable turn 2'),
      hasLength(1),
    );
    expect(
      _replies(
        tester,
      ).singleWhere((b) => b.item.body == 'Fixture durable turn 2').item.id,
      isNot(liveId),
    );

    // Finishing the UI turn is not itself proof that the real guest observed
    // the ordered start/stop and durable input binding. Wait for host FIFO
    // reconciliation before asking the native model companion to mutate.
    Map<String, dynamic> settledQueue = {};
    final settleDeadline = DateTime.now().add(const Duration(seconds: 20));
    do {
      settledQueue = (await tester.runAsync(() => _fixture('stats')))!;
      if ((settledQueue['pending_inputs'] as List).isEmpty) break;
      await tester.pump(const Duration(milliseconds: 200));
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
    } while (DateTime.now().isBefore(settleDeadline));
    expect(settledQueue['pending_inputs'], isEmpty,
      reason:'Native FIFO must reconcile start/stop before model change: $settledQueue');

    final oldControl =
        (await tester.runAsync(
          () => repository.getAgentProviderControl(
            projectId: 'omp-fixture',
            agentName: 'mobile',
          ),
        ))!;
    final sheet = showProviderControlSheet(
      appContext,
      repository: repository,
      projectId: 'omp-fixture',
      agent: agent,
    );
    await _until(
      tester,
      () =>
          find
              .byKey(const ValueKey('provider-model-option-fixture/two'))
              .evaluate()
              .isNotEmpty,
    );
    await _tap(
      tester,
      find.byKey(const ValueKey('provider-model-option-fixture/two')),
    );
    await _until(
      tester,
      () => find.byType(ProviderControlSheet).evaluate().isEmpty,
      reason: 'native model selection closes after host readback',
    );
    await sheet;
    final afterModel = (await tester.runAsync(() => _fixture('stats')))!;
    expect(afterModel['model'], {'provider': 'fixture', 'id': 'two'});
    expect(afterModel['changes'], 1);
    expect(afterModel['pid'], before['pid']);
    expect(afterModel['session_id'], before['session_id']);
    expect(afterModel['generation'], before['generation']);
    final updatedControl =
        (await tester.runAsync(
          () => repository.getAgentProviderControl(
            projectId: 'omp-fixture',
            agentName: 'mobile',
          ),
        ))!;
    expect(updatedControl.control.activeModel, 'fixture/two');
    expect(updatedControl.control.restartPending, isFalse);
    expect(
      find.byKey(const ValueKey('provider-control-pending')),
      findsNothing,
    );
    final selectedSheet = showProviderControlSheet(
      appContext,
      repository: repository,
      projectId: 'omp-fixture',
      agent: agent,
    );
    await _until(
      tester,
      () =>
          find
              .byKey(const ValueKey('provider-model-option-fixture/two'))
              .evaluate()
              .isNotEmpty,
    );
    await tester.runAsync(() => _fixture('screenshot', {'name': 'model'}));
    Navigator.of(appContext).pop();
    await tester.pump(const Duration(milliseconds: 300));
    await selectedSheet;

    // Stale model revision must never mutate native runtime.
    await tester.runAsync(() async {
      try {
        await repository.updateAgentProviderSettings(
          projectId: 'omp-fixture',
          agentName: 'mobile',
          model: 'fixture/one',
          thinking: 'off',
          expectedRevision: oldControl.configRevision!,
          expectedNamespaceEpoch: 4,
          expectedProvider: 'omp',
          expectedRuntimeRevision: oldControl.control.runtimeRevision,
          idempotencyKey: 'fixture-stale-model',
        );
        fail('Stale model runtime was accepted');
      } catch (error) {
        expect(error, anyOf(isA<GatewayHttpException>(), isA<RelayGatewayException>()));
        expect(error is GatewayHttpException ? error.statusCode : (error as RelayGatewayException).statusCode, 409);
      }
    });

    await send('Fixture error question', 3);
    await tester.runAsync(() => _fixture('error'));
    await _until(tester, () => find.text('Failed').evaluate().isNotEmpty);
    final oldConversation =
        (await tester.runAsync(
          () => repository.getAgentConversation(
            projectId: 'omp-fixture',
            agent: 'mobile',
            namespaceEpoch: 4,
          ),
        ))!;
    await tester.runAsync(() => _fixture('disconnect'));
    controller.refreshLatest();
    // RELAY intentionally sanitizes unknown gateway 503 details. It presents
    // the generic failure/retry banner, not the HTTP host-unavailable wording.
    final disconnectBanner = find.byKey(ValueKey(
      _relayUrl.isNotEmpty ? 'omp-conversation-load-failed' : 'omp-collab-disconnected'));
    await _until(
      tester,
      () => disconnectBanner.evaluate().isNotEmpty,
    );
    expect(
      tester
          .widget<IconButton>(
            find.byKey(const ValueKey('agent-message-send-button')),
          )
          .onPressed,
      isNull,
    );
    await _tap(tester, find.descendant(of: disconnectBanner,
      matching: find.text(_relayUrl.isNotEmpty ? 'Retry' : 'Reconnect')));
    await tester.runAsync(() => _fixture('reconnect'));
    await _until(
      tester,
      () => disconnectBanner.evaluate().isEmpty,
    );
    final reconnectedConversation = (await tester.runAsync(
      () => repository.getAgentConversation(
        projectId: 'omp-fixture', agent: 'mobile', namespaceEpoch: 4)))!;
    expect(reconnectedConversation.ompSessionId, oldConversation.ompSessionId);
    expect(reconnectedConversation.ompGeneration, isNot(oldConversation.ompGeneration));
    await tester.runAsync(() async {
      try {
        await repository.submitAgentMessage(
          CcbAgentMessageSubmitRequest(
            projectId: 'omp-fixture',
            agentName: 'mobile',
            namespaceEpoch: 4,
            idempotencyKey: 'fixture-stale-send',
            body: 'Must not arrive',
            expectedOmpSessionId: oldConversation.ompSessionId,
            expectedOmpGeneration: oldConversation.ompGeneration,
          ),
        );
        fail('Stale Collab generation was accepted');
      } catch (error) {
        expect(error, anyOf(isA<GatewayHttpException>(), isA<RelayGatewayException>()));
        expect(error is GatewayHttpException ? error.statusCode : (error as RelayGatewayException).statusCode, 409);
      }
    });
    await tester.runAsync(() => _fixture('screenshot', {'name': 'reconnect'}));

    // Three real UI sends while the first model-free native turn is running.
    // The host—not the fixture controls—must own FIFO admission and dispatch.
    await tester.runAsync(() => _fixture('automatic_live'));
    await send('Fixture FIFO first question', 4);
    await tester.runAsync(() => _fixture('intermediate'));
    controller.refreshLatest();
    await _until(tester, () => _replies(tester).any(
      (bubble) => jsonEncode(bubble.item.nativeTurn?.toJson()).contains('Fixture intermediate continuation 4')));
    final firstFifoReply = _replies(tester).singleWhere(
      (bubble) => jsonEncode(bubble.item.nativeTurn?.toJson()).contains('Fixture intermediate continuation 4')).item.id;
    await send('Fixture FIFO second question', 4);
    await send('Fixture FIFO third question', 4);
    final queuedUi = tester.widgetList<ConversationBubble>(find.byType(ConversationBubble))
      .where((bubble) => bubble.item.body == 'Fixture FIFO second question' || bubble.item.body == 'Fixture FIFO third question')
      .map((bubble) => bubble.item).toList();
    expect(queuedUi, hasLength(2));
    expect(queuedUi.map((item) => item.state), everyElement(CcbConversationDeliveryState.queued));
    final queuedIds = {for (final item in queuedUi) item.body:item.id};
    await tester.runAsync(() => _fixture('screenshot', {'name':'fifo-queued'}));
    controller.refreshLatest();
    await _until(tester, () => queuedIds.entries.every((entry) =>
      tester.widgetList<ConversationBubble>(find.byType(ConversationBubble)).any(
        (bubble) => bubble.item.id == entry.value && bubble.item.body == entry.key && bubble.item.state == CcbConversationDeliveryState.queued)));

    Future<void> waitForDispatch(int count) async {
      final deadline = DateTime.now().add(const Duration(seconds: 20));
      Map<String, dynamic> stats = {};
      do {
        stats = (await tester.runAsync(() => _fixture('stats')))!;
        if (stats['prompts'] == count) break;
        await tester.pump(const Duration(milliseconds: 200));
        await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 200)));
      } while (DateTime.now().isBefore(deadline));
      expect(stats['prompts'], count);
      expect(stats['active_prompts'], 1);
      expect(stats['max_active_prompts'], 1);
    }
    await tester.runAsync(() => _fixture('finish'));
    await waitForDispatch(5);
    controller.refreshLatest();
    await _until(tester, () => _replies(tester).any(
      (bubble) => bubble.item.id == firstFifoReply && bubble.item.body.contains('Fixture durable turn 4')));
    final retainedFirst = _replies(tester).singleWhere((bubble) => bubble.item.id == firstFifoReply).item;
    expect(jsonEncode(retainedFirst.nativeTurn!.toJson()), contains('Fixture live turn 4'));
    expect(jsonEncode(retainedFirst.nativeTurn!.toJson()), contains('Fixture intermediate continuation 4'));
    await tester.runAsync(() => _fixture('finish'));
    await waitForDispatch(6);
    await tester.runAsync(() => _fixture('finish'));
    controller.refreshLatest();
    await _until(tester, () => _replies(tester).any((bubble) => bubble.item.body.contains('Fixture durable turn 6')));
    controller.refreshLatest();
    await _until(tester, () => _replies(tester).any((bubble) =>
      bubble.item.id == firstFifoReply &&
      bubble.item.nativeTurn?.turnId == retainedFirst.nativeTurn!.turnId &&
      bubble.item.body.contains('Fixture durable turn 4')),
      reason:'First FIFO bubble presentation identity survives completion and refresh');
    await tester.runAsync(() async {
      final all = <CcbConversationItem>[];
      String? cursor;
      do {
        final page = await repository.getAgentConversation(
          projectId:'omp-fixture',agent:'mobile',namespaceEpoch:4,cursor:cursor);
        all.addAll(page.items);
        cursor = page.nextCursor;
      } while (cursor != null);
      for (final entry in queuedIds.entries) {
        final item = all.singleWhere((item) => item.body == entry.key);
        expect(item.id, entry.value);
        expect(item.state, CcbConversationDeliveryState.sent);
      }
      // Workspace bubbles may carry a stable presentation alias. Repository
      // history retains the canonical native identity, not that widget alias.
      final retained = all.singleWhere((item) =>
        item.kind == CcbConversationItemKind.agentReply &&
        item.nativeTurn?.turnId == retainedFirst.nativeTurn!.turnId &&
        item.nativeTurn?.sessionId == retainedFirst.nativeTurn!.sessionId);
      expect(jsonEncode(retained.nativeTurn!.toJson()), contains('Fixture live turn 4'));
      expect(jsonEncode(retained.nativeTurn!.toJson()), contains('Fixture intermediate continuation 4'));
      expect(retained.body, contains('Fixture durable turn 4'));
      expect(all.any((item) => item.body == 'Fixture initial native snapshot'), isTrue);
      final stats = await _fixture('stats');
      expect(all.map((item) => item.id).toSet().length, all.length,
        reason:'FIFO refresh and older pagination must not duplicate native turns');
      if (initialHistoryIds.isNotEmpty) {
        expect(all.map((item) => item.id), containsAll(initialHistoryIds),
          reason:'All original large-history turns survive FIFO updates and pagination');
      }
      expect(stats['active_prompts'], 0);
      expect(stats['max_active_prompts'], 1);
      final order = (stats['dispatch_order'] as List).skip(3).toList();
      expect(order, hasLength(3));
      for (var index = 0; index < 3; index++) {
        expect(jsonEncode(order[index]), contains(['Fixture FIFO first question','Fixture FIFO second question','Fixture FIFO third question'][index]));
      }
      await _fixture('fifo-proof', {'dispatch_ordinals':[4,5,6], 'max_active_prompts':1,
        'retained_reply_id':firstFifoReply,'queued_ids':queuedIds});
    });
    await tester.runAsync(() async {
      final first = await repository.getAgentConversation(
        projectId:'omp-fixture',agent:'mobile',namespaceEpoch:4,limit:2);
      final olderCursor = first.nextCursor;
      expect(olderCursor, isNotNull);
      final older = await repository.getAgentConversation(
        projectId:'omp-fixture',agent:'mobile',namespaceEpoch:4,cursor:olderCursor,limit:2);
      final before = await _fixture('stats');
      final notices = jsonEncode(before['notices']);
      await _fixture('history-error');
      await expectLater(repository.getAgentConversation(
        projectId:'omp-fixture',agent:'mobile',namespaceEpoch:4), throwsA(anything));
      final failed = await _fixture('stats');
      expect(failed['connected'], isTrue);
      expect(failed['ready'], isTrue);
      expect(jsonEncode(failed['notices']), notices,
        reason:'Native ancestry/history failure must not cycle healthy guest WS');
      expect(failed['history_error'], isNotNull);
      expect(failed['joined_count'], before['joined_count']);
      expect(failed['left_count'], before['left_count']);
      await _fixture('screenshot', {'name':'history-error'});
      await _fixture('history-recover');
      final recovered = await repository.getAgentConversation(
        projectId:'omp-fixture',agent:'mobile',namespaceEpoch:4);
      expect(recovered.items, isNotEmpty);
      final healthy = await _fixture('stats');
      expect(healthy['connected'], isTrue);
      expect(healthy['history_error'], isNull);
      expect(healthy['joined_count'], before['joined_count']);
      expect(healthy['left_count'], before['left_count']);
      expect(jsonEncode(healthy['notices']), notices,
        reason:'History recovery must not emit native joined/left notices');
      await _fixture('append-history');
      final replay = await repository.getAgentConversation(
        projectId:'omp-fixture',agent:'mobile',namespaceEpoch:4,cursor:olderCursor,limit:2);
      expect(replay.items.map((item)=>item.id), orderedEquals(older.items.map((item)=>item.id)),
        reason:'An old page is immutable across native append');
      expect(replay.items.map((item)=>item.body), orderedEquals(older.items.map((item)=>item.body)));
      await _fixture('history-proof', {'notices_before':before['notices'],
        'notices_after':healthy['notices'],'retained_page_ids':replay.items.map((item)=>item.id).toList(),
        'connected_during_error':failed['connected']});
    });
    controller.refreshLatest();
    await _until(tester, () => _replies(tester).any((bubble)=>bubble.item.body=='Fixture append history answer'));
    await tester.runAsync(() => _fixture('screenshot', {'name':'history-recovered'}));
    await tester.runAsync(() => _fixture('screenshot', {'name':'fifo-complete'}));

    // An actual lifecycle notification exercises the product observer and timer.
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(seconds: 2)),
    );
    final pausedBefore = (await tester.runAsync(() => _fixture('stats')))!;
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(seconds: 4)),
    );
    final pausedAfter = (await tester.runAsync(() => _fixture('stats')))!;
    expect(
      pausedAfter['conversation_reads'],
      pausedBefore['conversation_reads'],
      reason: 'no foreground polling while backgrounded',
    );
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await _until(
      tester,
      () => tester.binding.lifecycleState == AppLifecycleState.resumed,
    );
    await tester.pump(const Duration(seconds: 2));
    final finalStats = (await tester.runAsync(() => _fixture('stats')))!;
    expect(finalStats['terminal_calls'], 0);
    expect(finalStats['prompts'], 6);
    expect(finalStats['changes'], 1);
    expect(finalStats['max_native_page_bytes'], lessThanOrEqualTo(262144));
    expect(finalStats['native_pages'], greaterThan(0));
    if (_relayUrl.isNotEmpty || _longHistory) {
      expect(finalStats['max_conversation_bytes'], lessThanOrEqualTo(192 * 1024));
      expect(finalStats['content_reads'], greaterThan(16));
    }
    await tester.runAsync(() => _fixture('screenshot', {'name': 'final'}));
    await tester.runAsync(() async {
      final old = await repository.getAgentConversation(
        projectId:'omp-fixture',agent:'mobile',namespaceEpoch:4,limit:2);
      expect(old.nextCursor, isNotNull);
      await _fixture('branch-history');
      await expectLater(repository.getAgentConversation(
        projectId:'omp-fixture',agent:'mobile',namespaceEpoch:4,cursor:old.nextCursor), throwsA(anything));
      final branch = await repository.getAgentConversation(
        projectId:'omp-fixture',agent:'mobile',namespaceEpoch:4);
      expect(branch.items.map((item)=>item.body), contains('Fixture isolated branch answer'));
      expect(branch.items.any((item)=>initialHistoryIds.contains(item.id)), isFalse);
      expect(branch.items.any((item)=>item.body.contains('Fixture durable')), isFalse);
      expect(branch.nextCursor, isNull);
      await _fixture('branch-proof', {'branch_ids':branch.items.map((item)=>item.id).toList(),
        'old_cursor_rejected':true});
    });
    controller.refreshLatest();
    await _until(tester, () => _replies(tester).any((bubble)=>bubble.item.body=='Fixture isolated branch answer'));
    await tester.runAsync(() => _fixture('screenshot', {'name':'history-branch'}));
    await tester.pumpWidget(const SizedBox.shrink());
    controller.dispose();
  });
}
