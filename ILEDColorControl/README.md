# ILEDColorControl

Clean Python control package for the verified `iledcolor` BLE path.

This package contains only the currently validated pieces:

- AE00 RCSP authentication
- RCSP target-info query
- A95x direct-stream rendering
- BLE discovery/probing helpers
- BTSnoop parser and a verified capture sample

The full reverse-engineering summary for this cleaned repository is in [../README.md](../README.md).

Reference-material comparison notes are in [../reference/README.md](../reference/README.md).

The current "worth porting next" shortlist is in [../reference/MIGRATION_TODO.md](../reference/MIGRATION_TODO.md).

## Quick Start

```bash
python3 examples/direct_red.py \
  F419210F-C4C7-BF00-3E7C-0EF7EF1AACC0 \
  --manufacturer-data 424402001000200300000b00060044000000d60508004a4c414953444b \
  --solid ff0000
```
