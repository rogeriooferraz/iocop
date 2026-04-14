# IOCOP - I/O monitoring and notification service

IOCOP is a systemd service that monitors storage failures reported by the kernel and
sends desktop notifications to active local graphical sessions.

It is supported on Ubuntu 24.04 Desktop.

## IOCOP notifications

Notifications may appear as popup banners depending on their urgency and remain
accessible later from the notification list. When Do Not Disturb is enabled,
normal-priority notifications may be suppressed as banners, but they will still
be available in the notification list.

IOCOP throttles storage failure notifications so that no more than one
notification is sent every 5 seconds.

When multiple storage failures occur during the same 5-second interval, IOCOP
sends a single plain-text summary notification. Failures are grouped by device,
and the summary table shows the failure count first and the device name second.

Each summary notification also includes:

- the exact first and last journal timestamps for the batch
- a localized `journalctl` command that the user can run to inspect the matching
  kernel messages for that exact interval

The first matching kernel failure starts a 5-second batch. All matching failures
in that interval are counted per device. At the end of the batch, IOCOP sends
one summary notification for the interval.

The journal handler source file is `iocop_journal_handler.py`, and it is
installed as the command `/usr/local/sbin/iocop-journal-handler`.

## Dependencies

Install the notification and test tools:

```bash
sudo apt update
sudo apt install libnotify-bin python3-pytest
```

## Install

Clone this repo into a temporary folder and install the IOCOP service.

```bash
tmpdir="$(mktemp -d)"

git clone https://github.com/rogeriooferraz/iocop.git "$tmpdir/iocop"
cd "$tmpdir/iocop"

for f in \
  /usr/local/sbin/iocop-journal-handler \
  /usr/local/sbin/iocop-notify-handler \
  /etc/systemd/system/iocop.service \
  /usr/local/share/icons/hicolor/scalable/apps/iocop.svg \
  /usr/local/share/icons/hicolor/48x48/apps/iocop.png
do
  [ -e "$f" ] && echo "WARNING: existing file will be overwritten: $f"
done

sudo install -m 0755 iocop_journal_handler.py /usr/local/sbin/iocop-journal-handler
sudo install -m 0755 iocop-notify-handler  /usr/local/sbin/iocop-notify-handler
sudo install -m 0644 iocop.service         /etc/systemd/system/iocop.service

sudo install -d -m 0755 /usr/local/share/icons/hicolor/scalable/apps
sudo install -m 0644 assets/iocop.svg /usr/local/share/icons/hicolor/scalable/apps/iocop.svg
sudo install -d -m 0755 /usr/local/share/icons/hicolor/48x48/apps
sudo install -m 0644 assets/iocop-48x48.png /usr/local/share/icons/hicolor/48x48/apps/iocop.png

sudo systemctl daemon-reload
sudo systemctl enable iocop.service
sudo systemctl restart iocop.service

echo "Installation complete."
echo "Service enabled and restarted: iocop.service"

cd -
```

## Uninstall

Remove the IOCOP service and its support scripts.

```bash
for f in \
  /usr/local/sbin/iocop-journal-handler \
  /usr/local/sbin/iocop-notify-handler \
  /etc/systemd/system/iocop.service
do
  [ -e "$f" ] && echo "Removing: $f"
done

sudo systemctl disable --now iocop.service 2>/dev/null || true

sudo rm -f /usr/local/sbin/iocop-journal-handler
sudo rm -f /usr/local/sbin/iocop-notify-handler
sudo rm -f /etc/systemd/system/iocop.service

sudo rm -f /usr/local/share/icons/hicolor/scalable/apps/iocop.svg
sudo rm -f /usr/local/share/icons/hicolor/48x48/apps/iocop.png

sudo systemctl daemon-reload
sudo systemctl reset-failed

echo "Uninstall complete."
echo "Service removed: iocop.service"
```

## Testing

Test desktop notification sending:

```bash
notify-send \
  -u normal \
  -i /usr/local/share/icons/hicolor/scalable/apps/iocop.svg \
  -h string:image-path:file:///usr/local/share/icons/hicolor/scalable/apps/iocop.svg "Disk monitor test" \
  "A normal notification with SVG icons. It does not show up when Do Not Disturb is on"

notify-send \
  -u normal \
  -i /usr/local/share/icons/hicolor/48x48/apps/iocop.png \
  -h string:image-path:file:///usr/local/share/icons/hicolor/48x48/apps/iocop.png "Disk monitor test" \
  "A normal notification with PNG icons. It does not show up when Do Not Disturb is on"

notify-send \
  -u critical \
  -i /usr/local/share/icons/hicolor/48x48/apps/iocop.png \
  -h string:image-path:file:///usr/local/share/icons/hicolor/scalable/apps/iocop.svg "Disk monitor test" \
  "A critical notification always shows up, also when Do Not Disturb is on"

sudo /usr/local/sbin/iocop-notify-handler \
  "IOCOP Warning" \
  "Sent by iocop-notify-handler" \
  critical
```

Run the automated test suite:

```bash
pytest -q
```
