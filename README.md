# Beam VM recipes

Small, runnable recipes for [Beam persistent VMs](https://platform.beam.cloud/get-started) and their persistent public IPv6 addresses. Each recipe includes the software, setup steps, a real demo, and cleanup commands.

## Recipes

| Recipe | What you get |
| --- | --- |
| [Localhost tunnel](recipes/tunnel/) | Share a local app through a Beam VM. Public HTTPS, direct IPv6, an interactive terminal, and Ctrl+C to stop. |

Start with the [tunnel recipe](recipes/tunnel/README.md). It uses Python's standard library, OpenSSH, and Nginx; no third-party Python packages are required.

```sh
git clone https://github.com/beam-cloud/beam-vm-recipes.git
cd beam-vm-recipes
```

## What persistence means

The VM keeps its public IPv6 address across stop/start and pause/resume. Its writable disk also persists, so installed services and configuration remain available when it starts again. Connections are interrupted while the VM is stopped; clients reconnect to the same address afterward.

These recipes use published TCP ports. The tunnel recipe requires an IPv6-capable connection from your computer to the VM; visitors can use the public HTTPS URL without a direct IPv6 connection.

VMs keep running and billing until stopped. Each recipe documents how to stop its processes and the VM.

## Adding a recipe

Keep each recipe in its own folder under `recipes/`. Include setup, a working example, validation, and cleanup. Use [the recipe template](docs/recipe-template.md) when adding one. Add an entry to the table once the recipe is ready to run.

Do not commit local configuration, SSH identities, tokens, generated host-key files, or runtime state.

## Checks

```sh
python3 -m unittest discover -s recipes/tunnel/tests -v
bash -n recipes/tunnel/setup-relay.sh
```

MIT licensed. See [LICENSE](LICENSE).
