# ps5-remoteplay

Python asyncio port of the PS5 Remote Play protocol: wake, standby, status and pairing. Published on PyPI as `ps5-remoteplay` (AGPL-3.0-only), GitHub `iharosi/ps5-remoteplay`. Python >= 3.12, dependencies `aiohttp` and `cryptography`.

## Related repos

- `~/Sites/other/playactor`: TypeScript reference implementation. Run `DEBUG=playactor:* node dist/cli/index.js ...` there to compare behaviour.
- `~/Sites/other/ha-ps5-remoteplay`: Home Assistant integration. It pins an exact version of this library in `manifest.json`.

## Commands

```sh
source .venv/bin/activate
pip install -e '.[dev]'      # editable install, like npm link
pytest -q
ps5-remoteplay status [--host IP]          # exit 0 awake, 1 standby, 2 not found
ps5-remoteplay wake --host IP
ps5-remoteplay standby --host IP [--passcode 1234]
ps5-remoteplay pair --host IP [--no-browser]
# add --debug for protocol logs; --credentials ~/.config/playactor/credentials.json reuses a playactor pairing
```

- Default credentials file: `~/.config/ps5-remoteplay/credentials.json`, mode 0600.
- `test-credentials.json` and `*.pcap` are gitignored. Never commit them.

## Layout (`src/ps5_remoteplay/`)

- `session.py`: init and control connections, login, standby
- `registration.py`, `oauth.py`: pairing
- `discovery.py`, `wake.py`: UDP discovery and wake
- `crypto.py`, `keys.py`, `http.py`, `credentials.py`, `errors.py`, `const.py`, `__main__.py` (CLI)
- Tests: `tests/`, with `vectors.json` generated from the TypeScript code

## Release routine

1. Bump `version` in `pyproject.toml`, commit, and create the `vX.Y.Z` git tag.
2. Build and upload manually with `twine`. The project-scoped PyPI token is in the macOS Keychain.
3. Verify with a real install: `pip install --no-cache-dir ps5-remoteplay==X.Y.Z`. PyPI's JSON API shows a new version before the pip index does.
4. Only then release an integration version that pins it.

Versions so far are git tags, not GitHub releases, so `publish.yml` (trusted publishing on GitHub release) has never run. It needs a PyPI trusted publisher first: owner `iharosi`, repo `ps5-remoteplay`, workflow `publish.yml`, environment `pypi`.

**GitHub Actions is disabled on this repo.** It was turned off during an account billing lock that has since been fixed. Re-enabling it is the user's decision: `gh api -X PUT repos/iharosi/ps5-remoteplay/actions/permissions -F enabled=true`.

## Console behaviour (learned from packet captures)

- The console ties sessions to the **pairing**, not the host. A pairing that's stuck fails from every machine, while a second pairing still works from the same one.
- A failed attempt keeps that pairing's session held for 1–2 minutes. Retries during that window get `403 80108b10` ("already in use"), and fast retries extend it. **No automatic retries.**
- The console refuses in 3 ways: `403` with a reason code, an immediate TCP reset, or no answer at all (timeout).
- **Root cause of the Home Assistant standby failures (fixed in 0.1.8):** the control connection must open only after the init connection has fully closed **and a short pause has passed**. Home Assistant's event loop reconnected 4 µs after teardown and failed; a Linux CLI took 1.2 ms and worked. The fix awaits the close, then sleeps `CTRL_CONNECT_DELAY` (200 ms). A test fails if that pause is removed.
- A session closed politely stays reserved on the console. Setting `SO_LINGER` to zero before abort sends a real RST.
- The all-zero `RP-Did` device id is **not** a cause. It was randomised once on mixed-up evidence, then reverted.
- The console can stop answering discovery for 10–20 s while it goes into or out of standby.
- There's no push signal for power-on. The mDNS announcements are identical in rest mode, so polling is the only way to detect it.
- Before trusting a failed test, check whether an earlier attempt left the console stuck. Last-resort reset: `Settings > System > Remote Play > Unlink All Devices`, then a full restart.

Local environment details (console, network, working preferences) are in `CLAUDE.local.md`, which is not committed.
