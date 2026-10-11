# hatch rs

Hatch plugin for Rust builds

[![Build Status](https://github.com/python-project-templates/hatch-rs/actions/workflows/build.yaml/badge.svg?branch=main&event=push)](https://github.com/python-project-templates/hatch-rs/actions/workflows/build.yaml)
[![codecov](https://codecov.io/gh/python-project-templates/hatch-rs/branch/main/graph/badge.svg)](https://codecov.io/gh/python-project-templates/hatch-rs)
[![License](https://img.shields.io/github/license/python-project-templates/hatch-rs)](https://github.com/python-project-templates/hatch-rs)
[![PyPI](https://img.shields.io/pypi/v/hatch-rs.svg)](https://pypi.python.org/pypi/hatch-rs)

## Overview

A simple, extensible Rust build plugin for [hatch](https://hatch.pypa.io/latest/).

```toml
[tool.hatch.build.hooks.hatch-rs]
verbose = true
path = "."
module = "project"
```

### Rust artifacts and C ABI libraries

Projects can declare multiple Rust artifacts in one hook. Artifact `name` is the
Cargo output stem used for exact file discovery, and `crate-type` defaults to
`cdylib`. A destination containing `{python_extension_name}` packages that
`cdylib` as a Python extension module; other Rust artifact destinations package
the platform shared library name for standalone C ABI consumers.

```toml
[tool.hatch.build.hooks.hatch-rs]
verbose = true
path = "."
module = "project"
target-dir = "isolated"

[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "project"
manifest = "Cargo.toml"
destination = "project/{python_extension_name}"

[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "project_ffi"
manifest = "rust/Cargo.toml"
destination = "project/lib/{shared_library}"
```

Destination templates support `{module}`, `{target}`, `{profile}`, `{name}`,
`{shared_library}`, `{import_library}`, and `{python_extension_name}`.

### Third-party Cargo licenses

Configure the `licenses` table to collect dependency license texts with
[cargo-bundle-licenses](https://github.com/sstadick/cargo-bundle-licenses).
Install the collector before building; hatch-rs does not install Cargo tools:

```sh
cargo install cargo-bundle-licenses --version 4.2.0 --locked
```

```toml
[tool.hatch.build.hooks.hatch-rs]
module = "project"
path = "rust"
target-dir = "isolated"

[tool.hatch.build.hooks.hatch-rs.licenses]
destination = "{module}/licenses/{name}.json"
```

The empty `licenses` table also enables collection with this destination.
Without the table, builds do not run the collector. Each compiled artifact gets
a JSON bundle containing dependency names, versions, license identifiers, and
original license texts. `{name}` uses the artifact name, or `cargo` for the
implicit Python extension artifact. Give multiple artifacts distinct destinations.
Generated-file and skipped artifacts do not run the collector.

The hook runs the collector in the Cargo manifest directory and passes the
artifact's enabled features and environment. For `all-features` builds, it reads
the feature names from Cargo metadata. With licenses enabled, dependency-selection
options such as `--features` and `--package` must use the structured hook or
artifact fields rather than `cargo-args`. Bundles are copied into the Python package
before temporary build directories are cleaned up, then forced into the wheel.
They are also recorded when `artifact-manifest = true`. These package files do
not add Python `License-File` metadata entries.

Missing tools, failed collector commands, invalid output, or missing license
texts fail the build. A reviewed JSON bundle can supply texts the collector could
not find:

```toml
[tool.hatch.build.hooks.hatch-rs.licenses]
previous = "third-party.json"
check-previous = true
```

`previous` is relative to the hook's `path`, unless absolute. `check-previous`
also requires the newly collected bundle to be a subset of the reviewed bundle;
omit it to reuse reviewed texts without checking for dependency changes. Include
the reviewed bundle and Rust sources in the sdist when building wheels from it.
Collection itself runs only for wheels, including editable wheels.

The hook resolves each selected Cargo package through metadata and runs the
collector in that package's directory, so licenses of linked workspace members
are included. A virtual workspace manifest requires an artifact `package`
selector.

The collector's scope is conservative: version 4.2.0 always enables default
features, cannot filter by target, and collects normal dependencies rather than
build-only or dev-only dependencies. A bundle can include more crates than the
artifact uses. The collector does not accept
Cargo's `--locked` or `--frozen` flags, so prepare the dependency cache and review
the resulting lockfile when reproducibility matters. Vendored native code,
additional notice files, and libraries added during wheel repair need separate
review. Collection does not establish license compliance.

### Using the Rust debug profile for editable installs

By default rust release profile is used to compile the artifacts and it can
be changed using the `profile` option in `tool.hatch.build.hooks.hatch-rs`
or `tool.hatch.build.hooks.hatch-rs.artifacts` sections.

When installing the Python project in development mode (aka editable installs)
using the command `pip install --editable <package>`, it can be preferred
to use the rust debug profile so the compilation time is faster.

Using the Rust debug profile for editable installs can be enforced by setting the
`editable-debug` option to `true` either in `tool.hatch.build.hooks.hatch-rs`
or `tool.hatch.build.hooks.hatch-rs.artifacts` sections.

### Generated files and headers

Artifacts with `command` run an argv-list command and then validate explicit
outputs. Outputs can be packaged into the wheel, installed as wheel shared data,
or used only as required validation checks. The same `outputs` table can be used
for generated headers, either on a command artifact or on the Rust artifact whose
build produced the file.

```toml
[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "generated-package-files"
command = ["python", "scripts/write_generated_files.py"]
inputs = ["scripts/write_generated_files.py"]

[[tool.hatch.build.hooks.hatch-rs.artifacts.outputs]]
source = "project/generated/package.txt"
destination = "project/generated/package.txt"
install-scheme = "package"

[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "public-c-header"

[[tool.hatch.build.hooks.hatch-rs.artifacts.outputs]]
source = "project/include/project.h"
destination = "include/project/project.h"
install-scheme = "shared-data"
```

### ABI validation and artifact metadata

`cdylib` artifacts can validate the copied C ABI library before the wheel is
finalized. The hook can check expected exported symbols, verify headers and ABI
strings/macros, load the copied library with `ctypes.CDLL` when `validate = true`,
run project validation commands, include Windows import libraries, and emit a
package-local artifact manifest.

```toml
[tool.hatch.build.hooks.hatch-rs]
module = "project"
target-dir = "isolated"
artifact-manifest = true

[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "project_ffi"
manifest = "rust/Cargo.toml"
destination = "project/lib/{shared_library}"
expected-symbols = ["project_ffi_answer"]
expected-headers = ["project/include/project.h"]
expected-abi-strings = ["PROJECT_ABI_VERSION"]
validate = true
include-import-lib = true

[[tool.hatch.build.hooks.hatch-rs.artifacts.validation-commands]]
command = ["python", "scripts/validate_abi.py", "{destination}", "{header}"]
```

`include-import-lib` only packages an import library on Windows targets, where
Cargo emits `.dll.lib` or `.dll.a` files for downstream native linkers.

### Platform tags and cibuildwheel

Binary wheel tags are generated with `packaging.tags` from the resolved Rust
target. Linux builds default to `linux_<arch>` unless `AUDITWHEEL_PLAT` is set
by auditwheel/cibuildwheel or `wheel-platform-tag` is configured explicitly.
Rust targets should use concrete triples such as `x86_64-unknown-linux-gnu` or
`x86_64-unknown-linux-musl`; manylinux and musllinux are wheel platform tags,
not Rust target triples.

For cibuildwheel, keep Cargo outputs isolated so repeated platform builds do not
reuse stale artifacts from another target:

```toml
[tool.hatch.build.hooks.hatch-rs]
module = "project"
target-dir = "isolated"

[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "project"
manifest = "Cargo.toml"
destination = "project/{python_extension_name}"

[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "project_ffi"
manifest = "rust/Cargo.toml"
destination = "project/lib/{shared_library}"

[tool.cibuildwheel]
build = "cp311-*"
test-command = "python -c \"import project\""
```

When cross-building outside cibuildwheel, set `wheel-platform-tag` only if the
final wheel platform tag is known, for example `manylinux_2_28_x86_64`.

### Pyodide

Pyodide builds are detected from `CARGO_BUILD_TARGET=wasm32-unknown-emscripten`.
The hook collects Cargo's `.wasm` cdylib, gives it CPython's Emscripten extension
suffix, and uses `PYODIDE_ABI_VERSION` for the `pyemscripten` wheel platform tag.
No project-specific build hook is required.

```toml
[tool.hatch.build.hooks.hatch-rs]
module = "project"
path = "."

[tool.cibuildwheel.pyodide]
xbuild-tools = ["cargo", "rustc", "rustup"]
test-command = "python -c 'import project.project'"
```

Rust 1.95 or newer is required to emit an Emscripten shared library for a
`cdylib` automatically. `wheel-platform-tag` remains available as an explicit
override when building outside Pyodide's configured environment.

> [!NOTE]
> This library was generated using [copier](https://copier.readthedocs.io/en/stable/) from the [Base Python Project Template repository](https://github.com/python-project-templates/base).
