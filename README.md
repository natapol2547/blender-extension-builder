# Blender Extension Builder

A GitHub Action that turns any Blender add-on repository into release-ready extension builds:

1. Downloads a Blender release (latest by default) from [download.blender.org](https://download.blender.org/release/) — cached between runs.
2. Builds your add-on with the official `blender --command extension build`.
3. Validates the built `.zip` with a bundled, dependency-free validator distilled from the
   [blender-extension-validator](../blender-extension-validator) project — 70+ static checks
   (manifest rules, license policy, AST-level Python checks, packaging hygiene) plus the official
   `blender --command extension validate`.
4. If validation passes, creates a **draft GitHub release** with the `.zip` attached, tagged with
   the add-on's own version from `blender_manifest.toml`.

Requires a **Linux runner** (`ubuntu-latest`).

## Usage

```yaml
name: Build extension

on:
  push:
    branches: [main]

permissions:
  contents: write # needed to create the draft release

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: natapol2547/blender-extension-builder@main
        with:
          source-dir: "." # directory containing blender_manifest.toml
```

Pushing a manifest with `version = "1.2.3"` produces a draft release tagged `v1.2.3` with
`your_addon-1.2.3.zip` attached. Review the draft and publish it when ready. The action fails
(and creates no release) if the tag already exists — bump the manifest version instead.

## Inputs

| Input                 | Default               | Description                                                                |
| --------------------- | --------------------- | -------------------------------------------------------------------------- |
| `source-dir`          | `.`                   | Add-on root (the directory containing `blender_manifest.toml`).            |
| `output-dir`          | _(temp dir)_          | Where the built `.zip` is written.                                         |
| `blender-version`     | `latest`              | `latest` or an explicit release like `4.5.3`.                              |
| `validate`            | `true`                | Run the bundled validator on the built `.zip`; failure blocks the release. |
| `strict`              | `false`               | Treat validator WARN findings as failures too.                             |
| `create-release`      | `true`                | Create a draft GitHub release tagged with the add-on version.              |
| `tag-prefix`          | `v`                   | Release tag = prefix + manifest version (e.g. `v1.2.3`).                   |
| `github-token`        | `${{ github.token }}` | Token used for the release. Needs `contents: write`.                       |
| `upload-artifact`     | `true`                | Also upload the `.zip` + validation report as a workflow artifact.         |
| `install-system-deps` | `true`                | `apt-get install` the X11/GL libraries headless Blender needs.             |

## Outputs

| Output                       | Description                                                    |
| ---------------------------- | -------------------------------------------------------------- |
| `zip-path`                   | Path to the built extension `.zip`.                            |
| `addon-id` / `addon-version` | From `blender_manifest.toml`.                                  |
| `blender-version`            | The Blender release actually used.                             |
| `release-url`                | URL of the draft release (empty when `create-release: false`). |

## What the validator checks

Severity is **ERROR** (fails the build) or **WARN** (advisory; failing with `strict: true`):

- **Manifest** — required fields, `id`/`version`/`tagline` formatting, license policy
  (add-ons must be GPL-compatible), allowed tags, permissions each with a reason.
- **Python (AST)** — no `eval`/`exec`, no runtime `pip` installs, no writes into the install
  directory, no `sys.path` manipulation, no threading, network access guarded by
  `bpy.app.online_access`, and more.
- **Packaging** — no `__pycache__`/compiled files, no VCS dirs, no oversized assets.
- **Runtime** — the official `blender --command extension validate` on the built zip.

Skip everything with `validate: 'false'` (the build + release still run), or keep validation
but soften nothing with the default non-strict mode.

## Examples

Pin a Blender version and validate strictly:

```yaml
- uses: natapol2547/blender-extension-builder@main
  with:
    blender-version: "4.5.3"
    strict: "true"
```

Build only (no validation, no release) — e.g. for pull requests:

```yaml
- uses: natapol2547/blender-extension-builder@main
  with:
    validate: "false"
    create-release: "false"
```

## Repository layout

```
action.yml            # the composite action
scripts/              # release resolver, Blender downloader, manifest reader
validator/            # vendored stdlib-only validator (main.py + blender_validator/)
test-addon/           # minimal fixture add-on used by CI (passes strict validation)
.github/workflows/    # CI: runs the action on the fixture
```
