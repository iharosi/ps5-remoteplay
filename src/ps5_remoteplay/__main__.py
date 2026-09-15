import argparse
import asyncio
import json
import logging
import os
import sys
import webbrowser
from pathlib import Path

import aiohttp

from . import (
    Credentials,
    DeviceInfo,
    DeviceNotFound,
    DeviceStatus,
    PS5Error,
    account_id_from_redirect,
    discover,
    get_device,
    login_url,
    register,
    standby,
    wait_for_status,
    wake,
)

EXIT_AWAKE = 0
EXIT_STANDBY = 1
EXIT_NOT_FOUND = 2
EXIT_ERROR = 3

DEFAULT_CREDENTIALS = Path.home() / ".config" / "ps5-remoteplay" / "credentials.json"


def _load_store(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def _save_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(store, f, indent=2)


def _credentials_for(args: argparse.Namespace, device: DeviceInfo) -> Credentials:
    entry = _load_store(args.credentials).get(device.id)
    if not entry:
        raise PS5Error(f"No credentials for {device.name} ({device.id}) in {args.credentials}; run 'pair' first")
    return Credentials.from_dict(entry)


def _describe(device: DeviceInfo) -> str:
    return f"{device.name} ({device.id}) at {device.host}: {device.status}"


async def _status(args: argparse.Namespace) -> int:
    if args.host:
        device = await get_device(args.host, timeout=args.timeout)
        print(_describe(device))
        return EXIT_AWAKE if device.status == DeviceStatus.AWAKE else EXIT_STANDBY

    devices = await discover(timeout=args.timeout)
    for device in devices:
        print(_describe(device))
    return EXIT_AWAKE if devices else EXIT_NOT_FOUND


async def _wake(args: argparse.Namespace) -> int:
    device = await get_device(args.host)
    if device.status == DeviceStatus.AWAKE:
        print(f"{device.name} is already awake")
        return EXIT_AWAKE

    await wake(args.host, _credentials_for(args, device))
    print(f"Sent WAKEUP to {device.name}")
    if not args.no_wait:
        await wait_for_status(args.host, DeviceStatus.AWAKE, timeout=args.timeout)
        print(f"{device.name} is awake")
    return EXIT_AWAKE


async def _pair(args: argparse.Namespace) -> int:
    device = await get_device(args.host)
    if device.status != DeviceStatus.AWAKE:
        raise PS5Error("The PS5 must be awake to pair; turn it on first")

    url = login_url()
    print("1. Sign in to PSN in your browser. When the page shows \"redirect\", copy the full URL from the address bar.")
    print(f"   {url}")
    if not args.no_browser:
        webbrowser.open(url)
    redirect = await asyncio.to_thread(input, "Redirect URL> ")
    account_id = await account_id_from_redirect(redirect)

    print("2. On the PS5, open Settings > System > Remote Play > Link Device.")
    pin = await asyncio.to_thread(input, "PIN shown on the PS5> ")
    credentials = await register(args.host, account_id, pin)

    store = _load_store(args.credentials)
    store[device.id] = credentials.to_dict()
    _save_store(args.credentials, store)
    print(f"Paired with {device.name}; credentials saved to {args.credentials}")
    return EXIT_AWAKE


async def _standby(args: argparse.Namespace) -> int:
    device = await get_device(args.host)
    changed = await standby(args.host, _credentials_for(args, device), passcode=args.passcode)
    print(f"{device.name} {'is going into standby' if changed else 'is already in standby'}")
    return EXIT_STANDBY


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--debug", action="store_true", help="log protocol details")
    common.add_argument(
        "--credentials", type=Path, default=DEFAULT_CREDENTIALS,
        help=f"credentials file (default {DEFAULT_CREDENTIALS}); playactor's file also works",
    )

    parser = argparse.ArgumentParser(prog="ps5-remoteplay")
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status", parents=[common], help="show status; exit 0 awake, 1 standby, 2 not found")
    status.add_argument("--host", help="console IP; omit to broadcast")
    status.add_argument("--timeout", type=float, default=3.0)
    status.set_defaults(handler=_status)

    wake_cmd = commands.add_parser("wake", parents=[common], help="wake a paired console")
    wake_cmd.add_argument("--host", required=True)
    wake_cmd.add_argument("--no-wait", action="store_true")
    wake_cmd.add_argument("--timeout", type=float, default=30.0)
    wake_cmd.set_defaults(handler=_wake)

    pair = commands.add_parser("pair", parents=[common], help="pair via PSN login and the Link Device PIN")
    pair.add_argument("--host", required=True)
    pair.add_argument("--no-browser", action="store_true")
    pair.set_defaults(handler=_pair)

    standby_cmd = commands.add_parser("standby", parents=[common], help="put a paired console into standby")
    standby_cmd.add_argument("--host", required=True)
    standby_cmd.add_argument("--passcode", help="4-digit PS5 user passcode, if set")
    standby_cmd.set_defaults(handler=_standby)
    return parser


def main() -> None:
    args = _parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)
    try:
        sys.exit(asyncio.run(args.handler(args)))
    except DeviceNotFound as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(EXIT_NOT_FOUND)
    except (PS5Error, ValueError, OSError, aiohttp.ClientError) as e:
        print(f"Error: {e or type(e).__name__}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
