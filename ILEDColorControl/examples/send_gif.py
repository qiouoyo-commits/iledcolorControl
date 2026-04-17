import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from iledcolorcontrol import (
    DEFAULT_MANUFACTURER_ID,
    IledColorController,
    decode_iledcolor_advertisement,
    parse_gif_logical_screen_size,
    recommended_chunk_size,
)


if __name__ == "__main__":
    args = list(sys.argv[1:])
    enable_display = False
    chunk_size = None
    manufacturer_hex = None

    if "--enable-display" in args:
        args.remove("--enable-display")
        enable_display = True

    if "--chunk-size" in args:
        index = args.index("--chunk-size")
        chunk_size = int(args[index + 1])
        del args[index : index + 2]

    if "--manufacturer-data" in args:
        index = args.index("--manufacturer-data")
        manufacturer_hex = args[index + 1]
        del args[index : index + 2]

    if len(args) < 2:
        raise SystemExit(
            "Usage: python3 examples/send_gif.py <device> <file.gif> "
            "[--manufacturer-data HEX] [--chunk-size N] [--enable-display]"
        )

    device_name = args[0]
    gif_path = args[1]
    gif_bytes = Path(gif_path).read_bytes()
    gif_width, gif_height = parse_gif_logical_screen_size(gif_bytes)

    print("GIF upload path")
    print("GIF logical screen:", gif_width, "x", gif_height)

    if manufacturer_hex is not None:
        info = decode_iledcolor_advertisement(
            bytes.fromhex(manufacturer_hex),
            manufacturer_id=DEFAULT_MANUFACTURER_ID,
        )
        print("Decoded advertisement size:", info.width, "x", info.height)
        print("Decoded screen color type:", info.screen_color_type)
        print("Decoded fun_code:", hex(info.fun_code))
        print("Supports time:", info.supports_time)
        print("Supports gif:", info.supports_gif)
        if (info.width, info.height) != (gif_width, gif_height):
            print(
                "Warning: GIF size does not match advertisement size:",
                f"{gif_width}x{gif_height} vs {info.width}x{info.height}",
            )

    controller = IledColorController(device_name, backend="bleak", auto_auth=True)
    print("MTU:", controller.mtu)
    print("Recommended chunk size:", recommended_chunk_size(controller.mtu))
    print("RCSP authenticated:", controller.authenticated)

    result = controller.send_gif_file(
        gif_path,
        chunk_size=chunk_size,
        enable_display=enable_display,
    )

    print("Connect notification:", None if result.connect_notification is None else vars(result.connect_notification))
    print("TestPass notification:", None if result.test_pass_notification is None else vars(result.test_pass_notification))
    if result.enable_notification is not None:
        print("Enable notification:", vars(result.enable_notification))
    print("Start notification:", None if result.start_notification is None else vars(result.start_notification))
    print("End notification:", None if result.end_notification is None else vars(result.end_notification))

    controller.disconnect()
