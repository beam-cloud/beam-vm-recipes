import base64
import contextlib
import importlib.machinery
import importlib.util
import json
import io
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RECIPE = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, str(RECIPE / filename))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


tunnel = load_module("beam_tunnel", "beam-tunnel")
configure = load_module("configure_tunnel", "configure.py")
beam_cli = load_module("beam_cli_test", "beam_cli.py")
PUBLIC_KEY = "ssh-ed25519 " + base64.b64encode(
    b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + bytes(range(32))
).decode()


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.config_path = self.directory / "config.json"
        self.identity = self.directory / "identity"
        self.identity.write_text("test identity placeholder")
        (self.directory / "known_hosts").write_text(f"[2001:db8::1]:2222 {PUBLIC_KEY}\n")
        self.config = {
            "ipv6": "2001:db8::1", "public_url": "https://relay.example.invalid/",
            "identity_file": "identity", "known_hosts_file": "known_hosts",
        }

    def write(self):
        self.config_path.write_text(json.dumps(self.config))

    def test_paths_resolve_relative_to_config_not_current_directory(self):
        self.write()
        tunnel.load_config(self.config_path)
        self.assertIn(str(self.identity), tunnel.SSH)
        self.assertIn(f"UserKnownHostsFile={self.directory / 'known_hosts'}", tunnel.SSH)

    def test_missing_pinned_hosts_fails_before_connecting(self):
        self.write()
        (self.directory / "known_hosts").unlink()
        with self.assertRaisesRegex(tunnel.TunnelError, "Verified known_hosts"):
            tunnel.load_config(self.config_path)

    def test_rejects_ipv4_or_shell_text_as_ipv6(self):
        for address in ("127.0.0.1", "::1; touch /tmp/unwanted", None):
            self.config["ipv6"] = address
            self.write()
            with self.assertRaises(tunnel.TunnelError):
                tunnel.load_config(self.config_path)

    def test_rejects_invalid_remote_ports(self):
        for value in (0, 65536, "9000", True):
            self.config["remote_port"] = value
            self.write()
            with self.assertRaisesRegex(tunnel.TunnelError, "remote_port"):
                tunnel.load_config(self.config_path)

    def test_rejects_credential_bearing_public_urls(self):
        for url in ("https://relay.example.invalid/?token=secret", "https://user:pass@relay.example.invalid/", "file:///tmp/sample"):
            self.config["public_url"] = url
            self.write()
            with self.assertRaises(tunnel.TunnelError):
                tunnel.load_config(self.config_path)

    def test_configure_pins_authenticated_key_without_copying_private_key(self):
        results = [
            {"public_ipv6": "2001:db8::1"},
            {"8000": "https://relay.example.invalid/"},
            {"exit_code": 0, "stdout": PUBLIC_KEY + " root@relay\n"},
        ]
        with patch.object(configure, "beam_json", side_effect=results):
            path, _ = configure.write_config("relay", self.config_path, self.identity)
        self.assertEqual((path.parent / "known_hosts").read_text(), f"[2001:db8::1]:2222 {PUBLIC_KEY}\n")
        self.assertNotIn(self.identity.read_text(), path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_configure_rejects_bad_or_ambiguous_host_keys(self):
        for value in ("ssh-ed25519 invalid!", PUBLIC_KEY + "\n" + PUBLIC_KEY, "ssh-rsa AAAA"):
            with self.assertRaises(ValueError):
                configure.verified_host_key(value)

    def test_no_config_reports_actionable_error(self):
        result = subprocess.run(
            [str(RECIPE / "beam-tunnel"), "--config", str(self.config_path), "8080"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("configure.py", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = Path(temporary.name)
        values = {
            "ACTIVE_FORWARD": state / "forward.json", "CONTROL": state / "ssh-control",
            "STOP_REQUEST": state / "stop-request", "PUBLIC_URL": "https://relay.example.invalid/",
            "DIRECT_URL": "http://[2001:db8::1]:8000/",
        }
        for name, value in values.items():
            patcher = patch.object(tunnel, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_ctrl_c_reaps_owned_child_and_reports_clean_shutdown(self):
        # Use a real child process and a real listening local socket, while
        # substituting the remote SSH handshake so CI needs no credentials.
        children = []
        real_popen = subprocess.Popen

        def start_child(_command, **options):
            child = real_popen(["sleep", "30"], **options)
            children.append(child)
            return child

        class Display(tunnel.Display):
            def status(self, text):
                if text.startswith("Tunnel open"):
                    raise KeyboardInterrupt

        with socket.socket() as local:
            local.bind(("127.0.0.1", 0))
            local.listen()
            with patch.object(tunnel, "master_running", side_effect=[False, True]), \
                 patch.object(tunnel.subprocess, "Popen", side_effect=start_child), \
                 patch.object(tunnel, "control_command", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                 patch.object(tunnel, "flush_master"), \
                 patch.object(tunnel, "stop_requested", return_value=False):
                tunnel.TARGET = "root@2001:db8::1"
                tunnel.SSH = ["ssh"]
                tunnel.PUBLIC_URL = "https://relay.example.invalid/"
                tunnel.DIRECT_URL = "http://[2001:db8::1]:8000/"
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    tunnel.run_tunnel(local.getsockname()[1], 9000, Display(), "ipv6")
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].poll(), "The tunnel child was left running")
        self.assertIn("Public forwarding is closed.", output.getvalue())

    def test_auto_gateway_fallback_reaps_both_children_on_ctrl_c(self):
        children = []
        commands = []
        real_popen = subprocess.Popen

        def start_child(command, **options):
            commands.append(command)
            child = real_popen(["sleep", "30"], **options)
            children.append(child)
            return child

        def start_bridge(log):
            return start_child(["beam", "vm", "port-forward"], stdout=log), 43210

        class Display(tunnel.Display):
            def status(self, text):
                if text.startswith("Tunnel open"):
                    raise KeyboardInterrupt

        with socket.socket() as local:
            local.bind(("127.0.0.1", 0))
            local.listen()
            with patch.object(tunnel, "master_running", side_effect=[False, True]), \
                 patch.object(tunnel, "ipv6_reachable", return_value=False), \
                 patch.object(tunnel, "start_beam_bridge", side_effect=start_bridge), \
                 patch.object(tunnel, "wait_for_bridge"), \
                 patch.object(tunnel.subprocess, "Popen", side_effect=start_child), \
                 patch.object(tunnel, "control_command", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                 patch.object(tunnel, "flush_master"), \
                 patch.object(tunnel, "stop_requested", return_value=False), \
                 patch.object(tunnel, "TARGET", "root@2001:db8::1"), \
                 patch.object(tunnel, "SSH", ["ssh", "-6", "-p", "2222", "-o", "HostKeyAlias=[2001:db8::1]:2222"]):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    tunnel.run_tunnel(local.getsockname()[1], 9000, Display())
        self.assertEqual(len(children), 2)
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertIn("Beam gateway (HTTPS)", output.getvalue())
        self.assertIn("-4", commands[1])
        self.assertIn("root@127.0.0.1", commands[1])
        self.assertIn("HostKeyAlias=[2001:db8::1]:2222", commands[1])

    def test_refused_reverse_listener_never_reports_connected(self):
        children = []
        real_popen = subprocess.Popen

        def start_child(_command, **options):
            child = real_popen(["sleep", "30"], **options)
            children.append(child)
            return child

        replies = [
            subprocess.CompletedProcess([], 1, "", "remote port forwarding failed"),
            subprocess.CompletedProcess([], 0, "", ""),
        ]
        with socket.socket() as local:
            local.bind(("127.0.0.1", 0))
            local.listen()
            with patch.object(tunnel, "master_running", side_effect=[False, True]), \
                 patch.object(tunnel.subprocess, "Popen", side_effect=start_child), \
                 patch.object(tunnel, "control_command", side_effect=replies), \
                 patch.object(tunnel, "stop_requested", return_value=False), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaisesRegex(tunnel.TunnelError, "remote port forwarding failed"):
                    tunnel.run_tunnel(local.getsockname()[1], 9000, tunnel.Display(), "ipv6")
        self.assertNotIn("● connected", output.getvalue())
        self.assertIsNotNone(children[0].poll())


class BeamExecutableTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.old = self.folder / "pyenv" / "beam"
        self.new = self.folder / "installed" / "beam"
        for file, code in ((self.old, 2), (self.new, 0)):
            file.parent.mkdir()
            file.write_text(f"#!/bin/sh\nexit {code}\n")
            file.chmod(0o755)

    def test_skips_legacy_sdk_first_on_python_path(self):
        with patch.dict(os.environ, {"PATH": f"{self.old.parent}:{self.new.parent}", "BEAM_CLI": ""}):
            self.assertEqual(beam_cli.find_beam_cli(), str(self.new))

    def test_explicit_legacy_executable_reports_vm_support_problem(self):
        with patch.dict(os.environ, {"BEAM_CLI": ""}):
            with self.assertRaisesRegex(ValueError, "does not support VM commands"):
                beam_cli.find_beam_cli(str(self.old))


if __name__ == "__main__":
    unittest.main()
