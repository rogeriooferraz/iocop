import iocop_journal_handler as mod


def test_matches_io_error():
    assert mod.matches_io_error("blk_update_request: I/O error, dev sda, sector 123")
    assert mod.matches_io_error("Buffer I/O error on dev dm-0, logical block 1")
    assert not mod.matches_io_error("usb 1-1: new high-speed USB device")


def test_extract_device():
    assert mod.extract_device("Buffer I/O error on dev dm-0, logical block 123") == "dm-0"
    assert mod.extract_device("blk_update_request: I/O error, dev sda, sector 42") == "sda"
    assert mod.extract_device("I/O error, dev nvme0n1, sector 7 op 0x0:(READ)") == "nvme0n1"
    assert mod.extract_device("critical medium error, something else") == "unknown"


def test_parse_journal_line_valid():
    raw = (
        '{"__REALTIME_TIMESTAMP":"1713090000123456",'
        '"MESSAGE":"blk_update_request: I/O error, dev sda, sector 42"}'
    )
    parsed = mod.parse_journal_line(raw)
    assert parsed == (1713090000123456, "blk_update_request: I/O error, dev sda, sector 42")


def test_parse_journal_line_replaces_newlines_and_tabs():
    raw = '{"__REALTIME_TIMESTAMP":"1713090000123456","MESSAGE":"line1\\nline2\\tline3"}'
    parsed = mod.parse_journal_line(raw)
    assert parsed == (1713090000123456, "line1 line2 line3")


def test_parse_journal_line_invalid_json():
    assert mod.parse_journal_line("{not json") is None


def test_parse_journal_line_missing_fields():
    assert mod.parse_journal_line('{"MESSAGE":"hello"}') is None
    assert mod.parse_journal_line('{"__REALTIME_TIMESTAMP":"123"}') is None


def test_compute_table_widths():
    fail_count = {"sda": 12, "nvme0n1": 3}
    count_width, device_width = mod.compute_table_widths(fail_count)

    assert count_width >= len("FAILURES")
    assert count_width >= len("12")
    assert device_width >= len("DEVICE")
    assert device_width >= len("nvme0n1")


def test_build_table_rows():
    rows = [("nvme0n1", 3), ("sda", 12)]
    result = mod.build_table_rows(rows, count_width=8, device_width=8)

    assert result.splitlines() == [
        "       3  nvme0n1 ",
        "      12  sda     ",
    ]


def test_build_detail_command(monkeypatch):
    monkeypatch.setattr(mod, "format_journal_ts", lambda ts: f"TS{ts}")

    command = mod.build_detail_command(111, 222)

    assert command == (
        "sudo journalctl -k -o short-iso-precise "
        "--since TS111 --until TS222 "
        "'--grep=Buffer I/O error|blk_update_request: I/O error|"
        "end_request: I/O error|I/O error, dev |critical medium error' "
        "--no-pager"
    )


def test_build_notification_body(monkeypatch):
    monkeypatch.setattr(mod, "format_journal_ts", lambda ts: f"TS{ts}")
    monkeypatch.setattr(mod, "build_detail_command", lambda first, last: f"CMD {first} {last}")

    batch = mod.Batch()
    batch.add(100, "sda")
    batch.add(200, "sda")
    batch.add(300, "nvme0n1")

    body = mod.build_notification_body(batch)

    expected = "\n".join([
        "Disk I/O failures in this 5s batch:",
        "",
        "FAILURES  DEVICE ",
        "--------  -------",
        "       1  nvme0n1",
        "       2  sda    ",
        "--------  -------",
        "       3  TOTAL  ",
        "",
        "First failure: TS100",
        "Last failure:  TS300",
        "",
        "For more detail, run:",
        "CMD 100 300",
    ])

    assert body == expected


def test_batch_due():
    batch = mod.Batch()
    batch.add(1_000_000, "sda")

    assert not batch.due(1_000_000 + mod.WINDOW_USEC - 1)
    assert batch.due(1_000_000 + mod.WINDOW_USEC)


def test_batch_reset():
    batch = mod.Batch()
    batch.add(1_000_000, "sda")
    batch.add(2_000_000, "nvme0n1")

    batch.reset()

    assert batch.pending is False
    assert batch.batch_start_usec == 0
    assert batch.first_ts_usec == 0
    assert batch.last_ts_usec == 0
    assert dict(batch.fail_count) == {}
