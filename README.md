# deph: Isolate, Analyze, and Compose Your Python Code

[![PyPI version](https://badge.fury.io/py/deph.svg)](https://badge.fury.io/py/deph) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://github.com/dvm-shlee/deph/actions/workflows/python-test.yml/badge.svg)](https://github.com/OWNER/REPO/actions/workflows/python-test.yml) ![Python Versions](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)

<!-- Replace OWNER/REPO above with your GitHub org/user and repository name -->

`deph` is a developer utility designed to untangle Python codebases. It traces and isolates all the necessary source code for a specific function or class (an "endpoint"), analyzes its dependencies, and then composes the code into a clean, self-contained module.

This is especially powerful for code developed in interactive environments like **Jupyter Notebooks**, where dependencies can be scattered and implicit. `deph` helps you extract a piece of logic—like a model's prediction function or a data processing pipeline—from a complex notebook or script and prepare it for refactoring, testing, or future packaging.

## Key Features

-   **Endpoint-based Code Isolation**: Pinpoint a function or class, and `deph` will recursively find all internal dependencies (other functions, classes, global variables) required for it to run.
-   **Dependency Analysis**: Automatically identifies the standard library, third-party, and local modules your isolated code depends on.
-   **Source Code Composition**: Gathers all the required source code and assembles it into a single, clean Python module, ready for use in a new context.
-   **Jupyter-Aware**: Works inside Jupyter Notebook/Lab by analyzing the live Python session (in-memory functions, classes, variables, and imports).

## Why Use `deph`? (The Problem)

Imagine you have a large Jupyter Notebook used for exploratory data analysis. It contains a critical function for training a model, but its helper functions and imports are spread across dozens of cells. To move that training logic into a production pipeline, you would have to:

1.  Manually copy-paste the main function.
2.  Hunt down every helper function it calls.
3.  Figure out which `import` statements are actually needed.
4.  Repeat this process until the code runs without errors.

`deph` automates this. It answers the question: **"What is the absolute minimum code and which libraries are required to run this specific function?"**

**Use Cases:**

-   Isolating logic from a monolithic script to create a reusable module.
-   Extracting a specific feature from a Jupyter Notebook to create a standalone script.
-   Preparing a piece of logic to be packaged into a library or deployed as a service.
-   Understanding the true dependencies of a specific part of your application.

## Installation

```bash
# Core library
python -m pip install deph

# Test dependencies (used only by the test suite)
python -m pip install "deph[test]"
```

## Quickstart

Let's say you have a Python file `my_model.py` containing a function `train_model` that you want to isolate.

```python
# my_model.py
import pandas as pd

def preprocess(data):
    return data.dropna()

def train_model(df: pd.DataFrame):
    processed_df = preprocess(df)
    # ... training logic ...
    return "Model trained!"
```

You can use `deph`'s high-level functions to isolate `train_model` and all its local dependencies (like `preprocess`):

```python
from deph import isolate, analyze
from my_model import train_model

# 1) Analyze the target to inspect dependencies
report = analyze(train_model)
print("--- IMPORTS ---")
print(report["imports"])  # Dict by module with ImportItem entries

# 2) Isolate the target function to get the composed code result
result = isolate(train_model)

# 3) Print the results
print("--- ISOLATED SOURCE CODE ---")
print(result.source)

print("\n--- IDENTIFIED DEPENDENCIES ---")
print(report.get("imports"))

# Extras available on result:
# - result.warnings   (list of unresolved names; also printed to stderr)
# - result.reqs_pypi  (inferred non-stdlib PyPI packages)
# - result.reqs_unknown (non-stdlib, not resolvable on PyPI)

```
This will produce a self-contained script with `train_model`, `preprocess`, and the necessary `import pandas as pd` statement, along with a detailed dependency report.

## Utilities (Optional)

`deph.utils` includes a few lightweight helpers used in examples:

- `deph.utils.log`: simple console/file logging configuration
- `deph.utils.zip`: ZIP archive helpers
- `deph.utils.pip`: a thin wrapper around `pip`