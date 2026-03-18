---
name: version-release
description: "Analyze git commits since the last tag, determine the appropriate semver bump (patch/minor/major), update version numbers in pyproject.toml and Cargo.toml, create a git tag, and push to trigger the PyPI release workflow. Use when the user says 'release', 'bump version', 'publish', 'push a new version', 'tag and push', 'prepare release', or 'version bump'. Also use when asked to determine what version number to use based on recent changes."
---

# Version & Release

Automate semver version bumps and release tagging for this Rust+Python hybrid project.

## Version Locations (ALL must be updated)

| File | Line | Format |
|------|------|--------|
| `pyproject.toml` | `version = "X.Y.Z"` | Python package version |
| `Cargo.toml` | `version = "X.Y.Z"` | Rust crate version (source of `__version__`) |

Both files MUST have identical version strings. `Cargo.toml` exports `__version__` via `env!("CARGO_PKG_VERSION")` in `src/lib.rs`.

## Workflow

### 1. Find the last release tag

```bash
git tag --list 'v*' --sort=-v:refname | head -1
```

If no tags exist, use the initial commit as the baseline.

### 2. Analyze commits since last tag

```bash
git log <last-tag>..HEAD --oneline --no-merges
```

### 3. Determine semver bump

Classify each commit by its prefix and pick the highest-priority bump:

| Commit Prefix | Bump | Examples |
|---|---|---|
| `feat:` | **minor** | New API surface, new feature, new type |
| `fix:` | **patch** | Bug fixes, corrections |
| `docs:` | **patch** | Documentation only |
| `refactor:` | **patch** | Internal restructuring, no API change |
| `perf:` | **patch** | Performance improvement |
| `test:` | **patch** | Test additions/changes |
| `chore:` | **patch** | Build, CI, dependency updates |
| `BREAKING CHANGE` in body | **major** | Only if user explicitly confirms |

**Rules:**
- Use the **highest bump** across all commits (feat > fix > docs).
- **NEVER auto-bump major.** Major bumps (X.0.0) require explicit user confirmation.
- If unsure, propose the bump and ask: "I see 3 feat commits and 2 fixes since v0.1.0. Suggesting v0.2.0 (minor). OK?"

### 4. Update version files

Edit both files with the new version. Verify with:

```bash
grep '^version' pyproject.toml Cargo.toml
```

Both must show the same new version.

### 5. Commit, tag, and push

```bash
git add pyproject.toml Cargo.toml
git commit -m "release: v<NEW_VERSION>"
git tag v<NEW_VERSION>
git push origin main
git push origin v<NEW_VERSION>
```

The tag push triggers `.github/workflows/release.yml` which builds wheels for Linux (x86_64 + aarch64), macOS ARM64, and Windows x86_64, then publishes to PyPI via trusted publishing.

### 6. Confirm to user

Report:
- Previous version → new version
- Bump type and reason (which commits drove the decision)
- Tag pushed
- Link: `https://github.com/omoios/conduit-agent-sdk/actions` to monitor the release
