# -*- coding: utf-8 -*-
"""Install md-paper guard hooks for Claude Code, Codex, and OpenCode.

Claude keeps the established PowerShell hooks. Codex and OpenCode use one deployed Python policy
core through native thin adapters. Every config merge preserves unrelated user hooks/plugins.
"""
import argparse
import copy
import json
import os
import shutil
import sys
import tempfile

from setup_hooks import patch_cc_switch, patch_live_settings


POLICY_NAME = 'md_hook_policy.py'


def _is_policy_handler(handler):
    return isinstance(handler, dict) and POLICY_NAME in str(handler.get('command') or '')


def merge_codex_document(document, deployed_policy):
    if document is None:
        document = {}
    if not isinstance(document, dict):
        raise ValueError('Codex hooks.json must contain a JSON object')
    out = copy.deepcopy(document)
    hooks = out.get('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('Codex hooks.json hooks must be a JSON object')
    pre = hooks.get('PreToolUse', [])
    if not isinstance(pre, list):
        raise ValueError('Codex hooks.PreToolUse must be a JSON array')

    kept = []
    for index, group in enumerate(pre):
        if not isinstance(group, dict) or not isinstance(group.get('hooks', []), list):
            raise ValueError('Codex hooks.PreToolUse[%d] is malformed' % index)
        handlers = [h for h in group.get('hooks', []) if not _is_policy_handler(h)]
        if handlers:
            cloned = copy.deepcopy(group)
            cloned['hooks'] = handlers
            kept.append(cloned)

    posix_command = 'python3 "$HOME/.md-paper/hooks/%s" --platform codex' % POLICY_NAME
    windows_command = 'py "%s" --platform codex' % deployed_policy
    kept.append({
        'matcher': 'Bash|Edit|Write',
        'hooks': [{
            'type': 'command',
            'command': posix_command,
            'commandWindows': windows_command,
            'timeout': 30,
            'statusMessage': 'Checking md-paper source protections',
        }],
    })
    hooks['PreToolUse'] = kept
    out['hooks'] = hooks
    return out


def atomic_write_json(path, data):
    parent = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + '.', suffix='.tmp', dir=parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def deploy_policy(source, destination):
    if not os.path.isfile(source):
        raise FileNotFoundError('policy core not found: ' + source)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=POLICY_NAME + '.', suffix='.tmp',
                               dir=os.path.dirname(destination))
    os.close(fd)
    try:
        shutil.copy2(source, tmp)
        os.replace(tmp, destination)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def patch_codex(path, deployed_policy):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            document = json.load(f)
    else:
        document = {}
    merged = merge_codex_document(document, deployed_policy)
    atomic_write_json(path, merged)


def opencode_plugin_source():
    return r'''import { homedir } from "node:os"
import { join } from "node:path"

const policy = join(homedir(), ".md-paper", "hooks", "md_hook_policy.py")

async function runPolicy(payload) {
  const python = process.platform === "win32" ? "py" : "python3"
  const proc = Bun.spawn([python, policy, "--platform", "opencode"], {
    stdin: "pipe",
    stdout: "pipe",
    stderr: "pipe",
  })
  proc.stdin.write(JSON.stringify(payload))
  proc.stdin.end()
  const stdout = await new Response(proc.stdout).text()
  const stderr = await new Response(proc.stderr).text()
  const code = await proc.exited
  if (code !== 0) throw new Error(stderr || `md-paper hook exited ${code}`)
  return stdout.trim() ? JSON.parse(stdout) : { decision: "allow" }
}

export const MdPaperProtection = async ({ client, directory }) => ({
  "tool.execute.before": async (input, output) => {
    try {
      const result = await runPolicy({
        tool_name: input.tool,
        tool_input: output.args,
        cwd: directory,
      })
      if (result.additional_context) {
        try {
          await client.app.log({ body: {
            service: "md-paper-hook", level: "warn", message: result.additional_context,
          }})
        } catch {}
      }
      if (result.decision === "deny") {
        const denied = new Error(result.reason)
        denied.name = "MdPaperPolicyDeny"
        throw denied
      }
    } catch (error) {
      if (error?.name === "MdPaperPolicyDeny") throw error
      try {
        await client.app.log({ body: {
          service: "md-paper-hook", level: "error",
          message: `hook unavailable; proceeding fail-open: ${String(error)}`,
        }})
      } catch {}
    }
  },
})
'''


def write_opencode_plugin(path):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    text = opencode_plugin_source()
    fd, tmp = tempfile.mkstemp(prefix='md-paper.', suffix='.tmp', dir=parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def selftest():
    old = {
        'hooks': {
            'Stop': [{'hooks': [{'type': 'command', 'command': 'python stop.py'}]}],
            'PreToolUse': [
                {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'python user.py'}]},
                {'matcher': 'Write', 'hooks': [{'type': 'command',
                                                'command': 'py md_hook_policy.py --platform codex'}]},
            ],
        }
    }
    merged = merge_codex_document(old, r'C:\stable\md_hook_policy.py')
    assert merged['hooks']['Stop'] == old['hooks']['Stop']
    commands = [h['command'] for g in merged['hooks']['PreToolUse'] for h in g['hooks']]
    assert 'python user.py' in commands
    assert len([c for c in commands if POLICY_NAME in c]) == 1
    assert merge_codex_document(merged, r'C:\stable\md_hook_policy.py') == merged
    plugin = opencode_plugin_source()
    assert 'tool.execute.before' in plugin
    assert 'MdPaperPolicyDeny' in plugin and 'proceeding fail-open' in plugin
    print('OK setup_all_hooks self-test passed')


def main():
    home = os.path.expanduser('~')
    here = os.path.dirname(os.path.abspath(__file__))
    default_source = os.path.abspath(os.path.join(here, '..', 'md-swarm', POLICY_NAME))
    ap = argparse.ArgumentParser()
    ap.add_argument('--policy-source', default=default_source)
    ap.add_argument('--skip-claude', action='store_true')
    ap.add_argument('--skip-codex', action='store_true')
    ap.add_argument('--skip-opencode', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0

    deployed = os.path.join(home, '.md-paper', 'hooks', POLICY_NAME)
    deploy_policy(args.policy_source, deployed)
    print('[shared] deployed policy ->', deployed)

    if not args.skip_claude:
        settings = os.path.join(home, '.claude', 'settings.json')
        patch_live_settings(settings)
        result = patch_cc_switch(os.path.join(home, '.cc-switch', 'cc-switch.db'))
        print('[claude] merged settings; unrelated hooks preserved')
        if result:
            print('[claude] merged %d cc-switch templates; backup -> %s' %
                  (len(result[1]), result[0]))

    if not args.skip_codex:
        path = os.path.join(home, '.codex', 'hooks.json')
        patch_codex(path, deployed)
        print('[codex] merged ->', path)

    if not args.skip_opencode:
        path = os.path.join(home, '.config', 'opencode', 'plugins', 'md-paper.js')
        write_opencode_plugin(path)
        print('[opencode] installed local plugin ->', path)

    print('OK; restart each harness. In Codex run /hooks and trust the new definition.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('[ERROR]', exc, file=sys.stderr)
        sys.exit(2)
