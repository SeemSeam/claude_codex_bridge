import 'package:flutter/material.dart';

import '../features/agent_chat/agent_state_display.dart';

/// Canonical theme-aware accent for the authoritative agent working state.
///
/// Working must come from execution classification (`agentActivityDisplay` /
/// server-provided working fields); it is never inferred from focus (`active`),
/// unread messages, or queued text. Selection keeps the primary check
/// treatment, unread keeps its orange badge, and working uses this accent
/// alone on every surface.
Color workingStatusAccent(ColorScheme colorScheme) {
  return colorScheme.brightness == Brightness.dark
      ? const Color(0xFF81C995)
      : const Color(0xFF1E8E3E);
}

/// Label for a working aggregate. [count] > 0 renders "N working"; a count of
/// zero with a known-working boolean renders the generic "working" for
/// projects that only report the boolean.
String workingStatusCountLabel(int count) {
  return count > 0 ? '$count working' : 'working';
}

/// State color for [display]: green for working, theme error for exceptions,
/// neutral gray for idle, faint outline for offline/unknown.
Color agentStateColor(AgentActivityDisplay display, ColorScheme colorScheme) {
  switch (display) {
    case AgentActivityDisplay.working:
      return workingStatusAccent(colorScheme);
    case AgentActivityDisplay.exception:
      return colorScheme.error;
    case AgentActivityDisplay.idle:
      return colorScheme.onSurfaceVariant;
    case AgentActivityDisplay.offline:
      return colorScheme.outline;
    case AgentActivityDisplay.unknown:
      return colorScheme.onSurfaceVariant;
  }
}

/// Translucent state fill used behind whole labels (chips, tiles, rows).
Color agentStateFill(AgentActivityDisplay display, ColorScheme colorScheme) {
  if (display == AgentActivityDisplay.idle ||
      display == AgentActivityDisplay.unknown) {
    return colorScheme.surfaceContainerHigh.withValues(alpha: 0.45);
  }
  final base = agentStateColor(display, colorScheme);
  return colorScheme.brightness == Brightness.dark
      ? base.withValues(alpha: 0.26)
      : base.withValues(alpha: 0.14);
}

/// Orange for the independent unread badge. Distinct from the working green
/// and the exception red on both themes.
Color unreadBadgeColor(ColorScheme colorScheme) {
  return colorScheme.brightness == Brightness.dark
      ? const Color(0xFFFFB77C)
      : const Color(0xFFE8710A);
}
