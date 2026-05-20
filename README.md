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

> [!NOTE]
> This library was generated using [copier](https://copier.readthedocs.io/en/stable/) from the [Base Python Project Template repository](https://github.com/python-project-templates/base).
