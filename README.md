# DCS Mission Creator

A minimal uv-compatible Python project for building Digital Combat Simulator missions with [pydcs](https://github.com/pydcs/dcs).

## Setup

Install the project dependencies with uv:

```bash
uv sync
```

## Usage

Generate a starter mission:

```bash
uv run dcs-mission-creator --output mission.miz
```

The command creates a basic `.miz` mission file in the current directory.
