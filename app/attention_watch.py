#!/usr/bin/env python3
"""Apply the user's approval preference and alert for requests needing human input."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
SOUND = '/System/Library/Sounds/Glass.aiff'


def supported_approval(item):
    payload = item.get('payload') or {}
    decisions = payload.get('availableDecisions')
    if item.get('kind') != 'approval' or decisions is not None and 'accept' not in decisions:
        return False
    if payload.get('human_required'):
        return False  # the broker already applied the policy; a workspace rule asked for the user's answer
    return (payload.get('method') in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval') or
            payload.get('subtype') == 'can_use_tool' and (payload.get('name') or payload.get('tool_name')) != 'AskUserQuestion')


class AttentionWatch:
    def __init__(self, request, sound, audit=lambda item: None):
        self.request, self.sound, self.audit = request, sound, audit
        self.handled, self.alerted = set(), set()

    def process(self, items, config):
        notify = False
        for item in items:
            if not item.get('actionable'):
                continue
            key = (item['run_id'], item['request_id'])
            if key in self.handled:
                continue
            if config.get('auto_approve_tools') is True and supported_approval(item):
                try:
                    result = self.request('/api/run/control', {'id':item['run_id'], 'action':'answer',
                                                              'request_id':item['request_id'], 'decision':'accept'})
                    if result.get('ok') is not True:
                        raise RuntimeError(result.get('error') or 'Approval was not confirmed')
                    self.handled.add(key)
                    self.audit({'ts':time.time(), 'run_id':key[0], 'request_id':key[1],
                                'action':'accept', 'reason':'user_enabled_automatic_tool_approvals'})
                    continue
                except Exception as error:
                    print(f'Approval remains pending for {key[0]}/{key[1]}: {error}', flush=True)
            if key not in self.alerted and config.get('attention_sound') is not False:
                notify = True
                self.alerted.add(key)
        if notify:
            self.sound()


class Banners:
    """One macOS banner per new notification that needs an answer or asks for one (quota warnings).

    The first poll only learns what is already listed, so a restart (of this watcher or of the
    server that adds the banner flag) never repeats old items."""
    def __init__(self, post):
        self.post, self.known = post, None

    def process(self, items, config):
        seen = {item['id'] for item in items}
        new = [item for item in items if self.known is not None and item['id'] not in self.known
               and (item.get('needs_answer') or item.get('banner')) and item.get('state') == 'open']
        self.known = seen | (self.known or set())
        if config.get('notification_banners') is False or os.environ.get('CHAT_DASHBOARD_NO_BANNER'):
            return
        for item in new:
            self.post(item.get('title') or 'Needs your answer', item.get('chat_title') or '', item.get('body') or '')


def active(data=DATA):
    """True while a watcher holds its lock, so clients leave the chime to it."""
    try:
        with (Path(data)/'attention-watch.lock').open('r') as lease:
            fcntl.flock(lease, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(lease, fcntl.LOCK_UN)
            return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def request(port, path, body=None):
    req = urllib.request.Request(f'http://127.0.0.1:{port}'+path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


def play_sound():
    if not Path('/usr/bin/afplay').is_file() or not Path(SOUND).is_file():
        raise RuntimeError('Mac notification sound is unavailable')
    subprocess.Popen(['/usr/bin/afplay', SOUND], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def post_banner(title, subtitle, text):
    """A notification banner never activates an app. Values go in as arguments, never into the script."""
    script = ['on run argv', 'display notification (item 2 of argv) with title "Library" subtitle (item 1 of argv)', 'end run']
    heading = title + (' - ' + subtitle if subtitle else '')
    args = ['/usr/bin/osascript'] + [part for line in script for part in ('-e', line)] + [heading[:150], text[:200]]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def audit(item):
    with (DATA/'approval-actions.jsonl').open('a') as handle:
        handle.write(json.dumps(item)+'\n')


def main():
    DATA.mkdir(exist_ok=True)
    with (DATA/'attention-watch.lock').open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = {}
        watcher = AttentionWatch(lambda path, body: request(int(config.get('port',8744)),path,body), play_sound, audit)
        banners = Banners(post_banner)
        while True:
            try:
                config = json.loads((DATA/'config.json').read_text())
                value = request(int(config.get('port',8744)), '/api/attention')
                watcher.process(value.get('items') or [], config)
            except Exception as error:
                print(f'Attention watcher: {type(error).__name__}: {error}', flush=True)
            try:
                banners.process(request(int(config.get('port',8744)), '/api/notifications').get('items') or [], config)
            except urllib.error.HTTPError as error:
                if error.code != 404:  # an older server has no feed yet
                    print(f'Attention watcher banners: HTTP {error.code}', flush=True)
            except Exception as error:
                print(f'Attention watcher banners: {type(error).__name__}: {error}', flush=True)
            time.sleep(1)


if __name__ == '__main__':
    main()
