# ILEDColorControl

Clean Python control path for `iledcolor` BLE displays.

This repository now focuses on the validated, publicly useful part of the project:

- `AE00` RCSP authentication
- `RCSP GetTargetInfo (0x03)`
- `A95x` direct-stream image rendering
- BLE probing and debugging helpers

The maintained package lives in [ILEDColorControl](./ILEDColorControl).

## What Works

The current known-good path for the tested device family is:

1. authenticate on `AE00 / AE01 / AE02`
2. connect to the display transport on `A950 / A951 / A952 / A953`
3. send direct-stream packets
4. render raw RGB888 frames

Validated capabilities:

- same-link `AE00` RCSP authentication
- `RCSP GetTargetInfo`
- `A95x` direct-stream `Connect -> TestPass -> StartStream -> Continue -> EndStream`
- solid-color rendering on real hardware
- GIF upload with device-side loop playback on real hardware

Validated test target:

- device name: `iledcolor-3A05`
- resolution: `32x16`
- manufacturer data:
  - `424402001000200300000b00060044000000d60508004a4c414953444b`

## Install

From the repository root:

```bash
python3 -m pip install -e ./ILEDColorControl
```

Or from inside the package directory:

```bash
cd ILEDColorControl
python3 -m pip install -e .
```

Runtime dependency:

- `bleak`

Optional extra:

- `gattlib`

Install with the optional extra:

```bash
cd ILEDColorControl
python3 -m pip install -e ".[gattlib]"
```

## Quick Start

Send a solid red frame:

```bash
python3 ILEDColorControl/examples/direct_red.py \
  F419210F-C4C7-BF00-3E7C-0EF7EF1AACC0 \
  --manufacturer-data 424402001000200300000b00060044000000d60508004a4c414953444b \
  --solid ff0000
```

Probe services and fetch target info:

```bash
python3 ILEDColorControl/examples/target_info.py \
  F419210F-C4C7-BF00-3E7C-0EF7EF1AACC0
```

GIF upload path:

```bash
python3 ILEDColorControl/examples/send_gif.py \
  F419210F-C4C7-BF00-3E7C-0EF7EF1AACC0 \
  ./demo.gif \
  --manufacturer-data 424402001000200300000b00060044000000d60508004a4c414953444b
```

## Repository Layout

- [ILEDColorControl](./ILEDColorControl)
  - Python package
  - examples
  - RCSP helpers
  - direct-stream helpers
  - BLE transport helpers

## Release

Package build and release instructions are documented in [ILEDColorControl/README.md](./ILEDColorControl/README.md).

Recommended release flow:

1. bump the version in [ILEDColorControl/setup.py](./ILEDColorControl/setup.py)
2. build from `ILEDColorControl/`
3. run `twine check`
4. upload to PyPI when ready

## Notes

This repository intentionally presents only the currently validated control path as the public baseline.

Things that are not advertised here as stable yet:

- playlist/programme container uploads
- resource-only upload flows
- unverified animation/text builders

## License

MIT. See [LICENSE](./LICENSE).
