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

> [!NOTE]
> This library was generated using [copier](https://copier.readthedocs.io/en/stable/) from the [Base Python Project Template repository](https://github.com/python-project-templates/base).
