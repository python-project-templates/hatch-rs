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

### Shared C ABI libraries

Projects can declare multiple Rust artifacts in one hook. A `python-extension`
artifact keeps the existing PyO3 extension flow, while a `shared-library`
artifact builds a standalone `cdylib`, copies the exact platform library name to
the configured destination, and includes it in the wheel.

```toml
[tool.hatch.build.hooks.hatch-rs]
verbose = true
path = "."
module = "project"
target-dir = "isolated"

[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "python-extension"
kind = "python-extension"
manifest = "Cargo.toml"
library = "project"

[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "c-abi"
kind = "shared-library"
manifest = "rust/Cargo.toml"
library = "project_ffi"
crate-type = "cdylib"
destination = "project/lib/{shared_library}"
```

Destination templates support `{module}`, `{target}`, `{profile}`, `{library}`,
`{shared_library}`, and `{python_extension_name}`.

### Generated files and headers

`command` artifacts run an argv-list command and then validate explicit outputs.
Outputs can be packaged into the wheel, installed as wheel shared data, or used
only as required validation checks. `header` artifacts are a typed shorthand for
validated header outputs and can also use `generator = "cbindgen"` in CLI or
build-script mode.

```toml
[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "generated-package-files"
kind = "command"
command = ["python", "scripts/write_generated_files.py"]
inputs = ["scripts/write_generated_files.py"]

[[tool.hatch.build.hooks.hatch-rs.artifacts.outputs]]
source = "project/generated/package.txt"
destination = "project/generated/package.txt"
install-scheme = "package"

[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "public-c-header"
kind = "header"
source = "project/include/project.h"
destination = "include/project/project.h"
install-scheme = "shared-data"
```

### ABI validation and artifact metadata

Shared-library artifacts can validate the copied C ABI library before the wheel
is finalized. The hook can check expected exported symbols, verify headers and
ABI strings/macros, load the copied library with `ctypes.CDLL`, run project
validation commands, include Windows import libraries, and emit a package-local
artifact manifest.

```toml
[tool.hatch.build.hooks.hatch-rs]
module = "project"
target-dir = "isolated"
artifact-manifest = true

[[tool.hatch.build.hooks.hatch-rs.artifacts]]
name = "c-abi"
kind = "shared-library"
manifest = "rust/Cargo.toml"
library = "project_ffi"
crate-type = "cdylib"
destination = "project/lib/{shared_library}"
expected-symbols = ["project_ffi_answer"]
expected-headers = ["project/include/project.h"]
expected-abi-strings = ["PROJECT_ABI_VERSION"]
runtime-load = true
include-import-lib = true

[[tool.hatch.build.hooks.hatch-rs.artifacts.validation-commands]]
command = ["python", "scripts/validate_abi.py", "{destination}", "{header}"]
```

`include-import-lib` only packages an import library on Windows targets, where
Cargo emits `.dll.lib` or `.dll.a` files for downstream native linkers.

> [!NOTE]
> This library was generated using [copier](https://copier.readthedocs.io/en/stable/) from the [Base Python Project Template repository](https://github.com/python-project-templates/base).
