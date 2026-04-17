import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from iledcolorcontrol import (
    DEFAULT_MANUFACTURER_ID,
    IledColorController,
    decode_iledcolor_advertisement,
    recommended_chunk_size,
)


def parse_hex_color(value):
    value = value.strip().lower()
    if value.startswith("#"):
        value = value[1:]
    if value.startswith("0x"):
        value = value[2:]
    if len(value) != 6:
        raise SystemExit("Colour values must be 6-digit RGB hex such as ff0000.")
    return int(value, 16)


if __name__ == "__main__":
    args = list(sys.argv[1:])
    enable_display = False
    width = 32
    height = 16
    color = 0xFF0000
    chunk_size = None

    if "--enable-display" in args:
        args.remove("--enable-display")
        enable_display = True

    if "--width" in args:
        index = args.index("--width")
        width = int(args[index + 1])
        del args[index : index + 2]

    if "--height" in args:
        index = args.index("--height")
        height = int(args[index + 1])
        del args[index : index + 2]

    if "--chunk-size" in args:
        index = args.index("--chunk-size")
        chunk_size = int(args[index + 1])
        del args[index : index + 2]

    if "--solid" in args:
        index = args.index("--solid")
        color = parse_hex_color(args[index + 1])
        del args[index : index + 2]

    if "--manufacturer-data" in args:
        index = args.index("--manufacturer-data")
        manufacturer_hex = args[index + 1]
        del args[index : index + 2]
        info = decode_iledcolor_advertisement(
            bytes.fromhex(manufacturer_hex),
            manufacturer_id=DEFAULT_MANUFACTURER_ID,
        )
        width = info.width
        height = info.height
        print("Decoded advertisement size:", info.width, "x", info.height)
        print("Decoded screen color type:", info.screen_color_type)
        print("Decoded fun_code:", hex(info.fun_code))
        print("Supports time:", info.supports_time)
        print("Supports gif:", info.supports_gif)

    device_name = args[0] if args else "iledcolor"

    controller = IledColorController(device_name, backend="bleak", auto_auth=True)
    print("MTU:", controller.mtu)
    print("Recommended chunk size:", recommended_chunk_size(controller.mtu))
    print("RCSP authenticated:", controller.authenticated)

    result = controller.send_direct_solid_color(
        color,
        width=width,
        height=height,
        chunk_size=chunk_size,
        enable_display=enable_display,
    )

    print("Direct solid colour:", f"{color:06x}")
    print("Connect notification:", None if result.connect_notification is None else vars(result.connect_notification))
    print("TestPass notification:", None if result.test_pass_notification is None else vars(result.test_pass_notification))
    if result.enable_notification is not None:
        print("Enable notification:", vars(result.enable_notification))
    print("Start notification:", None if result.start_notification is None else vars(result.start_notification))
    print("End notification:", None if result.end_notification is None else vars(result.end_notification))

    controller.disconnect()
