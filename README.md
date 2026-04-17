# ILEDColor Reverse Engineering Notes

This repository has been cleaned to keep only the latest APK/reverse-engineering material and the verified Python control path for the `iledcolor` BLE panel family.

The validated controller code now lives in [ILEDColorControl](./ILEDColorControl).

## What Is Confirmed

Only findings that were either reproduced from a capture or verified on real hardware are listed below.

### 1. Device And Advertisement

- Tested device name: `iledcolor-3A05`
- Tested BLE address: `F419210F-C4C7-BF00-3E7C-0EF7EF1AACC0`
- Tested manufacturer data:
  - `424402001000200300000b00060044000000d60508004a4c414953444b`
- Confirmed decoded values:
  - width: `32`
  - height: `16`
  - `screen_color_type`: `3`
  - `fun_code`: `0x44`
  - `supports_time`: `False`
  - `supports_gif`: `True`

### 2. BLE Architecture

The latest app uses two BLE layers on the same device:

- `AE00 / AE01 / AE02`
  - vendor/JieLi RCSP-style authentication and management channel
- `A950 / A951 / A952 / A953`
  - display/data transport channel

Confirmed UUIDs:

- `0000ae00-0000-1000-8000-00805f9b34fb`
- `0000ae01-0000-1000-8000-00805f9b34fb`
- `0000ae02-0000-1000-8000-00805f9b34fb`
- `0000a950-0000-1000-8000-00805f9b34fb`
- `0000a951-0000-1000-8000-00805f9b34fb`
- `0000a952-0000-1000-8000-00805f9b34fb`
- `0000a953-0000-1000-8000-00805f9b34fb`

### 3. AE00 Authentication

The AE00 side is a confirmed mutual challenge-response handshake.

Known-good constants:

- link key:
  - `06775f87918dd423005df1d8cf0c142b`
- auth seed:
  - `112233332211`
- auth-ok payload:
  - `0270617373`
- reset-auth payload:
  - `fedcbac00600020001ef`

Verified capture pair:

- host challenge:
  - `00249e811a494ebde8f8f71b824b3ae663`
- device response:
  - `01fb4a608c1e9ae92cec1cf0fcf5b2c224`

Verified reverse direction:

- device challenge:
  - `00f654e1cc02ea8c7d4de664173499935d`
- host response:
  - `01d6c5db7a17e2f82033c603dfc68881c7`

The Python implementation reproduces these responses exactly.

### 4. RCSP Target Info

After AE00 authentication, `GetTargetInfo (opcode 0x03)` succeeds.

Known-good request:

- `fedcbac003000600ffffffff00ef`

Known-good response body from the tested panel:

- `000002002005010000000009023704604915630e0006040000004e0002050003080001020900020a00020601050d0080021c081100370460603a05021300`

This confirms that the AE00 side is not just decorative; it is alive and useful before rendering.

### 5. A95x Direct Stream Path

The first fully confirmed rendering path is:

1. AE00 auth on the same BLE connection
2. A95x `Connect (0x0d)`
3. A95x `TestPass (0x0f)`
4. optional `LedEnable (0x0a)`
5. `StartStream (0x06)`
6. `Continue (0x00)` packets
7. `EndStream (0x01)`

This path rendered a solid red frame on real hardware.

Known-good packets:

- Connect:
  - `540d0003000064`
- TestPass with six zero bytes:
  - `540f0008000000000000006b`
- LedEnable(on):
  - `540a000b010000000000000000006a`
- EndStream:
  - `54010003010059`

### 6. Direct Stream Image Format

The validated direct-stream image payload is:

- 11 big-endian 16-bit words:
  - `0000 0000 width height 0000 0001 0001 0001 0032 0064 0000`
- followed by raw pixel bytes
- pixel format for the confirmed path:
  - RGB888

For the tested red frame:

- width: `32`
- height: `16`
- color: `ff0000`

### 7. Direct Stream Wrapper Format

The validated stream wrapper is:

- `CRC32C(image_bytes)` as big-endian `u32`
- `0x01`
- `19x00`
- raw `image_bytes`

The validated `StartStream` payload is:

- `CRC32C(image_bytes)`
- `0000`
- `len(stream_payload)` as big-endian `u16`
- `000000`

The tested solid-red start packet was:

- `5406000d7b6e5ce70000062e00000002c7`

### 8. Chunking

Confirmed on the tested panel:

- negotiated MTU: `515`
- recommended chunk size: `487`

This chunk size was used successfully for the verified direct-stream render.

### 9. What Is Not Treated As Confirmed

These paths are intentionally not presented as working control paths:

- app-style programme / playlist container uploads
- `resource-only` / `playlist + apply` as a stable rendering method
- old SPOTLED-compatible text/send abstractions

Those areas may still be useful for future reverse engineering, but they are not the current known-good baseline.

## Cleaned Repository Layout

- [ILEDColorControl](./ILEDColorControl)
  - validated Python package
  - AE00 auth
  - RCSP target-info query
  - A95x direct-stream render path
  - BTSnoop parser
- [reference](./reference)
  - preserved external references used during reverse engineering
  - currently includes:
    - [`akkaisinabin/iledcolor-rs`](./reference/iledcolor-rs)
  - comparison notes:
    - [reference/README.md](./reference/README.md)
  - migration shortlist:
    - [reference/MIGRATION_TODO.md](./reference/MIGRATION_TODO.md)
- [apk](./apk)
  - latest APK file
  - latest decoded APK reverse
  - latest blutter output reverse

## Quick Start

Direct solid red using the cleaned package:

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

## Notes

The cleaned package is intentionally centered on the verified path:

- same BLE link
- AE00 auth first
- A95x direct stream second

Future work should compare any richer image/text/animation builder against this path rather than using the older programme container as the reference.

For a direct comparison between the external Rust reference and the cleaned Python implementation, see [reference/README.md](./reference/README.md).

For the shortlist of what is still worth porting from the Rust reference, see [reference/MIGRATION_TODO.md](./reference/MIGRATION_TODO.md).
