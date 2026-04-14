#!/usr/bin/env python3

"""
Copyright (c) 2026 Rogerio O. Ferraz <rogerio.o.ferraz@gmail.com>

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

import datetime
import json
import re
import selectors
import shlex
import signal
import subprocess
import syslog
import textwrap
import time

from collections import defaultdict
from dataclasses import dataclass, field
from textwrap import dedent

WINDOW_SECONDS = 5
WINDOW_USEC = WINDOW_SECONDS * 1_000_000

MATCH_RE = re.compile(
    r"(Buffer I/O error|blk_update_request: I/O error|end_request: I/O error|I/O error, dev |critical medium error)"
)

DEVICE_PATTERNS = [
    re.compile(r"\bon\s+dev\s+([^,\s]+)"),
    re.compile(r"\bdev\s+([^,\s]+)"),
]

GREP_RE = (
    r"Buffer I/O error|blk_update_request: I/O error|"
    r"end_request: I/O error|I/O error, dev |critical medium error"
)

NOTIFY_HANDLER = "/usr/local/sbin/iocop-notify-handler"
LOGGER_TAG = "disk-io-watch"


@dataclass
class Batch:
    pending: bool = False
    batch_start_usec: int = 0
    first_ts_usec: int = 0
    last_ts_usec: int = 0
    fail_count: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def reset(self) -> None:
        self.pending = False
        self.batch_start_usec = 0
        self.first_ts_usec = 0
        self.last_ts_usec = 0
        self.fail_count.clear()

    def start(self, ts_usec: int) -> None:
        self.pending = True
        self.batch_start_usec = ts_usec
        self.first_ts_usec = ts_usec
        self.last_ts_usec = ts_usec

    def add(self, ts_usec: int, device: str) -> None:
        if not self.pending:
            self.start(ts_usec)
        self.last_ts_usec = ts_usec
        self.fail_count[device] += 1

    def due(self, now_usec: int) -> bool:
        return self.pending and (now_usec - self.batch_start_usec >= WINDOW_USEC)


def matches_io_error(message: str) -> bool:
    return bool(MATCH_RE.search(message))


def extract_device(message: str) -> str:
    for pattern in DEVICE_PATTERNS:
        match = pattern.search(message)
        if match:
            return match.group(1)
    return "unknown"


def format_journal_ts(ts_usec: int) -> str:
    sec, micros = divmod(ts_usec, 1_000_000)
    local_tz = datetime.datetime.now().astimezone().tzinfo
    dt = datetime.datetime.fromtimestamp(sec, tz=local_tz)
    return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{micros:06d}{dt.strftime('%z')}"


def build_detail_command(first_ts_usec: int, last_ts_usec: int) -> str:
    since = format_journal_ts(first_ts_usec)
    until = format_journal_ts(last_ts_usec)

    cmd = [
        "sudo",
        "journalctl",
        "-k",
        "-o",
        "short-iso-precise",
        "--since",
        since,
        "--until",
        until,
        f"--grep={GREP_RE}",
        "--no-pager",
    ]
    return " ".join(shlex.quote(part) for part in cmd)


def compute_table_widths(fail_count: dict[str, int]) -> tuple[int, int]:
    total = sum(fail_count.values())

    count_width = max(
        len("FAILURES"),
        len(str(total)),
        *(len(str(count)) for count in fail_count.values()),
    )

    device_width = max(
        len("DEVICE"),
        *(len(device) for device in fail_count.keys()),
    )

    return count_width, device_width


def build_table_rows(rows: list[tuple[str, int]], count_width: int, device_width: int) -> str:
    return "\n".join(
        f"{count:>{count_width}}  {device:<{device_width}}"
        for device, count in rows
    )


def build_notification_body(batch: Batch) -> str:
    rows = sorted(batch.fail_count.items(), key=lambda item: (item[1], item[0]))
    total = sum(batch.fail_count.values())

    count_width = max(len("FAILURES"), len(str(total)))
    device_width = max(
        len("DEVICE"),
        len("TOTAL"),
        *(len(device) for device, _ in rows),
    )

    table_lines = [
        f"{'FAILURES':>{count_width}}  {'DEVICE':<{device_width}}",
        f"{'-' * count_width}  {'-' * device_width}",
        *(f"{count:>{count_width}}  {device:<{device_width}}" for device, count in rows),
        f"{'-' * count_width}  {'-' * device_width}",
        f"{total:>{count_width}}  {'TOTAL':<{device_width}}",
    ]

    return "\n".join([
        f"Disk I/O failures in this {WINDOW_SECONDS}s batch:",
        "",
        *table_lines,
        "",
        f"First failure: {format_journal_ts(batch.first_ts_usec)}",
        f"Last failure:  {format_journal_ts(batch.last_ts_usec)}",
        "",
        "For more detail, run:",
        build_detail_command(batch.first_ts_usec, batch.last_ts_usec),
    ])

def send_notification(body: str) -> None:
    subprocess.run(
        [NOTIFY_HANDLER, "Disk I/O failures detected", body, "critical"],
        check=True,
    )


def log_error_line(message: str) -> None:
    syslog.syslog(syslog.LOG_ERR, message)


def log_summary(batch: Batch) -> None:
    syslog.syslog(
        syslog.LOG_ERR,
        "Sent throttled I/O failure summary for interval "
        f"{format_journal_ts(batch.first_ts_usec)} .. {format_journal_ts(batch.last_ts_usec)}",
    )


def current_time_usec() -> int:
    return time.time_ns() // 1_000


def flush_batch_if_due(batch: Batch, now_usec: int) -> None:
    if not batch.due(now_usec):
        return

    body = build_notification_body(batch)
    send_notification(body)
    log_summary(batch)
    batch.reset()


def make_journal_proc():
    return subprocess.Popen(
        [
            "journalctl",
            "-kf",
            "-n0",
            "-o",
            "json",
            "--output-fields=__REALTIME_TIMESTAMP,MESSAGE",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def parse_journal_line(raw: str) -> tuple[int, str] | None:
    try:
        entry = json.loads(raw)
    except json.JSONDecodeError:
        return None

    ts = entry.get("__REALTIME_TIMESTAMP")
    msg = entry.get("MESSAGE")

    if not isinstance(ts, str) or not isinstance(msg, str):
        return None

    try:
        ts_usec = int(ts)
    except ValueError:
        return None

    return ts_usec, msg.replace("\n", " ").replace("\t", " ")


def main() -> int:
    syslog.openlog(LOGGER_TAG, syslog.LOG_PID, syslog.LOG_DAEMON)

    proc = make_journal_proc()
    assert proc.stdout is not None

    batch = Batch()
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)

    stop = False

    def handle_signal(_signum, _frame) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while not stop:
            events = selector.select(timeout=1.0)

            if events:
                line = proc.stdout.readline()
                if line == "":
                    if proc.poll() is not None:
                        raise RuntimeError(f"journalctl exited with code {proc.returncode}")
                    continue

                parsed = parse_journal_line(line)
                if parsed is None:
                    flush_batch_if_due(batch, current_time_usec())
                    continue

                ts_usec, message = parsed

                if matches_io_error(message):
                    flush_batch_if_due(batch, ts_usec)

                    device = extract_device(message)
                    batch.add(ts_usec, device)
                    log_error_line(message)

            flush_batch_if_due(batch, current_time_usec())

    finally:
        try:
            selector.unregister(proc.stdout)
        except Exception:
            pass

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

        syslog.closelog()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        syslog.openlog(LOGGER_TAG, syslog.LOG_PID, syslog.LOG_DAEMON)
        syslog.syslog(syslog.LOG_ERR, f"Unhandled exception: {exc}")
        syslog.closelog()
        raise
