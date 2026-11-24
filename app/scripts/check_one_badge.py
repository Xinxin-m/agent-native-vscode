"""Live check: every chat row on the Library page shows at most one status badge.

Loads the page headless (never brings a window forward), waits for the sidebar and the
auth-recovery poll, then counts status marks inside each [data-chat-key] row. Exits 1 when
any row has two or more, or when no chat rows rendered at all (an empty page is a failure).

    python3 scripts/check_one_badge.py [--url http://127.0.0.1:8744/] [--shots DIR]
"""
import argparse
import os
import sys

from playwright.sync_api import sync_playwright

# Every element that shows a chat's status in the old Library client.
MARKS = '.row-mark, .auth-recovery-dot, .workflow-badge, .live-side-status'

COUNT_JS = """marks => {
  const rows = [...document.querySelectorAll('[data-chat-key]')].filter(r => r.dataset.chatKey && !r.parentElement.closest('[data-chat-key]'));
  const bad = [];
  for (const row of rows) {
    const found = [...row.querySelectorAll(marks)].map(m => m.className);
    if (found.length > 1) bad.push({key: row.dataset.chatKey, marks: found});
  }
  // The sidebar mark must be the server's one value (status_mark from sidebar.resolve) when the server sends it.
  const want = new Map((typeof STATE !== 'undefined' && STATE ? STATE.chats : []).filter(c => 'status_mark' in c).map(c => [c.key, c.status_mark || '']));
  const wrong = [];
  for (const row of rows) {
    if (!want.has(row.dataset.chatKey) || row.classList.contains('live-side-row')) continue;
    const mark = row.querySelector('.row-mark'), shown = mark ? mark.className.replace('row-mark', '').trim() : '';
    if (row.querySelector('.recent-title, .card-chat-title') && shown !== want.get(row.dataset.chatKey)) wrong.push({key: row.dataset.chatKey, marks: ['shown ' + (shown || 'none'), 'server ' + (want.get(row.dataset.chatKey) || 'none')]});
  }
  return {rows: rows.length, marked: rows.filter(r => r.querySelector(marks)).length, bad, wrong, checked: want.size};
}"""


def check(page, label, shots):
    page.wait_for_selector('[data-chat-key]', state='attached', timeout=60000)
    page.wait_for_timeout(6500)  # one 5 s recovery poll in agent-workspace.js
    result = page.evaluate(COUNT_JS, MARKS)
    if shots:
        page.screenshot(path=os.path.join(shots, f'one-badge-{label}.png'))
    print(f"{label}: {result['rows']} chat rows, {result['marked']} with a status badge, {len(result['bad'])} with two or more, "
          f"{len(result['wrong'])} differing from the server's status_mark ({result['checked']} chats carry it)")
    for item in (result['bad'] + result['wrong'])[:20]:
        print('  ', item['key'], item['marks'])
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8744/')
    parser.add_argument('--shots', default='')
    args = parser.parse_args()
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)
    failed = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for label, size in (('desktop', (1440, 900)), ('phone', (390, 844))):
                page = browser.new_page(viewport={'width': size[0], 'height': size[1]})
                page.goto(args.url, wait_until='domcontentloaded', timeout=60000)
                result = check(page, label, args.shots)
                failed |= result['rows'] == 0 or bool(result['bad']) or bool(result['wrong'])
                page.close()
        finally:
            browser.close()
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
