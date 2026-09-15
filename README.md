# ps5-remoteplay

Asyncio Python library for PlayStation 5 consoles on the local network:
discover and read status, wake, put into standby, and pair via PSN OAuth + Remote Play PIN.

Ported from [playactor](https://github.com/dhleong/playactor) (PS5 code path only).

## Install

```bash
pip install ps5-remoteplay
```

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

## CLI

```bash
ps5-remoteplay status [--host IP]          # exit 0 awake, 1 standby, 2 not found
ps5-remoteplay pair --host IP              # PS5 must be awake
ps5-remoteplay wake --host IP [--no-wait]
ps5-remoteplay standby --host IP [--passcode 1234]
```

Credentials are stored in `~/.config/ps5-remoteplay/credentials.json` (mode 0600), in the
same format as playactor's `~/.config/playactor/credentials.json`; pass
`--credentials ~/.config/playactor/credentials.json` to reuse an existing playactor pairing.

Pairing binds UDP port 9295 locally, and broadcast discovery needs the host to be on the same
subnet as the console.

## Library

```python
from ps5_remoteplay import Credentials, get_device, wake, standby, DeviceStatus

device = await get_device("192.168.1.50")
if device.status == DeviceStatus.STANDBY:
    await wake(device.host, creds)
else:
    await standby(device.host, creds)
```

## Testing

`tests/vectors.json` holds byte-exact reference outputs generated from playactor's TypeScript
implementation (crypto, registration payloads, session headers and frames, wake packets).

## License and credits

AGPL-3.0-only. See `LICENSE`.

The protocol implementation is ported from [playactor](https://github.com/dhleong/playactor)
by Daniel Leong, whose Remote Play crypto is in turn based on the work of the
[chiaki](https://git.sr.ht/~thestr4ng3r/chiaki) project.
