"""
Lists the cameras each installed SDK can see.

First-line diagnostic when the app cannot find a camera: if it does not show up
here, the problem is the network, the firewall or the vendor driver, not the app.

Exits 0 when at least one camera is detected, 1 when none is.
"""

import sys

_LINE_WIDTH = 66

# Driver ids as written in config.yaml. Owned by REGISTERED_DRIVERS in
# tools/camera/camera_factory.py, and copied here on purpose: this script must run
# on a machine where the app tree is not importable yet.
_BASLER_DRIVER = "basler_gige"
_SENTECH_DRIVER = "st_gige"

# BaslerDriver looks the camera up by IP among GigE devices, so any other device
# class cannot be reached through the basler_gige driver.
_BASLER_GIGE_CLASS = "BaslerGigE"

# Name shown for the device, first hit wins.
_TITLE_FIELDS = ("GetModelName", "display_name", "model", "GetFriendlyName")

# Label -> candidate attribute names, first hit wins. pypylon exposes getter methods
# (GetIpAddress()) and StApi exposes properties (device_id), so both spellings of the
# same concept are listed and whatever the SDK has gets printed.
_DEVICE_FIELDS = (
    ("class",  ("GetDeviceClass", "tl_type", "device_type")),
    ("vendor", ("GetVendorName", "vendor")),
    ("id",     ("device_id", "GetDeviceID")),
    ("serial", ("GetSerialNumber", "serial_number")),
    ("ip",     ("GetIpAddress", "ip_address", "ip")),
    ("mac",    ("GetMacAddress", "mac_address", "mac")),
    ("user",   ("GetUserDefinedName", "user_defined_name")),
    # A camera already opened by another process shows up here rather than as a
    # mysterious connect() failure later.
    ("access", ("access_status", "GetAccessStatus")),
)

# pypylon fills the fields a transport does not have with "N/A" instead of failing,
# and an unassigned GigE camera reports 0.0.0.0. Neither is usable as an address, so
# both count as missing.
_MISSING_VALUES = frozenset({"", "n/a", "none", "null", "0.0.0.0"})

# The setup scripts live in a per-platform folder.
_IS_WINDOWS = sys.platform == "win32"
_BASLER_SETUP = ("setup\\cameras\\windows\\basler.ps1" if _IS_WINDOWS
                 else "setup/cameras/linux/basler.sh")
_SENTECH_SETUP = ("setup\\cameras\\windows\\sentech.ps1" if _IS_WINDOWS
                  else "setup/cameras/linux/sentech.sh")


def _print_header(title: str):
    print("-" * _LINE_WIDTH)
    print(title)
    print("-" * _LINE_WIDTH)


def _read_field(source, attr_name: str) -> str | None:
    """
    Reads one device-info field, or None when the SDK does not expose it.

    The attribute is called only if it turns out to be callable, so the same reader
    works for pypylon's getters and StApi's properties.
    """
    try:
        value = getattr(source, attr_name)
        if callable(value):
            value = value()
        # StApi returns enums here; their repr carries the class name as noise.
        if not isinstance(value, str) and hasattr(value, "name"):
            value = value.name
    except Exception:
        return None
    text = str(value).strip()
    return None if text.lower() in _MISSING_VALUES else text


def _read_first(source, attr_names: tuple) -> str | None:
    """Returns the first of several candidate attribute names that resolves."""
    for attr_name in attr_names:
        value = _read_field(source, attr_name)
        if value:
            return value
    return None


def _print_device(index: int, source, extra: dict | None = None) -> dict:
    """
    Prints one device with whatever fields its SDK exposes.

    `extra` supplies fields the device info itself does not carry, keyed by the same
    labels. Returns everything it found, so the caller can build the config.yaml hint
    out of it.
    """
    print(f"  [{index}] {_read_first(source, _TITLE_FIELDS) or 'unknown'}")
    extra = extra or {}
    found = {}
    for label, candidates in _DEVICE_FIELDS:
        value = extra.get(label) or _read_first(source, candidates)
        if value:
            found[label] = value
            print(f"       {label:6}: {value}")
    return found


def _sentech_ips(st_module, st_interface) -> dict:
    """
    Maps device index -> IP for one StApi interface.

    StApi's device info carries no IP: its GenTL producer keys GigE cameras by MAC,
    which is what device_id holds. The interface nodemap does publish each camera's
    IP, and without opening the camera.
    """
    ips = {}
    try:
        nodemap = st_interface.port.nodemap
        selector = st_module.PyIInteger(nodemap.get_node("DeviceSelector"))
        ip_node = st_module.PyIInteger(nodemap.get_node("GevDeviceIPAddress"))
    except Exception:
        return ips

    for index in range(st_interface.device_count):
        try:
            selector.set_value(index)
            packed = ip_node.value
            ips[index] = ".".join(str((packed >> s) & 0xFF) for s in (24, 16, 8, 0))
        except Exception:
            continue
    return ips


def _list_basler() -> int:
    """Prints the Basler devices pypylon enumerates and returns how many there are."""
    _print_header("Basler (pypylon)")
    try:
        from pypylon import pylon
    except ImportError as e:
        print(f"  pypylon not available: {e}")
        print(f"  See {_BASLER_SETUP} to install it.")
        return 0

    try:
        devices = pylon.TlFactory.GetInstance().EnumerateDevices()
    except Exception as e:
        print(f"  Enumeration failed: {e}")
        return 0

    if not devices:
        print("  No cameras detected.")
        return 0

    for i, device in enumerate(devices):
        found = _print_device(i, device)
        # BaslerDriver matches on the IP, so that is what goes into `address`.
        if found.get("class") == _BASLER_GIGE_CLASS and found.get("ip"):
            print(f"       config.yaml -> driver: {_BASLER_DRIVER} | "
                  f"address: {found['ip']}")
        else:
            print(f"       not reachable through the {_BASLER_DRIVER} driver: "
                  f"it needs class {_BASLER_GIGE_CLASS} and an IP")

    return len(devices)


def _list_sentech() -> int:
    """Prints the Sentech devices StApi enumerates and returns how many there are."""
    _print_header("Sentech / Omron (stapipy)")
    try:
        import stapipy as st
    except ImportError as e:
        print(f"  stapipy not available: {e}")
        print(f"  See {_SENTECH_SETUP} to install the SDK and its wheel.")
        return 0

    try:
        st.initialize()
        system = st.create_system()
    except Exception as e:
        print(f"  StApi initialization failed: {e}")
        return 0

    count = 0
    try:
        for i in range(system.interface_count):
            interface = system.get_interface(i)
            ip_by_index = _sentech_ips(st, interface)

            for j in range(interface.device_count):
                found = _print_device(count, interface.get_device_info(j),
                                      {"ip": ip_by_index.get(j)})
                # StDriver takes either: an IP is translated to the device_id before
                # opening. The IP is preferred because it is the one you can ping and
                # the one the Basler entries use, so both drivers read the same.
                address = found.get("ip") or found.get("id")
                if address:
                    print(f"       config.yaml -> driver: {_SENTECH_DRIVER} | "
                          f"address: {address}")
                else:
                    print(f"       neither ip nor device id: {_SENTECH_DRIVER} "
                          f"cannot target this camera")
                count += 1
    except Exception as e:
        print(f"  Failed while walking the interfaces: {e}")

    if count == 0:
        print("  No cameras detected.")
    return count


def main() -> int:
    print("=" * _LINE_WIDTH)
    print("Cameras detected on this machine")
    print("=" * _LINE_WIDTH)
    total = _list_basler() + _list_sentech()
    print("=" * _LINE_WIDTH)
    if total:
        print(f"Total: {total} camera(s).")
        return 0
    print("Total: none. Use the 'mock' driver for development.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
