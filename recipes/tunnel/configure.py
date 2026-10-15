#!/usr/bin/env python3
"""Generate a local tunnel config and pin the VM host key through Beam."""

import argparse
import base64
import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from beam_cli import find_beam_cli


DEFAULT_CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "beam-tunnel" / "config.json"


def beam_json(operation, vm, context=None, tail=(), beam_cli=None):
    command = [beam_cli or find_beam_cli(), "vm", operation, "--json"]
    if context:
        command += ["--context", context]
    command += [vm, *tail]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"beam vm {operation} failed.")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise ValueError(f"beam vm {operation} did not return JSON. Check your Beam CLI version.")


def verified_host_key(stdout):
    keys = [line.split() for line in stdout.splitlines() if line.startswith("ssh-ed25519 ")]
    if len(keys) != 1 or len(keys[0]) < 2:
        raise ValueError("The authenticated VM command did not return one Ed25519 public host key.")
    algorithm, value = keys[0][:2]
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError("The VM public host key is not valid base64.")
    if len(decoded) != 51 or decoded[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20":
        raise ValueError("The VM public host key is not a valid Ed25519 SSH key.")
    return f"{algorithm} {value}"


def write_config(vm, path, identity, context=None, beam_cli=None):
    info = beam_json("get", vm, context, beam_cli=beam_cli)
    try:
        ipv6 = str(ipaddress.IPv6Address(info["public_ipv6"]))
    except (KeyError, ValueError, TypeError):
        raise ValueError("The VM has no public IPv6 address. Wait for it to become ready.")
    ports = beam_json("ports", vm, context, beam_cli=beam_cli)
    public_url = ports.get("8000")
    if not isinstance(public_url, str):
        raise ValueError(f"Publish the relay first: beam vm expose --public {vm} 8000")
    url = urlsplit(public_url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("The relay URL must be stable and contain no access token. Publish port 8000 as public.")
    result = beam_json("exec", vm, context, ["cat", "/etc/ssh/ssh_host_ed25519_key.pub"], beam_cli=beam_cli)
    if result.get("exit_code") != 0:
        raise ValueError("Could not read the VM public SSH host key through Beam.")
    key = verified_host_key(result.get("stdout", ""))
    identity = identity.expanduser().resolve()
    if not identity.is_file():
        raise ValueError(f"SSH identity does not exist: {identity}")
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    hosts = path.with_name("known_hosts")
    hosts.write_text(f"[{ipv6}]:2222 {key}\n")
    hosts.chmod(0o600)
    config = {
        "vm_name": vm,
        "beam_cli": beam_cli,
        "beam_context": context,
        "transport": "auto",
        "ipv6": ipv6,
        "public_url": public_url,
        "ssh_user": "root",
        "ssh_port": 2222,
        "identity_file": str(identity),
        "known_hosts_file": str(hosts),
        "listen_port": 8000,
        "remote_port": 9000,
    }
    path.write_text(json.dumps(config, indent=2) + "\n")
    path.chmod(0o600)
    return path, public_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vm", help="Prepared Beam relay VM name")
    parser.add_argument("--context", help="Beam CLI context")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Where to write config.json")
    parser.add_argument("--identity", type=Path, default=Path("~/.beam/ssh/vm_ed25519"), help="Local private SSH identity path")
    parser.add_argument("--beam-cli", help="Beam executable override (also configurable with BEAM_CLI)")
    args = parser.parse_args()
    try:
        beam_cli = find_beam_cli(args.beam_cli)
        path, url = write_config(args.vm, args.config, args.identity, args.context, beam_cli)
    except (ValueError, OSError, TypeError) as error:
        print(f"Configuration failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Configuration cancelled.", file=sys.stderr)
        return 130
    print(f"Configuration: {path}")
    print(f"Beam CLI: {beam_cli}")
    print("VM host key pinned through authenticated Beam exec.")
    print(f"Public URL: {url}")
    print("Start your local app, then run recipes/tunnel/beam-tunnel LOCAL_PORT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
