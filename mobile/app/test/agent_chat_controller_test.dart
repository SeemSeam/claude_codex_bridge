import 'package:ccb_mobile/features/agent_chat/agent_chat_controller.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';
import 'package:ccb_mobile/models/readable_terminal_history.dart';
import 'package:ccb_mobile/native_session/native_session.dart';
import 'package:test/test.dart';

void main() {
  group('AgentChatController', () {
    test('latest refresh retains old pages and updates stable entries', () {
      final controller = AgentChatController();
      void apply(CcbAgentConversation value) => controller.applyRemoteConversation(
        agentName: 'lead', conversation: value, shouldScroll: false);
      apply(_nativeConversation([_agentReply(id: 'latest', body: 'before')], nextCursor: 'older'));
      controller.prependRemoteConversationPage(agentName: 'lead', conversation:
        _nativeConversation([_agentReply(id: 'old', body: 'loaded page')], nextCursor: 'oldest'));
      controller.toggleExpandedItem('lead', 'latest');
      apply(_nativeConversation([
        _agentReply(id: 'latest', body: 'after'),
        _agentReply(id: 'new', body: 'new reply'),
      ], nextCursor: 'older'));
      final value = controller.remoteConversationFor('lead')!;
      expect(value.items.map((item) => item.id), ['old', 'latest', 'new']);
      expect(value.items[1].body, 'after');
      expect(value.nextCursor, 'oldest');
      expect(controller.expandedItemIds('lead'), {'latest'});
    });

    test('session generation namespace and branch changes replace active history', () {
      for (final changed in [
        _nativeConversation([], session: 'other'),
        _nativeConversation([], generation: '2'),
        _nativeConversation([], epoch: 8),
        _nativeConversation([], branch: 'fork'),
      ]) {
        final controller = AgentChatController();
        controller.applyRemoteConversation(agentName: 'lead', conversation:
          _nativeConversation([_agentReply(id: 'old', body: 'old branch')]), shouldScroll: false);
        controller.toggleExpandedItem('lead', 'old');
        controller.applyRemoteConversation(agentName: 'lead', conversation: changed, shouldScroll: false);
        expect(controller.remoteConversationFor('lead')!.items, isEmpty);
        expect(controller.expandedItemIds('lead'), isEmpty);
        final stalePage = controller.prependRemoteConversationPage(
          agentName: 'lead', conversation: _nativeConversation([
            _agentReply(id: 'stale-page', body: 'superseded session'),
          ]));
        expect(stalePage.changed, isFalse);
        expect(controller.remoteConversationFor('lead')!.items, isEmpty);
      }
    });

    test('durable text survives snapshots while absent stream partial is removed', () {
      final controller = AgentChatController();
      void apply(List<NativeSessionEntry> entries, {String? cursor, bool window = false}) {
        final item = _agentReply(id: 'turn', body: 'preview').copyWith(
          nativeTurn: NativeSessionTurn(sessionId: 's', turnId: 'u',
            status: NativeSessionStatus.running, entries: entries,
            readCursor: cursor, inlineWindow: window));
        controller.applyRemoteConversation(agentName: 'lead',
          conversation: _nativeConversation([item]), shouldScroll: false);
      }
      apply([_nativeEntry('a', 'first'), _nativeEntry('stream', 'par', streaming: true)]);
      apply([_nativeEntry('b', 'second'), _nativeEntry('c', 'second')]);
      var value = controller.remoteConversationFor('lead')!.items.single.nativeTurn!;
      expect(value.entries.map((entry) => entry.id), ['a', 'b', 'c']);
      expect(value.answerText, 'first\n\nsecond\n\nsecond');
      apply([_nativeEntry('latest', 'latest answer')], cursor: 'original', window: true);
      value = controller.remoteConversationFor('lead')!.items.single.nativeTurn!;
      expect(value.readCursor, 'original');
      expect(value.inlineWindow, isTrue);
      expect(value.entries.map((entry) => entry.id), ['latest']);
      expect(value.answerText, 'latest answer');
      for (var index = 0; index < 12; index++) {
        apply([_nativeEntry('window-$index', 'answer $index')],
          cursor: 'original-$index', window: true);
        value = controller.remoteConversationFor('lead')!.items.single.nativeTurn!;
        expect(value.entries.map((entry) => entry.id), ['window-$index']);
        expect(value.answerText, 'answer $index');
        expect(value.readCursor, 'original-$index');
      }
      apply([_nativeEntry('complete', 'complete refresh')]);
      value = controller.remoteConversationFor('lead')!.items.single.nativeTurn!;
      expect(value.inlineWindow, isFalse);
      expect(value.entries.map((entry) => entry.id), ['complete']);
      apply([_nativeEntry('following', 'following update')]);
      value = controller.remoteConversationFor('lead')!.items.single.nativeTurn!;
      expect(value.entries.map((entry) => entry.id), ['complete', 'following']);
    });

    test('owns message ids and per-agent transient flags', () {
      final controller = AgentChatController();

      expect(controller.nextLocalMessageId('lead'), 'local-lead-0');
      expect(
        controller.nextTerminalLiveOutputId('lead'),
        'terminal-live-output-lead-1',
      );

      controller.beginLoadingConversation('lead');
      controller.beginSubmitting('lead');
      controller.collapseComposer('lead');
      controller.toggleExpandedItem('lead', 'item-1');

      expect(controller.isLoadingConversation('lead'), isTrue);
      expect(controller.isSubmitting('lead'), isTrue);
      expect(controller.isComposerCollapsed('lead'), isTrue);
      expect(controller.expandedItemIds('lead'), {'item-1'});

      controller.finishLoadingConversation('lead');
      controller.finishSubmitting('lead');
      controller.expandComposer('lead');
      controller.toggleExpandedItem('lead', 'item-1');

      expect(controller.isLoadingConversation('lead'), isFalse);
      expect(controller.isSubmitting('lead'), isFalse);
      expect(controller.isComposerCollapsed('lead'), isFalse);
      expect(controller.expandedItemIds('lead'), isEmpty);
    });

    test('applies remote conversations and prunes covered local messages', () {
      final controller = AgentChatController();
      final pending = _user(id: 'local-pending', body: 'same');
      final failed = _user(
        id: 'local-failed',
        body: 'same',
        state: CcbConversationDeliveryState.failed,
      );
      controller.addLocalMessage('lead', pending);
      controller.addLocalMessage('lead', failed);
      controller.setConversationError('lead', Exception('refresh failed'));

      final update = controller.applyRemoteConversation(
        agentName: 'lead',
        conversation: _conversation([
          _user(
            id: 'remote-user',
            body: 'same',
            state: CcbConversationDeliveryState.sent,
          ),
          _agentReply(id: 'remote-reply', body: 'done'),
        ]),
        shouldScroll: false,
      );

      expect(update.changed, isTrue);
      expect(controller.conversationErrorFor('lead'), isNull);
      expect(controller.remoteConversationFor('lead')!.items, hasLength(2));
      expect(controller.localMessagesFor('lead').map((item) => item.id), [
        'local-failed',
      ]);
      expect(controller.hasNewMessages('lead'), isTrue);

      controller.clearNewMessageFlag('lead');
      final unchanged = controller.applyRemoteConversation(
        agentName: 'lead',
        conversation: _conversation([
          _user(
            id: 'remote-user',
            body: 'same',
            state: CcbConversationDeliveryState.sent,
          ),
          _agentReply(id: 'remote-reply', body: 'done'),
        ]),
        shouldScroll: true,
      );

      expect(unchanged.changed, isFalse);
      expect(controller.hasNewMessages('lead'), isFalse);
    });

    test(
      'keeps local send time when matching remote user message lacks timing',
      () {
        final controller = AgentChatController();
        final sentAt = DateTime.utc(2026, 7, 1, 3, 42);
        final pending = _user(
          id: 'local-pending',
          body: 'same',
          sentAt: sentAt,
        );
        controller.addLocalMessage('lead', pending);

        controller.applyRemoteConversation(
          agentName: 'lead',
          conversation: _conversation([
            _user(
              id: 'remote-user',
              body: 'same',
              state: CcbConversationDeliveryState.sent,
            ),
          ]),
          shouldScroll: true,
        );

        expect(controller.localMessagesFor('lead'), isEmpty);
        expect(
          controller.remoteConversationFor('lead')!.items.single.sentAt,
          sentAt,
        );
      },
    );

    test('merges local attachment presentation into remote pane echo', () {
      final controller = AgentChatController();
      final sentAt = DateTime.utc(2026, 7, 1, 11, 30);
      final local = CcbConversationItem.userMessage(
        id: 'local-image',
        agentName: 'lead',
        body: 'please inspect',
        attachments: const [
          CcbMessageAttachment(
            fileId: 'mobile-file-1',
            fileName: 'photo.png',
            mimeType: 'image/png',
            sizeBytes: 68,
            kind: CcbMessageAttachmentKind.image,
            state: CcbMessageAttachmentState.available,
          ),
        ],
        state: CcbConversationDeliveryState.sent,
        sentAt: sentAt,
      );
      controller.addLocalMessage('lead', local);

      controller.applyRemoteConversation(
        agentName: 'lead',
        conversation: _conversation([
          _user(
            id: 'remote-image-echo',
            body:
                'please inspect\n'
                'Attached files:\n'
                '- photo.png (image/png, 68 bytes, file id: mobile-file-1)',
            state: CcbConversationDeliveryState.sent,
          ),
        ]),
        shouldScroll: true,
      );

      expect(controller.localMessagesFor('lead'), isEmpty);
      final remote = controller.remoteConversationFor('lead')!.items.single;
      expect(remote.id, 'remote-image-echo');
      expect(remote.body, 'please inspect');
      expect(remote.sentAt, sentAt);
      expect(remote.attachments, hasLength(1));
      expect(remote.attachments.single.fileName, 'photo.png');
      expect(
        remote.attachments.single.effectiveKind,
        CcbMessageAttachmentKind.image,
      );
    });

    test('normalizes remote pane attachment echo without local fallback', () {
      final controller = AgentChatController();

      controller.applyRemoteConversation(
        agentName: 'lead',
        conversation: _conversation([
          _user(
            id: 'remote-image-echo',
            body:
                'please inspect\n'
                'Attached files:\n'
                '- photo.png (image/png, 68 bytes, file id: mobile-file-1)',
            state: CcbConversationDeliveryState.sent,
          ),
        ]),
        shouldScroll: true,
      );

      final remote = controller.remoteConversationFor('lead')!.items.single;
      expect(remote.body, 'please inspect');
      expect(remote.attachments, hasLength(1));
      expect(remote.attachments.single.fileId, 'mobile-file-1');
      expect(remote.attachments.single.fileName, 'photo.png');
    });

    test('prepends older remote page and dedupes overlapping items', () {
      final controller = AgentChatController();
      controller.applyRemoteConversation(
        agentName: 'lead',
        conversation: _conversation([
          _agentReply(id: 'reply-new', body: 'new'),
        ], nextCursor: '2'),
        shouldScroll: true,
      );
      controller.setConversationError('lead', Exception('stale'));

      final update = controller.prependRemoteConversationPage(
        agentName: 'lead',
        conversation: _conversation([
          _agentReply(id: 'reply-old', body: 'old'),
          _agentReply(id: 'reply-new', body: 'new'),
        ]),
      );

      expect(update.changed, isTrue);
      expect(controller.conversationErrorFor('lead'), isNull);
      expect(
        controller.remoteConversationFor('lead')?.items.map((item) => item.id),
        ['reply-old', 'reply-new'],
      );
      expect(controller.hasOlderConversation('lead'), isFalse);
      expect(controller.hasNewMessages('lead'), isFalse);
    });

    test('replaces removes and bulk-updates local messages', () {
      final controller = AgentChatController();
      final first = _user(id: 'local-1', body: 'one');
      final second = _user(id: 'local-2', body: 'two');

      controller.addLocalMessage('lead', first);
      controller.addLocalMessage('lead', second);
      controller.replaceLocalMessage(
        'lead',
        'local-1',
        first.copyWith(state: CcbConversationDeliveryState.sent),
      );
      controller.updateLocalMessages(
        'lead',
        (items) => items.reversed.toList(),
      );

      expect(controller.localMessagesFor('lead').map((item) => item.id), [
        'local-2',
        'local-1',
      ]);
      expect(
        controller.localMessagesFor('lead').last.state,
        CcbConversationDeliveryState.sent,
      );

      controller.removeLocalMessage('lead', 'local-2');
      controller.removeLocalMessage('lead', 'local-1');

      expect(controller.localMessagesFor('lead'), isEmpty);
    });

    test('restores local messages and advances local message ids', () {
      final controller = AgentChatController();
      controller.restoreLocalMessages('lead', [
        _user(id: 'local-lead-4', body: 'retry later'),
      ]);

      expect(controller.localMessagesFor('lead').single.body, 'retry later');
      expect(controller.nextLocalMessageId('lead'), 'local-lead-5');

      controller.restoreLocalMessages('lead', const []);

      expect(controller.localMessagesFor('lead'), isEmpty);
      expect(controller.nextLocalMessageId('lead'), 'local-lead-6');
    });

    test('stores and clears refreshed terminal history', () {
      final controller = AgentChatController();
      const history = ReadableTerminalHistory(
        agentName: 'lead',
        historyScope: 'tmux_scrollback',
        blocks: [
          ReadableTerminalBlock(id: 'cmd', type: 'command', text: 'ccb status'),
        ],
      );

      controller.setRefreshedTerminalHistory('lead', history);

      expect(controller.refreshedTerminalHistoryFor('lead'), same(history));

      controller.clearRefreshedTerminalHistories();

      expect(controller.refreshedTerminalHistoryFor('lead'), isNull);
    });
  });
}

CcbAgentConversation _conversation(
  List<CcbConversationItem> items, {
  String? nextCursor,
}) {
  return CcbAgentConversation(
    projectId: 'proj',
    agentName: 'lead',
    namespaceEpoch: 7,
    items: items,
    nextCursor: nextCursor,
    generatedAt: DateTime.utc(2026, 6, 22),
  );
}

CcbConversationItem _user({
  required String id,
  required String body,
  CcbConversationDeliveryState state = CcbConversationDeliveryState.pending,
  DateTime? sentAt,
}) {
  return CcbConversationItem.userMessage(
    id: id,
    agentName: 'lead',
    body: body,
    state: state,
    sentAt: sentAt,
  );
}

CcbConversationItem _agentReply({required String id, required String body}) {
  return CcbConversationItem(
    id: id,
    agentName: 'lead',
    kind: CcbConversationItemKind.agentReply,
    title: 'Agent reply',
    body: body,
  );
}

CcbAgentConversation _nativeConversation(
  List<CcbConversationItem> items, {
  String? nextCursor,
  String session = 's',
  String generation = '1',
  int epoch = 7,
  String branch = 'main',
}) => CcbAgentConversation(
  projectId: 'proj', agentName: 'lead', namespaceEpoch: epoch, items: items,
  ompSessionId: session, ompGeneration: generation, branchId: branch,
  nextCursor: nextCursor,
);

NativeSessionEntry _nativeEntry(String id, String text, {bool streaming = false}) =>
  NativeSessionEntry(id: id, role: NativeSessionRole.assistant, streaming: streaming,
    blocks: [NativeSessionBlock(type: NativeSessionBlockType.text, text: text)]);
