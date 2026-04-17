import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from iledcolorcontrol import IledColorController, probe_device


if __name__ == "__main__":
    device_name = sys.argv[1] if len(sys.argv) > 1 else "iledcolor"

    print("Probing GATT services...")
    info = probe_device(device_name)
    for service in info["services"]:
        print(service["uuid"])
        for characteristic in service["characteristics"]:
            print(f"  {characteristic['uuid']} {characteristic['properties']}")

    controller = IledColorController(device_name, backend="bleak", auto_auth=True)
    print("\nAuthenticated:", controller.authenticated)
    target_info = controller.get_target_info()
    print("Target info opcode:", hex(target_info.opcode))
    print("Target info raw:", target_info.raw.hex())
    controller.disconnect()
