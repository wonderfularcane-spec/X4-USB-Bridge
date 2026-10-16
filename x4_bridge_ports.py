"""Pure helpers for identifying a printer serial port and choosing retry delays."""


def is_ch340(port):
    label = (getattr(port, "description", "") or "").upper()
    return "CH340" in label or (
        getattr(port, "vid", None) == 0x1A86
        and getattr(port, "pid", None) in (0x7523, 0x5523)
    )


def port_identity(port):
    return (
        getattr(port, "vid", None),
        getattr(port, "pid", None),
        getattr(port, "serial_number", None),
        getattr(port, "location", None),
        port.device,
    )


def is_same_device(port, identity):
    if identity is None:
        return False
    vid, pid, serial_number, location, previous_port = identity
    if (getattr(port, "vid", None), getattr(port, "pid", None)) != (vid, pid):
        return False
    if serial_number:
        return getattr(port, "serial_number", None) == serial_number
    if location:
        return getattr(port, "location", None) == location
    return port.device.casefold() == previous_port.casefold()


def select_com_port(ports, preferred=None, identity=None):
    """Never connect to a new, unidentified USB device after a disconnect."""
    devices = list(ports)
    if identity is not None:
        matches = [p for p in devices if is_same_device(p, identity)]
        return matches[0] if len(matches) == 1 else None
    if preferred:
        matches = [p for p in devices if p.device.casefold() == preferred.casefold()]
        # Never silently fall back to a different USB device when the user
        # explicitly requested one COM port.
        return matches[0] if len(matches) == 1 else None
    matches = [p for p in devices if is_ch340(p)]
    return matches[0] if len(matches) == 1 else None


def retry_seconds(attempt):
    return min(30, 2 ** min(max(attempt, 1), 4) * 2)
