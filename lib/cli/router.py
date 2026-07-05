from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from textwrap import dedent


AuxiliaryHandler = Callable[[Sequence[str]], int]
ManagementHandler = Callable[[argparse.Namespace], int]

_MANAGEMENT_COMMANDS = {"install", "update", "version", "uninstall", "reinstall"}


def dispatch_auxiliary_command(
    argv: Sequence[str],
    *,
    droid_handler: AuxiliaryHandler,
) -> int | None:
    tokens = list(argv)
    if tokens and tokens[0] == "droid" and len(tokens) > 1 and tokens[1] in {"setup-delegation", "test-delegation"}:
        return droid_handler(tokens[1:])
    return None


def dispatch_management_command(
    argv: Sequence[str],
    *,
    install_handler: ManagementHandler,
    update_handler: ManagementHandler,
    version_handler: ManagementHandler,
    uninstall_handler: ManagementHandler,
    reinstall_handler: ManagementHandler,
) -> int | None:
    tokens = list(argv)
    if not tokens or tokens[0] not in _MANAGEMENT_COMMANDS:
        return None

    parser = _build_management_parser()
    args = parser.parse_args(tokens)
    if args.command == "install":
        return install_handler(args)
    if args.command == "update":
        return update_handler(args)
    if args.command == "version":
        return version_handler(args)
    if args.command == "uninstall":
        return uninstall_handler(args)
    if args.command == "reinstall":
        return reinstall_handler(args)
    parser.print_help()
    return 1


def parse_start_args(argv: Sequence[str]) -> argparse.Namespace:
    return build_start_parser().parse_args(list(argv))


def print_start_help(*, file=None) -> None:
    print(
        dedent(
            """
            usage: ccb [-s] [-n]

            Primary workflow:
              ccb                  Start project agents from `.ccb/ccb.config`.
              ccb -s               Safe start. Disable CLI auto-permission override.
              ccb -n               Rebuild runtime state while preserving config and managed agent history.
              ccb clear [agent...]  Send provider-native /clear to managed agent panes.
              ccb restart <agent> Restart one idle configured agent pane through ccbd.
              ccb reload            Apply a safe additive config reload, or reject with diagnostics.
              ccb reload --dry-run  Validate and plan config reload without mutation.
              ccb maintenance status Show maintenance heartbeat config and stored status.
              ccb maintenance tick   Run one maintenance heartbeat diagnosis tick.
              ccb mobile serve       Start the loopback CCB Mobile gateway for the current project.
              ccb mobile devices     List paired mobile devices for the current project.
              ccb mobile revoke <id> Revoke one paired mobile device locally.
              ccb agent add NAME:PROVIDER --role ROLE [--window NAME|--window-class CLASS] --hidden --json
                                    Hot-load one runtime dynamic agent without rewriting ccb.config.
              ccb agent remove NAME|--agents a,b --policy park|unload --json
                                    Park or unload runtime dynamic agents.
              ccb agent park NAME|--agents a,b --json
                                    Disable dispatch for dynamic agents while preserving panes.
              ccb agent resume NAME|--agents a,b --json
                                    Re-enable dispatch for parked dynamic agents.
              ccb agent release NAME --idle-only --json
                                    Safely release one dynamic agent through role policy.
              ccb loop capacity ensure --loop-id ID --profile worker=1 --profile code_reviewer=1 --json
                                    Plan dynamic loop workers from configured loop.role_profiles.
              ccb loop topology propose|commit|reconcile|status|release --loop-id ID --json
                                    Manage runtime workflow graph desired/observed topology.
              ccb loop run-once --loop-id ID --task TEXT --json
                                    Run one worker/reviewer/orchestrator/round-checker round and write loop artifacts.
              ccb kill             Stop the current project's background runtime.
              ccb kill -f          Force cleanup project-owned runtime residue.
              ccb cleanup          Prune safe provider rebuildable caches after ccbd is stopped.
              ccb theme [light|dark|+|-]
                                    Set or show the global CCB UI theme.

            Core commands:
              ccb ask <agent> [from <sender>] <message>
              ccb doctor

            Diagnostics-only control-plane status:
              ccb ping <agent|ccbd>

            Diagnostics-only observer:
              ccb pend <agent|job_id> [N]
              ccb pend --watch <agent|job_id>
              ccb pend --inbox [--detail] <agent>
              ccb pend --queue [--detail] <agent|all>

            Advanced views:
              ccb queue [--detail] <agent|all>
              ccb trace <id>

            Advanced recovery:
              ccb repair <ack|retry|resubmit> ...

            Management:
              ccb install mobile    Start the server-wide CCB Mobile gateway and pairing QR.
              ccb version | ccb update [rich|mobile|VERSION] | ccb uninstall [rich] | ccb reinstall

            Tools:
              ccb rich
              ccb rich uninstall
              ccb update rich
              ccb update mobile

            Roles:
              ccb roles list
              ccb roles install agentroles.ccb_self
              ccb roles update agentroles.ccb_self
              ccb roles add agentroles.ccb_self:codex
              ccb roles install agentroles.archi
              ccb roles update agentroles.archi
              ccb roles sync [path]
              ccb roles add agentroles.archi:codex
              ccb roles doctor agentroles.archi
            """
        ).strip(),
        file=file,
    )


def print_kill_help(*, file=None) -> None:
    print(
        dedent(
            """
            usage: ccb kill [-f]

            Project runtime cleanup:
              ccb kill     Stop the current project's ccbd, agents, and tmux namespace.
              ccb kill -f  Force cleanup project-owned runtime residue before `ccb -n`.

            Notes:
              - `kill` is project-scoped. It does not bootstrap a missing `.ccb`.
              - `kill` still works when `.ccb` exists but `ccb.config` is missing or stale.
              - Use `ccb -n` after `ccb kill` when you want to rebuild runtime state but keep config and managed agent history.
            """
        ).strip(),
        file=file,
    )


def print_command_help(command_name: str, *, file=None) -> bool:
    text = _COMMAND_HELP.get(command_name)
    if text is None:
        return False
    print(dedent(text).strip(), file=file)
    return True


_COMMAND_HELP = {
    "ping": """
        usage: ccb ping <agent|all|ccbd>

        Diagnostics-only control-plane status:
          ccb ping <agent>   Show cached runtime status for one named agent.
          ccb ping all       Show cached mounted-agent status across the project.
          ccb ping ccbd      Show cached project daemon status.
    """,
    "pend": """
        usage: ccb pend [--watch|--inbox|--queue] [--detail] <agent|job_id|all> [N]

        Diagnostics-only weak observer surface:
          These commands are not part of normal ask workflows.
          Primary weak observer entrypoint:
            ccb pend <agent>                    Show a non-authoritative observer snapshot for one agent.
            ccb pend <job_id>                   Show a non-authoritative observer snapshot for one submitted job.
            ccb pend --watch <agent|job_id>     Stream non-authoritative observer events via the converged observer entrypoint.
            ccb pend --inbox <agent>            Show a non-authoritative inbox summary via the converged observer entrypoint.
            ccb pend --inbox --detail <agent>   Expand inbox-item detail via the converged observer entrypoint.
            ccb pend --queue <agent|all>        Show the same non-authoritative backlog summary exposed by `ccb queue`.
            ccb pend --queue --detail <agent>   Expand queued-event detail through the observer entrypoint.
            ccb pend <target> N                 Show the latest N observer snapshot items.
          Use `ccb trace <id>` for lineage when needed.
    """,
    "watch": """
        usage: ccb watch <agent|job_id>

        Diagnostics-only weak observer compatibility entrypoint:
          ccb watch <agent>   Stream non-authoritative observer events for one agent.
          ccb watch <job_id>  Stream non-authoritative observer events for one job until terminal completion or timeout.
          This is not part of normal ask workflows.
          Prefer `ccb pend --watch <agent|job_id>` as the converged observer entrypoint.
          Do not treat non-terminal watch output as authoritative completion.
          Use `ccb trace <id>` for lineage when needed.
    """,
    "queue": """
        usage: ccb queue [--detail] <agent_name|all>

        Advanced backlog view:
          ccb queue <agent_name>            Show a non-authoritative observer summary for one agent.
          ccb queue --detail <agent_name>   Expand queued-event details for one agent.
          ccb queue all                     Show non-authoritative observer backlog state across the project.
          `ccb pend --queue [--detail] <agent|all>` remains the equivalent weak-observer form.
          Use `ccb trace <id>` for lineage when needed.
    """,
    "trace": """
        usage: ccb trace <submission_id|message_id|attempt_id|reply_id|job_id>

        Advanced lineage view:
          ccb trace <id>   Show the full job/message/reply lineage for one id.
    """,
    "why": """
        usage: ccb why <job_id>

        No-reply classification:
          ccb why <job_id>   Show the no_reply_reason, description, and triage hint for a terminal job.
    """,
    "theme": """
        usage: ccb theme [dark|light|+|-|solarized|tokyo|gruvbox|rose-pine]

        CCB UI theme:
          ccb theme          Show current CCB theme preference.
          ccb theme +        Switch to the next CCB theme.
          ccb theme -        Switch to the previous CCB theme.
          ccb theme light    Use a light CCB tmux/sidebar theme.
          ccb theme dark     Use the dark CCB tmux/sidebar theme.

        Notes:
          - Ordinary terminals keep their own terminal theme; CCB only updates
            CCB-owned tmux/sidebar colors.
          - CCB-owned rich WezTerm follows this preference through its
            generated config.
    """,
    "agent": """
        usage:
          ccb agent status [--class CLASS] [--json]
          ccb agent show <agent> [--json]
          ccb agent add <name[:provider]> (--profile PROFILE | --role ROLE) [--window NAME|--window-class CLASS] [--hidden|--visible|--parked] [--json]
          ccb agent move <agent>|--agents a,b (--window NAME|--window-class CLASS|--loop-id LOOP --node-id NODE) [--json]
          ccb agent hide <agent>|--agents a,b [--json]
          ccb agent park <agent>|--agents a,b [--json]
          ccb agent resume <agent>|--agents a,b [--visible|--hidden] [--json]
          ccb agent remove <agent>|--agents a,b [--policy auto|hide|park|unload|kill] [--idle-only] [--force --reason TEXT] [--json]
          ccb agent release <agent>|--agents a,b [--policy auto|hide|park|unload] [--idle-only] [--reason TEXT] [--json]

        Dynamic agent lifecycle:
          ccb agent add helper:codex --role agentroles.general --hidden --json
              Write a runtime lifecycle record and project the agent into the active config overlay.
          ccb agent add reviewer --profile code_reviewer --hidden --json
              Add from [loop.role_profiles.<profile>] in ccb.config.
          ccb agent add planner2:codex --role agentroles.planner --window-class plan-orchestrate --hidden --json
              Hot-load into an existing class window or create that window through reload.
          ccb agent add worker1:codex --profile worker --loop-id round1 --node-id node1 --hidden --json
              Place execution agents in node-<loop-id>-<node-id> windows.
          ccb agent move planner2 --window review --json
              Move a dynamic session agent to an existing target window when mounted.
          ccb agent move --agents worker1,checker1 --window archive --json
          ccb agent move --agents worker1,checker1 --window-class execution-node --json
              Move multiple dynamic session agents through one reload transaction.
          ccb agent park planner2 --json
              Keep the pane and context but reject new dispatches until resumed.
          ccb agent park --agents planner2,broker1 --json
              Park a long-lived group through one config-only reload transaction.
          ccb agent resume planner2 --hidden --json
              Re-enable dispatch without changing pane ownership.
          ccb agent resume --agents planner2,broker1 --hidden --json
              Resume a parked group while preserving pane ownership.
          ccb agent remove helper --policy park --json
              Keep long-lived context discoverable while removing it from visible active work.
          ccb agent remove helper --policy unload --idle-only --json
              Remove the dynamic overlay when the agent is idle.
          ccb agent remove --agents worker1,checker1 --policy unload --idle-only --json
              Unload a dynamic worker/checker group through one reload transaction.
          ccb agent release reviewer --idle-only --json
              Apply role policy without exposing the destructive kill path.
          ccb agent release --agents worker1,checker1 --idle-only --json
              Apply role policies to a dynamic group as one all-or-nothing lifecycle update.
          ccb agent remove helper --policy kill --force --reason "operator reset" --json
              Force destructive removal; requires an explicit reason.
    """,
    "inbox": """
        usage: ccb inbox [--detail] <agent_name>

        Weak observer compatibility entrypoint:
          ccb inbox <agent_name>            Show a non-authoritative observer summary for one agent.
          ccb inbox --detail <agent_name>   Expand inbox-item detail for one agent.
          Prefer `ccb pend --inbox [--detail] <agent>` as the converged observer entrypoint.
          Use `ccb trace <id>` for lineage when needed.
    """,
    "logs": """
        usage: ccb logs <agent>

        Runtime diagnostics compatibility view:
          ccb logs <agent>   Tail the current runtime/session log for one agent.
          Prefer `ccb doctor logs <agent>` as the converged diagnostics entrypoint.
    """,
    "doctor-logs": """
        usage: ccb doctor logs <agent>

        Runtime log diagnostics subview:
          ccb doctor logs <agent>   Tail the current runtime/session log for one agent through the primary diagnostics entrypoint.
          `ccb logs <agent>` remains a compatibility alias.
    """,
    "ps": """
        usage: ccb ps

        Runtime diagnostics compatibility view:
          ccb ps   Show known runtime/session/workspace bindings.
          Prefer `ccb doctor ps` as the converged diagnostics entrypoint.
    """,
    "doctor-ps": """
        usage: ccb doctor ps

        Runtime diagnostics subview:
          ccb doctor ps   Show known runtime/session/workspace bindings through the primary diagnostics entrypoint.
          `ccb ps` remains a compatibility alias.
    """,
    "doctor-storage": """
        usage: ccb doctor storage [--json]

        Storage diagnostics subview:
          ccb doctor storage        Show .ccb storage class totals and largest entries.
          ccb doctor storage --json Emit full storage classification payload.
    """,
    "cleanup": """
        usage: ccb cleanup

        Storage cleanup:
          ccb cleanup   Prune safe provider rebuildable caches after ccbd is stopped.

        Safety:
          - Refuses to run while ccbd is active or ask jobs are pending/running.
          - Keeps Claude versions currently referenced by managed homes.
          - Does not remove provider sessions, auth, plugin bundles, mailbox data, or runtime authority.
          - Use `ccb doctor storage` before cleanup to inspect storage classes.
    """,
    "clear": """
        usage: ccb clear [agent_name|all]...

        Agent context reset:
          ccb clear             Send /clear to every configured mounted agent pane.
          ccb clear agent1      Send /clear to one agent pane.
          ccb clear agent1 agent2
                                Send /clear to multiple agent panes.

        Notes:
          - This sends the provider-native /clear command into each pane.
          - It does not delete .ccb state, workspaces, auth, sessions, or logs.
          - Use `ccb kill` or the sidebar restart control when you need process restart.
    """,
    "restart": """
        usage: ccb restart <agent_name>

        Guarded single-agent runtime restart:
          ccb restart agent1   Restart one configured mounted agent pane through ccbd.

        Safety:
          - Target authority comes from the current mounted daemon graph.
          - Refuses when the agent is busy, queued, delivering a reply, or waiting on result-chain continuation.
          - Does not support `restart all`, window-level restart, or raw tmux mutation.
    """,
    "maintenance": """
        usage: ccb maintenance <status|tick|schedule>

        Maintenance heartbeat diagnostics:
          ccb maintenance status   Show configured heartbeat policy plus stored schedule/status state.
          ccb maintenance tick     Run one diagnosis tick, update heartbeat status/schedule when enabled.
          ccb maintenance schedule --after 5m [--reason TEXT]
                                   Schedule the next heartbeat follow-up.

        Safety:
          - tick reads ccbd/project-view evidence and may write only maintenance-heartbeat status/schedule/activation records.
          - non-healthy tick may submit one silent ask to the configured assessor, default ccb_self.
          - tick does not run repairs or start providers.
          - runner is an internal project-scoped schedule consumer used by startup ensure.
          - enable and disable are config-authority in v1; edit [maintenance.heartbeat].enabled.
          - Status reads `.ccb/ccbd/maintenance-heartbeat/`, not `.ccb/ccbd/heartbeats/`.
    """,
    "mobile": """
        usage: ccb mobile <serve|devices|revoke>

        CCB Mobile gateway:
          ccb mobile serve
              Start the loopback, current-project HTTP gateway and emit a
              short-lived pairing code.
          ccb mobile serve --listen 127.0.0.1:0
              Start on a dynamic loopback port.
          ccb mobile serve --listen 127.0.0.1:8787 --public-url https://mobile.example.com --route-provider cloudflare_tunnel
              Keep the gateway loopback-bound but emit Cloudflare route
              metadata in the pairing payload.
          ccb mobile devices
              List paired devices from the current project's local mobile
              state.
          ccb mobile revoke dev_1234
              Revoke a paired device locally, without exposing a public admin
              route.

        Endpoints:
          GET /v1/health
          GET /v1/projects
          GET /v1/mobile/notifications  Server-sent task completion notifications
          GET /v1/projects/{project_id}/view
          POST /v1/pairing/claim
          GET /v1/devices/me
          POST /v1/devices/{device_id}/revoke
          POST /v1/projects/{project_id}/lifecycle
          POST /v1/projects/{project_id}/focus-agent
          POST /v1/projects/{project_id}/focus-window
          POST /v1/projects/{project_id}/terminals
          GET /v1/terminals/{terminal_id}  WebSocket terminal frames

        Safety:
          - The gateway still only accepts loopback listen addresses.
          - --public-url changes pairing metadata only; it does not bind a
            public listener.
          - Device listing and host-side revocation are local CLI actions,
            not public HTTP endpoints.
          - Revoking a device also revokes its still-open terminal handles.
          - It exposes current-project data only.
          - Pairing and device tokens are hashed under `.ccb/ccbd/mobile`.
          - Lifecycle stop requests go through ccbd `stop-all`, not raw tmux.
          - Lifecycle routes require a valid device token with `lifecycle` scope.
          - Focus routes require a valid device token with `focus` scope.
          - Notification streams require a valid device token with `notify`
            scope and publish low-sensitivity task completion metadata only.
          - Terminal-open routes require `terminal_input` scope and mint
            short-lived terminal tokens.
          - Terminal WebSocket streams validate terminal tokens and monotonic
            input sequence numbers before forwarding input to a tmux attach
            client.
          - It does not configure Cloudflare Tunnel, lifecycle, or
            multi-project registry.
          - Stopping the gateway does not stop ccbd, provider panes, or tmux.
    """,
    "loop": """
        usage:
          ccb loop capacity <ensure|status|release> ...
          ccb loop run-once --loop-id ID --task TEXT [--json]

        Loop capacity:
          ccb loop capacity ensure --loop-id round1 --profile worker=1 --profile code_reviewer=1 --json
              Write a deterministic dynamic-node plan from configured loop.role_profiles.
          ccb loop capacity status --loop-id round1 --json
              Read the stored loop capacity plan.
          ccb loop capacity release --loop-id round1 --policy auto --json
              Release idle loop-owned dynamic nodes and retain busy nodes.

        One-round execution:
          ccb loop run-once --loop-id round1 --task "Implement task" --json
              Ensure one worker and one code_reviewer, submit work, collect review,
              ask orchestrator for aggregation, release idle dynamic nodes, and
              write `.ccb/runtime/loops/<loop-id>/` artifacts.
    """,
    "doctor": """
        usage: ccb doctor [ps|logs <agent>|storage] [--output [PATH]]

        Deep diagnostics:
          ccb doctor               Print project diagnostic summary.
          ccb doctor ps            Show the runtime/session/workspace diagnostics subview.
          ccb doctor logs <agent>  Tail the runtime/session log diagnostics subview for one agent.
          ccb doctor storage       Show .ccb storage class totals.
          ccb doctor --output      Export a support bundle to the default path.
          ccb doctor --output PATH Export a support bundle to PATH.
          `ccb ps` and `ccb logs <agent>` remain compatibility entrypoints.
    """,
    "cancel": """
        usage: ccb cancel <job_id>

        Job control view:
          ccb cancel <job_id>   Request cancellation for one submitted job.
    """,
    "ack": """
        usage: ccb ack <agent_name> [inbound_event_id]

        Advanced recovery compatibility entrypoint:
          ccb ack <agent_name> [inbound_event_id]   Acknowledge reply/inbox progress for one agent.
          Prefer `ccb repair ack <agent_name> [inbound_event_id]` as the converged recovery entrypoint.
    """,
    "repair-ack": """
        usage: ccb repair ack <agent_name> [inbound_event_id]

        Advanced recovery subcommand:
          ccb repair ack <agent_name> [inbound_event_id]   Acknowledge reply/inbox progress for one agent.
          `ccb ack <agent_name> [inbound_event_id]` remains a compatibility alias.
    """,
    "retry": """
        usage: ccb retry <job_id|attempt_id>

        Advanced recovery compatibility entrypoint:
          ccb retry <job_id|attempt_id>   Retry one failed or incomplete job/attempt lineage.
          Prefer `ccb repair retry <job_id|attempt_id>` as the converged recovery entrypoint.
    """,
    "repair-retry": """
        usage: ccb repair retry <job_id|attempt_id>

        Advanced recovery subcommand:
          ccb repair retry <job_id|attempt_id>   Retry one failed or incomplete job/attempt lineage.
          `ccb retry <job_id|attempt_id>` remains a compatibility alias.
    """,
    "resubmit": """
        usage: ccb resubmit <message_id>

        Advanced recovery compatibility entrypoint:
          ccb resubmit <message_id>   Create a fresh submission from one prior message lineage.
          Prefer `ccb repair resubmit <message_id>` as the converged recovery entrypoint.
    """,
    "repair-resubmit": """
        usage: ccb repair resubmit <message_id>

        Advanced recovery subcommand:
          ccb repair resubmit <message_id>   Create a fresh submission from one prior message lineage.
          `ccb resubmit <message_id>` remains a compatibility alias.
    """,
    "repair": """
        usage: ccb repair <ack|retry|resubmit> ...

        Advanced recovery:
          ccb repair ack <agent_name> [inbound_event_id]   Acknowledge reply/inbox progress for one agent.
          ccb repair retry <job_id|attempt_id>             Retry one failed or incomplete job/attempt lineage.
          ccb repair resubmit <message_id>                 Create a fresh submission from one prior message lineage.
          Legacy `ack` / `retry` / `resubmit` commands remain compatibility entrypoints.
    """,
    "config": """
        usage: ccb config validate

        Config validation:
          ccb config validate   Validate `.ccb/ccb.config` for the current project.
    """,
    "reload": """
        usage: ccb reload [--dry-run]

        Reload:
          ccb reload             Apply safe explicit changes: view-only, append-only add_agent/add_window, idle remove_agent, or idle same-slot replace_agent.
          ccb reload --dry-run   Ask the mounted daemon to validate `.ccb/ccb.config` and return a no-mutation reload plan.

        Explicit reload boundary:
          - Busy remove_agent and replace_agent record bounded drain state and stop before mutation.
          - move_agent and arbitrary layout changes are rejected unless routed through guarded agent move commands.
          - No config watch is started; full arbitrary kill/reflow of existing panes is not implemented.
          - Non-dry-run output includes stage, plan_class, graph version, diagnostics, and any residue.
    """,
    "tools": """
        usage: ccb tools <doctor|install|update|enable|disable|launch|uninstall> workbench [--profile rich]

        Managed tool provisioning:
          ccb update rich                               Install/update and enable the rich workbench bundle.
          ccb uninstall rich                            Remove the rich workbench and return normal `ccb` startup.
          ccb rich                                      Launch the installed rich workbench.
          ccb rich uninstall                            Remove the rich workbench and return normal `ccb` startup.
          ccb tools doctor workbench --profile rich     Inspect the CCB-owned rich workbench bundle.
          ccb tools install workbench --profile rich    Generate isolated WezTerm/Yazi/Markdown config.
          ccb tools enable workbench --profile rich     Mark the bundle enabled for CCB-owned tool usage.
          ccb tools launch workbench --profile rich     Launch the generated workbench wrapper.
          ccb tools launch workbench --dry-run          Print launch commands without starting a terminal.
          ccb tools disable workbench                  Disable and close recorded CCB-owned workbench surfaces.
          ccb tools uninstall workbench                Remove generated workbench config and wrappers.
    """,
    "roles": """
        usage: ccb roles <list|show|install|update|sync|add|doctor> ...

        Role Pack management:
          ccb roles list
          ccb roles show agentroles.ccb_self
          ccb roles install agentroles.ccb_self
          ccb roles update agentroles.ccb_self
          ccb roles add agentroles.ccb_self:codex
          ccb roles show agentroles.archi
          ccb roles install agentroles.archi
          ccb roles update agentroles.archi
          ccb roles sync [path]
          ccb roles add agentroles.archi:codex
          ccb roles doctor agentroles.archi
    """,
}


def _build_management_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccb", description="Claude AI unified launcher", add_help=True)
    subparsers = parser.add_subparsers(dest="command", help="Subcommands")

    install_parser = subparsers.add_parser("install", help="Install or activate optional CCB capabilities")
    install_parser.add_argument("target", nargs="?", help="'mobile' to start the server-wide CCB Mobile gateway")
    install_parser.add_argument("--listen", default="127.0.0.1:8787")
    install_parser.add_argument("--public-url", default=None)
    install_parser.add_argument(
        "--route-provider",
        default="lan",
        choices=("lan", "tailnet", "cloudflare_tunnel", "relay"),
    )

    update_parser = subparsers.add_parser("update", help="Update CCB or an optional bundle")
    update_parser.add_argument("target", nargs="?", help="version like '4', '4.1', '4.1.3', or optional bundle 'rich'/'mobile'")

    subparsers.add_parser("version", help="Show version and check for updates")
    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall ccb, or uninstall the optional rich bundle")
    uninstall_parser.add_argument("target", nargs="?", help="'rich' to uninstall only the optional rich bundle")
    subparsers.add_parser("reinstall", help="Reinstall ccb and refresh configs")
    return parser


def build_start_parser() -> argparse.ArgumentParser:
    start_parser = argparse.ArgumentParser(
        prog="ccb",
        description="Claude AI unified launcher",
        add_help=False,
    )
    start_parser.add_argument("-s", "--safe", action="store_true", default=False, help=argparse.SUPPRESS)
    start_parser.add_argument(
        "-n",
        "--new-context",
        dest="new_context",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return start_parser
