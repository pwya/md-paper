# -*- coding: utf-8 -*-
"""Portable md-paper hook policy for Codex and OpenCode adapters.

Reads one normalized tool event from stdin. In Codex mode it emits the native PreToolUse JSON
shape. In OpenCode mode it emits a small adapter result. The established Claude PowerShell hooks
remain unchanged until this implementation has accumulated real-world parity evidence.
"""
import argparse
import json
import os
import re
import sys
import tempfile


DEV_REMINDER = """[md-* DEV REMINDER] You are editing md-* skill code. Before finishing, check:
(1) allowlist > denylist; (2) explicit encoding; (3) AST > regex; (4) one bug = one test;
(5) fail loud/fast; (6) DRY; (7) retired; (8) single writer; META: put rules in code/tests."""


def _value(d, *names):
    for name in names:
        if isinstance(d, dict) and d.get(name) is not None:
            return d.get(name)
    return None


def _resolve(path, cwd):
    if not path:
        return None
    path = os.path.expandvars(os.path.expanduser(str(path).strip().strip('"\'')))
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.abspath(path)


def _is_protected(path):
    return bool(path and os.path.basename(path).lower() == 'manuscript.md'
                and os.path.isdir(os.path.join(os.path.dirname(path), 'manifest')))


def _file_targets(tool, args, cwd):
    targets = []
    direct = _value(args, 'file_path', 'filePath', 'path')
    if direct:
        targets.append(_resolve(direct, cwd))
    if tool == 'apply_patch':
        patch = str(_value(args, 'command', 'patch') or '')
        for match in re.finditer(r'^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$', patch, re.M):
            targets.append(_resolve(match.group(1), cwd))
    return [p for p in targets if p]


def _shell_command(tool, args):
    if tool in ('bash', 'powershell', 'shell', 'exec_command', 'unified_exec'):
        return str(_value(args, 'command', 'cmd') or '')
    return ''


def _paths_in_command(command, cwd):
    found = []
    pattern = r'"([^"\r\n]*manuscript\.md)"|\'([^\'\r\n]*manuscript\.md)\'|([^\s"\']*manuscript\.md)'
    for match in re.finditer(pattern, command, re.I):
        raw = next((g for g in match.groups() if g), '')
        raw = raw.lstrip('><').rstrip(';,')
        found.append(_resolve(raw, cwd))
    return [p for p in found if p]


def _protect_reason(tool, args, cwd):
    for path in _file_targets(tool, args, cwd):
        if _is_protected(path):
            return ('[protected source] %s is an md-unpack manuscript. AI tools must not write it '
                    'directly; create patch JSON and use apply_md_changeset.py.' % path)
    command = _shell_command(tool, args)
    if command and re.search(
            r'Set-Content|Add-Content|Out-File|WriteAllText|WriteAllLines|\.Save\b|'
            r'open\s*\([^)]*manuscript\.md[^)]*,\s*[\'\"][wax]|'
            r'>+\s*[\'\"]?[^\r\n]*manuscript\.md', command, re.I):
        for path in _paths_in_command(command, cwd):
            if _is_protected(path):
                return ('[protected source] %s cannot be written directly. Use the deterministic '
                        'changeset pipeline.' % path)
    return None


def _changeset_path(command, cwd):
    match = re.search(r'--changeset\s+(?:"([^"]+\.json)"|\'([^\']+\.json)\'|([^\s]+\.json))',
                      command, re.I)
    if not match:
        return None
    return _resolve(next(g for g in match.groups() if g), cwd)


def _triage_confirmed(path):
    if not path or not os.path.isfile(path):
        return False
    with open(path, encoding='utf-8') as f:
        for line in f:
            if not re.match(r'^\s*>?\s*\*\*人工确认', line):
                continue
            return '已确认' in line and '待确认' not in line
    return False


def _gate_reason(tool, args, cwd):
    command = _shell_command(tool, args)
    if 'apply_md_changeset.py' not in command:
        return None
    changeset = _changeset_path(command, cwd)
    if not changeset or not os.path.isfile(changeset):
        return None
    try:
        with open(changeset, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return None
    if not any(str(p.get('id') or '').startswith('机改') for p in data.get('patches', [])
               if isinstance(p, dict)):
        return None
    parent = os.path.dirname(changeset)
    candidates = [os.path.join(parent, 'md_triage.md'),
                  os.path.join(os.path.dirname(parent), 'swarm', 'md_triage.md')]
    if any(_triage_confirmed(p) for p in candidates):
        return None
    return ('[md-swarm human gate] Agent-authored patches are pending, but md_triage.md is not '
            'human-confirmed. Only the user may change the token to 已确认.')


def _dev_context(tool, args, cwd):
    for path in _file_targets(tool, args, cwd):
        norm = path.replace('\\', '/')
        if re.search(r'/(?:\.claude/skills|\.codex/skills|SKILL|md-paper)/md-[^/]+/[^/]+\.(?:py|ps1|lua)$',
                     norm, re.I):
            return DEV_REMINDER
    return None


def evaluate(event):
    tool = str(event.get('tool_name') or event.get('tool') or '').lower()
    args = event.get('tool_input') or event.get('args') or {}
    cwd = os.path.abspath(str(event.get('cwd') or os.getcwd()))
    reason = _protect_reason(tool, args, cwd) or _gate_reason(tool, args, cwd)
    return {'decision': 'deny' if reason else 'allow', 'reason': reason,
            'additional_context': _dev_context(tool, args, cwd)}


def _native_output(result):
    specific = {'hookEventName': 'PreToolUse'}
    if result['decision'] == 'deny':
        specific.update(permissionDecision='deny', permissionDecisionReason=result['reason'])
    elif result.get('additional_context'):
        specific['additionalContext'] = result['additional_context']
    else:
        return None
    return {'hookSpecificOutput': specific}


def selftest():
    with tempfile.TemporaryDirectory(prefix='md_hook_policy_') as td:
        os.makedirs(os.path.join(td, 'manifest'))
        man = os.path.join(td, 'manuscript.md')
        open(man, 'w', encoding='utf-8').write('text\n')
        direct = evaluate({'tool_name': 'Write', 'tool_input': {'file_path': man}, 'cwd': td})
        assert direct['decision'] == 'deny'
        patch = '*** Begin Patch\n*** Update File: manuscript.md\n@@\n-a\n+b\n*** End Patch'
        assert evaluate({'tool_name': 'apply_patch', 'tool_input': {'command': patch},
                         'cwd': td})['decision'] == 'deny'
        command = 'Set-Content -LiteralPath manuscript.md -Value x'
        assert evaluate({'tool_name': 'Bash', 'tool_input': {'command': command},
                         'cwd': td})['decision'] == 'deny'

        swarm = os.path.join(td, 'swarm')
        os.makedirs(swarm)
        cs = os.path.join(swarm, 'changeset.json')
        with open(cs, 'w', encoding='utf-8') as f:
            json.dump({'patches': [{'id': '机改-P1'}]}, f, ensure_ascii=False)
        apply_cmd = 'py apply_md_changeset.py --changeset "swarm/changeset.json" --manuscript manuscript.md'
        assert evaluate({'tool_name': 'Bash', 'tool_input': {'command': apply_cmd},
                         'cwd': td})['decision'] == 'deny'
        open(os.path.join(swarm, 'md_triage.md'), 'w', encoding='utf-8').write(
            '**人工确认：** 已确认\n')
        assert evaluate({'tool_name': 'Bash', 'tool_input': {'command': apply_cmd},
                         'cwd': td})['decision'] == 'allow'
    print('OK md_hook_policy self-test passed')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--platform', choices=('codex', 'claude', 'opencode'), required=True)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0
    try:
        event = json.load(sys.stdin)
        result = evaluate(event)
    except Exception:
        return 0  # hook layer is fail-open; deterministic apply remains the primary gate
    if args.platform == 'opencode':
        print(json.dumps(result, ensure_ascii=False))
    else:
        output = _native_output(result)
        if output:
            print(json.dumps(output, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
