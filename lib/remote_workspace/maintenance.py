"""Conservative retention: only retired, acknowledged transaction payloads.

Compact receipts and CCB job history remain. Current/blocked transactions,
active transcripts, user branches and working files are never removed.
"""

import os
from pathlib import Path
import time

from .endpoint import job_name
from .files import read_json, write_json
from .objects import SyncError


def prune_local(service, name, agent, *, keep=20, days=14, apply=False):
    if type(keep) is not int or not 2 <= keep <= 10000 or not 1 <= days <= 3650:
        raise SyncError('retention requires keep >= 2 and age >= 1 day')
    p = service.profile(name, agent)
    with service.locked(name) as root:
        state = read_json(root / 'state.json')
        if state.get('phase') != 'done' or state.get('blocked'):
            raise SyncError('unfinished or blocked workspace; pruning refused')
        receipts = sorted(
            root.glob('*.receipt.json'), key=lambda f: f.stat().st_mtime, reverse=True
        )
        cutoff = time.time() - days * 86400
        jobs = []
        for receipt in receipts[keep:]:
            job = job_name(receipt.name.removesuffix('.receipt.json'))
            if (
                job != state['job_id']
                and not receipt.is_symlink()
                and receipt.stat().st_mtime < cutoff
            ):
                value = read_json(receipt)
                if value.get('status') == 'synced':
                    jobs.append((job, value))
        result = {
            'dry_run': not apply,
            'jobs': [job for job, _ in jobs],
            'keep': keep,
            'days': days,
            'protected_job': state['job_id'],
            'receipts_and_active_transcripts_retained': True,
        }
        if not apply or not jobs:
            return result
        response = service.transport_factory(p).call(
            {
                'version': 1,
                'workspace_id': p['workspace_id'],
                'op': 'prune',
                'job_id': state['job_id'],
                'jobs': result['jobs'],
                'min_age_days': days,
            }
        )
        service._binding(p, response, state['job_id'])
        retired = set(response.get('retired', []))
        if not retired <= set(result['jobs']):
            raise SyncError('invalid remote retention receipt')
        result['retired'] = sorted(retired)
        git = service._git(p)
        for job, value in jobs:
            if job not in retired:
                continue
            # Retain identity/hash evidence before removing bulky JSON objects.
            write_json(root / (job + '.receipt.json'), {**value, 'payloads_retired': True})
            for suffix in ('.request.json', '.result.json', '.completion.json'):
                path = root / (job + suffix)
                if path.is_symlink():
                    raise SyncError('symlink retention target')
                path.unlink(missing_ok=True)
            for kind, oid in (('remote', value['head']), ('remote-input', value['base'])):
                ref = f"refs/ccb/{kind}/{p['workspace_id']}/{job}"
                # compare-and-swap; never delete a ref which changed meanwhile
                existing = git.run('for-each-ref', '--format=%(objectname)', ref).decode().strip()
                if existing:
                    git.run('update-ref', '-d', ref, oid)
        write_json(root / 'last-prune.json', result)
        return result


def prune_remote(endpoint, job, request):
    state = endpoint.state()
    if state.get('job_id') != job or state.get('phase') != 'acked':
        raise SyncError('only an acknowledged, idle workspace can retire payloads')
    jobs = request.get('jobs')
    days = request.get('min_age_days')
    if (
        not isinstance(jobs, list)
        or len(jobs) > 2000
        or len(set(jobs)) != len(jobs)
        or not isinstance(days, (int, float))
        or not 1 <= days <= 3650
    ):
        raise SyncError('invalid retention request')
    retired = []
    for old_job in jobs:
        job_name(old_job)
        if old_job == job:
            raise SyncError('cannot retire current job')
        receipt = endpoint.root / (old_job + '.result.json')
        if not receipt.exists():
            continue
        result = read_json(receipt)
        if result.get('job_id') != old_job:
            raise SyncError('retention receipt mismatch')
        if result.get('payloads_retired'):
            retired.append(old_job)
            continue
        if receipt.stat().st_mtime >= time.time() - days * 86400:
            continue
        write_json(
            receipt,
            {k: v for k, v in result.items() if k != 'objects'} | {'payloads_retired': True},
        )
        artifact = endpoint.root / 'artifacts' / (old_job + '.txt')
        if artifact.is_symlink():
            raise SyncError('symlink artifact')
        artifact.unlink(missing_ok=True)
        retired.append(old_job)
    return {
        'ok': True,
        'version': 1,
        'workspace_id': endpoint.config['workspace_id'],
        'job_id': job,
        'retired': retired,
    }
