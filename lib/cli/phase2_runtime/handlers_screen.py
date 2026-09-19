from __future__ import annotations

import json


def handle_screen(context, command, out, services) -> int:
    payload = services.agent_screen(context, command)
    if command.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=out)
    elif payload['status'] != 'ok':
        print(f"screen_status: failed\nerror: {payload['error']}", file=out)
    else:
        for key in ('agent', 'captured_at', 'pane', 'active_job'):
            if payload.get(key) is not None:
                print(f'{key}: {payload[key]}', file=out)
        print('--- screen ---', file=out)
        out.write(payload['text'])
        if not payload['text'].endswith('\n'):
            out.write('\n')
    return 0 if payload['status'] == 'ok' else 1
