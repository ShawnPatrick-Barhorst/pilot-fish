# Pilot Fish

[![Python Version](https://img.shields.io/badge/python-3.14%2B-blue)](https://www.python.org/)
[![Package Manager](https://img.shields.io/badge/uv-supported-261230)](https://github.com/astral-sh/uv)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

**Pilot Fish** is an extensible monitoring tool designed to run alongside your Python app, capturing every function call plus any custom data you choose to tap.



---
## Overview

Most monitoring tools assume you already have the infrastructure for them: a job queue with Flower, Prometheus to collect metrics, a SQL database to store it all. For a solo hobbyist that setup is rarely worth the effort, so most small projects end up with no monitoring at all.

Pilot Fish needs none of that. Wrap your code in a with block and it runs alongside your app, recording each of your functions to a local file. You can extend it with taps; small functions you write to record your own data.

## Features

 - **Zero infrastructure** — Impliment your monitor in less than 5 lines of code, without any pre-existing infrastructure.
- **Custom taps** — Collect custom telemetry by "tapping" a function and using it within your Monitor.
- **Thread-aware** — Works for multi-threaded applications on Python 3.14+, with or without the GIL.


## Installation


Requires Python 3.14 or newer, regular or free-threaded.

```bash
pip install pilot-fish
```

or with uv:

```bash
uv add pilot-fish
```

## Quickstart

## Quick start

### Without a tap

Wrap the part of your program you want to watch. Every call to your own functions is recorded to `trace.jsonl`.

```python
from pilot_fish import Monitor, JsonlSink

with Monitor(JsonlSink("trace.jsonl")):
    main()
```

### With a tap

A tap records your own data. Write a function that returns the value you care about, register it with `@tap.extend`, then call it through the tap anywhere in your code.

```python
from pilot_fish import Monitor, JsonlSink, Tap

tap = Tap()

@tap.extend
def batch_size(batch):
    return len(batch)

def process(batch):
    tap.batch_size(batch)
    ...

with Monitor(JsonlSink("trace.jsonl"), tap=tap):
    process([1, 2, 3])
```

Each value is recorded against the call it happened in: here, `3` is attached to the `process` call. Outside a `with Monitor(...)` block, `tap.batch_size(...)` does nothing, so taps can stay in your code.

Call taps from inside your functions, not directly in the `with` block.


## License

Distributed under the Apache 2.0 License. See `LICENSE` for more information.