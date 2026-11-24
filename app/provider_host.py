"""Own one provider process so it outlives a server restart.

    python3 provider_host.py <host dir> <provider argv...>

The server writes provider input to <host dir>/in.fifo; this process copies it
to the provider's stdin pipe. The provider writes stdout to out.jsonl and
stderr to err.log, so no pipe end belongs to the server. This process holds a
write end of the FIFO, so the provider never sees EOF when the server goes
away. SIGUSR1 releases it: once the server has closed its own end too, the
FIFO reads EOF and the provider's stdin is closed. A pipe, not the FIFO, is
the provider's stdin because Claude Code 2.1.328 on macOS never reads EOF
from a FIFO. When the provider exits, exit.json records its code.
"""
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path


def copy_input(source, stdin):
    try:
        while chunk := os.read(source, 65536):
            stdin.write(chunk)
            stdin.flush()
        stdin.close()
    except BrokenPipeError:
        pass  # the provider exited; main() records it


def main(host_dir, argv):
    host = Path(host_dir)
    fifo = host / 'in.fifo'
    if not fifo.exists():
        os.mkfifo(fifo, 0o600)
    holder = [os.open(fifo, os.O_RDWR)]

    def release(*_):
        if holder:
            os.close(holder.pop())
    signal.signal(signal.SIGUSR1, release)
    source = os.open(fifo, os.O_RDONLY)
    with open(host / 'out.jsonl', 'ab') as out, open(host / 'err.log', 'ab') as err:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err, start_new_session=True)
    tmp = host / 'provider.json.tmp'
    tmp.write_text(json.dumps({'pid': proc.pid}))
    tmp.rename(host / 'provider.json')
    threading.Thread(target=copy_input, args=(source, proc.stdin), daemon=True).start()
    code = proc.wait()
    tmp = host / 'exit.json.tmp'
    tmp.write_text(json.dumps({'returncode': code}))
    tmp.rename(host / 'exit.json')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2:])
