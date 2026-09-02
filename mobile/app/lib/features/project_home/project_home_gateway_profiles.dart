import '../../pairing/gateway_pairing.dart';

String projectHomeGatewayProfileKey(GatewayPairedHost profile) {
  return '${profile.profile.hostId}/${profile.profile.deviceId}';
}

String projectHomeGatewayProfileLabel(GatewayPairedHost profile) {
  final route = profile.profile.routeProvider.kind.wireName;
  final relayMode = profile.profile.routeProvider.relayMode;
  final routeLabel = route == 'relay' && relayMode != null
      ? '$route/${relayMode.wireName}'
      : route;
  return '${profile.profile.hostId} / ${profile.profile.deviceId} / $routeLabel';
}

/// Short human-facing name of a paired computer, used where several hosts are
/// listed together. A [customName] chosen on this phone always wins; otherwise
/// the pairing contract carries no computer name, so the paired project id is
/// preferred and an abbreviated host id is the fallback.
String projectHomeGatewayProfileHostName(
  GatewayPairedHost profile, {
  String? customName,
}) {
  final chosen = customName?.trim() ?? '';
  if (chosen.isNotEmpty) {
    return chosen;
  }
  final projectId = profile.projectId?.trim() ?? '';
  if (projectId.isNotEmpty) {
    return projectId;
  }
  final hostId = profile.profile.hostId.trim();
  return hostId.length <= 12 ? hostId : '${hostId.substring(0, 12)}…';
}

/// Name the user gave one paired computer, or null while that computer still
/// carries its pairing-derived name. [customNames] is the map returned by
/// [GatewayHostProfileStore.listHostNames].
String? projectHomeCustomHostName(
  Map<String, String> customNames,
  GatewayPairedHost profile,
) {
  final name =
      customNames[projectHomeCustomHostNameKey(profile)]?.trim() ?? '';
  return name.isEmpty ? null : name;
}

/// Key of one paired computer inside a custom host name map.
String projectHomeCustomHostNameKey(GatewayPairedHost profile) {
  return GatewayHostProfileStore.hostNameKey(
    hostId: profile.profile.hostId,
    deviceId: profile.profile.deviceId,
  );
}

List<GatewayPairedHost> sortProjectHomeGatewayProfiles(
  Iterable<GatewayPairedHost> profiles,
) {
  return [...profiles]..sort(
    (a, b) => projectHomeGatewayProfileLabel(
      a,
    ).compareTo(projectHomeGatewayProfileLabel(b)),
  );
}

List<GatewayPairedHost> mergeProjectHomeGatewayProfiles(
  Iterable<GatewayPairedHost> profiles,
  GatewayPairedHost paired,
) {
  final key = projectHomeGatewayProfileKey(paired);
  return sortProjectHomeGatewayProfiles([
    for (final profile in profiles)
      if (projectHomeGatewayProfileKey(profile) != key) profile,
    paired,
  ]);
}
