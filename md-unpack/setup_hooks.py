# -*- coding: utf-8 -*-
"""Install md-paper Claude Code hooks without overwriting unrelated hooks.

The merge function is deliberately pure/testable. The CLI patches the live Claude settings and,
when present, every Claude provider template in cc-switch.db. Existing non-md hook events and
handlers are preserved; prior md-paper handlers are replaced by one canonical copy.
"""
import argparse
import copy
import datetime
import json
import os
import shutil
import sqlite3
import sys
import tempfile


MD_HOOK_FILES = (
    'md_dev_checklist_hook.ps1',
    'md_protect_hook.ps1',
    'md_swarm_gate_hook.ps1',
)
CMD_TMPL = (r'cmd /c powershell -NoProfile -ExecutionPolicy Bypass -File '
            r'"%USERPROFILE%\.claude\skills\md-swarm\{}"')


def canonical_hooks_tree():
    return {'PreToolUse': [
        {'matcher': 'Write|Edit|MultiEdit',
         'hooks': [{'type': 'command', 'command': CMD_TMPL.format(MD_HOOK_FILES[0])}]},
        {'matcher': 'Write|Edit|MultiEdit|Bash|PowerShell',
         'hooks': [{'type': 'command', 'command': CMD_TMPL.format(MD_HOOK_FILES[1])}]},
        {'matcher': 'Bash|PowerShell',
         'hooks': [{'type': 'command', 'command': CMD_TMPL.format(MD_HOOK_FILES[2])}]},
    ]}


def _is_md_handler(handler):
    if not isinstance(handler, dict):
        return False
    command = str(handler.get('command') or '').lower()
    return any(name.lower() in command for name in MD_HOOK_FILES)


def merge_hooks(existing):
    """Return existing hooks with only md-paper handlers replaced by the canonical set."""
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise ValueError('hooks must be a JSON object')
    merged = copy.deepcopy(existing)
    pre = merged.get('PreToolUse', [])
    if not isinstance(pre, list):
        raise ValueError('hooks.PreToolUse must be a JSON array')

    kept_groups = []
    for index, group in enumerate(pre):
        if not isinstance(group, dict):
            raise ValueError('hooks.PreToolUse[%d] must be a JSON object' % index)
        handlers = group.get('hooks', [])
        if not isinstance(handlers, list):
            raise ValueError('hooks.PreToolUse[%d].hooks must be a JSON array' % index)
        kept_handlers = [h for h in handlers if not _is_md_handler(h)]
        if kept_handlers:
            kept = copy.deepcopy(group)
            kept['hooks'] = kept_handlers
            kept_groups.append(kept)

    merged['PreToolUse'] = kept_groups + canonical_hooks_tree()['PreToolUse']
    return merged


def merge_settings(config):
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError('settings JSON must be an object')
    out = copy.deepcopy(config)
    out['hooks'] = merge_hooks(out.get('hooks'))
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


def patch_live_settings(path):
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            current = json.load(f)
    else:
        current = {}
    patched = merge_settings(current)
    atomic_write_json(path, patched)
    return len(patched['hooks']['PreToolUse'])


def patch_cc_switch(db_path):
    if not os.path.exists(db_path):
        return None
    backup_dir = os.path.join(os.path.dirname(db_path), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    backup = os.path.join(backup_dir, 'md_hooks_patch_' + stamp + '.db')
    shutil.copy2(db_path, backup)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id,name,is_current,settings_config FROM providers WHERE app_type='claude'")
        rows = cur.fetchall()
        patched_names = []
        for provider_id, name, is_current, settings_config in rows:
            config = json.loads(settings_config) if settings_config else {}
            config = merge_settings(config)
            cur.execute('UPDATE providers SET settings_config=? WHERE id=?',
                        (json.dumps(config, ensure_ascii=False, separators=(',', ':')), provider_id))
            patched_names.append((name, is_current == 1))
        conn.commit()
        return backup, patched_names
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def selftest():
    user_handler = {'type': 'command', 'command': 'python user_hook.py'}
    old_md = {'type': 'command', 'command': 'powershell md_protect_hook.ps1'}
    original = {
        'PreToolUse': [
            {'matcher': 'Write', 'hooks': [user_handler, old_md]},
            {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'python another.py'}]},
        ],
        'PostToolUse': [{'matcher': '*', 'hooks': [user_handler]}],
    }
    merged = merge_hooks(original)
    commands = [h['command'] for g in merged['PreToolUse'] for h in g['hooks']]
    assert 'python user_hook.py' in commands
    assert 'python another.py' in commands
    assert all(commands.count(CMD_TMPL.format(name)) == 1 for name in MD_HOOK_FILES)
    assert not any(c == 'powershell md_protect_hook.ps1' for c in commands)
    assert merged['PostToolUse'] == original['PostToolUse']
    assert merge_hooks(merged) == merged, 'installer must be idempotent'
    try:
        merge_hooks([])
        raise AssertionError('malformed hooks must fail loudly')
    except ValueError:
        pass

    # P0 integration: cc-switch template merge preserves an unrelated provider hook.
    with tempfile.TemporaryDirectory(prefix='md_setup_hooks_selftest_') as td:
        db = os.path.join(td, 'cc-switch.db')
        conn = sqlite3.connect(db)
        conn.execute('CREATE TABLE providers (id TEXT, name TEXT, is_current INTEGER, '
                     'settings_config TEXT, app_type TEXT)')
        config = {'hooks': original}
        conn.execute('INSERT INTO providers VALUES (?,?,?,?,?)',
                     ('p1', 'demo', 1, json.dumps(config), 'claude'))
        conn.commit()
        conn.close()
        backup, names = patch_cc_switch(db)
        assert os.path.isfile(backup) and names == [('demo', True)]
        conn = sqlite3.connect(db)
        saved = json.loads(conn.execute('SELECT settings_config FROM providers').fetchone()[0])
        conn.close()
        saved_commands = [h['command'] for g in saved['hooks']['PreToolUse'] for h in g['hooks']]
        assert 'python user_hook.py' in saved_commands and 'python another.py' in saved_commands
    print('OK setup_hooks self-test passed')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--settings', default=os.path.expanduser('~/.claude/settings.json'))
    ap.add_argument('--cc-switch-db', default=os.path.expanduser('~/.cc-switch/cc-switch.db'))
    ap.add_argument('--skip-cc-switch', action='store_true')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return 0

    count = patch_live_settings(args.settings)
    print('[live] patched %s -> PreToolUse %d groups (unrelated hooks preserved)' %
          (args.settings, count))
    if not args.skip_cc_switch:
        result = patch_cc_switch(args.cc_switch_db)
        if result is None:
            print('[cc-switch] not installed -> live settings only')
        else:
            backup, names = result
            for name, is_current in names:
                print('         merged template: %s%s' % (name, ' <== CURRENT' if is_current else ''))
            print('[cc-switch] patched %d templates; backup -> %s' % (len(names), backup))
    print('OK')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('[ERROR]', exc, file=sys.stderr)
        sys.exit(2)
