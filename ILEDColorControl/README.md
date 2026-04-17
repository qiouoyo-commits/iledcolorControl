# ILEDColorControl

Python package for the validated `iledcolor` BLE control path.

## Features

This package currently focuses on the parts that are confirmed to work on real hardware:

- `AE00` RCSP authentication
- `RCSP GetTargetInfo (0x03)`
- `A95x` direct-stream rendering
- BLE discovery and probing helpers
- BTSnoop parsing utilities

## Installation

### Editable Install

From the repository root:

```bash
python3 -m pip install -e ./ILEDColorControl
```

From inside the package directory:

```bash
cd ILEDColorControl
python3 -m pip install -e .
```

### Optional Extra

Install with `gattlib` support:

```bash
cd ILEDColorControl
python3 -m pip install -e ".[gattlib]"
```

### Requirements

- Python `>=3.9`
- `bleak`

## Quick Start

Send a direct solid-red frame:

```bash
python3 examples/direct_red.py \
  F419210F-C4C7-BF00-3E7C-0EF7EF1AACC0 \
  --manufacturer-data 424402001000200300000b00060044000000d60508004a4c414953444b \
  --solid ff0000
```

Probe the device and fetch `TargetInfo`:

```bash
python3 examples/target_info.py \
  F419210F-C4C7-BF00-3E7C-0EF7EF1AACC0
```

## Package Layout

- [iledcolorcontrol/direct.py](./iledcolorcontrol/direct.py)
  - direct-stream packet builders and high-level controller
- [iledcolorcontrol/rcsp.py](./iledcolorcontrol/rcsp.py)
  - JieLi/RCSP authentication and frame parsing
- [iledcolorcontrol/ble.py](./iledcolorcontrol/ble.py)
  - BLE discovery, transport selection, and probing helpers
- [examples](./examples)
  - runnable example scripts
- [tools/parse_btsnoop.py](./tools/parse_btsnoop.py)
  - BTSnoop helper script

## Release

The package is structured so it can be built directly from this directory.

### 1. Bump The Version

Update the version in [setup.py](./setup.py).

### 2. Clean Old Build Artifacts

```bash
cd ILEDColorControl
rm -rf build dist *.egg-info
```

### 3. Build Source And Wheel Distributions

```bash
cd ILEDColorControl
python3 -m pip install -U build twine
python3 -m build
```

### 4. Validate The Distributions

```bash
cd ILEDColorControl
python3 -m twine check dist/*
```

### 5. Upload

TestPyPI:

```bash
cd ILEDColorControl
python3 -m twine upload --repository testpypi dist/*
```

PyPI:

```bash
cd ILEDColorControl
python3 -m twine upload dist/*
```

## Scope

This package is intentionally conservative: only the validated path is treated as public API baseline.

The following areas are still considered experimental and are not presented as stable here:

- playlist/programme container uploads
- resource-only flows
- unverified animation/text builders

## License

MIT. See [LICENSE](./LICENSE).
