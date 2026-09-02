"""Normalising and validating the two identifiers on a physical unit.

Both live here rather than in the serializer because three code paths write
them -- checking in a receipt, correcting a unit afterwards, and (in future)
any import -- and they must agree. A MAC stored in one format and searched
for in another is a unit you own but cannot find.
"""

import re

# Canonical form is upper-case, colon-separated: AA:BB:CC:DD:EE:FF. That is
# what MikroTik, RADIUS accounting and every device label in the field use,
# so storing it this way means a MAC copied off a router pastes straight into
# the search box and matches.
_MAC_SEPARATORS = re.compile(r"[\s:.\-_]")
_TWELVE_HEX = re.compile(r"^[0-9A-F]{12}$")


class InvalidMac(ValueError):
    """Raised with a message written for the person who typed it."""


def normalise_mac(value):
    """Canonicalise any of the common MAC spellings, or raise InvalidMac.

    Accepts AA:BB:CC:DD:EE:FF, aa-bb-cc-dd-ee-ff, aabb.ccdd.eeff, and bare
    aabbccddeeff -- the four formats you actually get off equipment labels,
    supplier spreadsheets and Cisco-style output. Blank stays blank: a MAC is
    optional, since not every serialized item has one (a splice tool doesn't).
    """
    if value is None:
        return ""
    stripped = _MAC_SEPARATORS.sub("", str(value)).upper()
    if not stripped:
        return ""
    if not _TWELVE_HEX.match(stripped):
        raise InvalidMac(
            f"'{value}' is not a MAC address. Expected 12 hex digits, e.g. "
            "AA:BB:CC:DD:EE:FF (dashes, dots or no separators are fine too)."
        )
    return ":".join(stripped[i:i + 2] for i in range(0, 12, 2))


def normalise_serial(value):
    """Trim and upper-case a serial number.

    Upper-cased for the same reason customer payment references are: Postgres
    compares case-sensitively, so 'sn1234' and 'SN1234' would both be allowed
    to exist while a human searching for either means the one unit. Storing
    one canonical form makes the duplicate check below actually work.
    """
    return (value or "").strip().upper()


def parse_serial_lines(raw_text):
    """Parse the receipt form's serial block into [(serial, mac), ...].

    One unit per line, as 'SERIAL' or 'SERIAL,MAC'. Blank lines are skipped
    so a trailing newline or a pasted block with gaps in it doesn't create a
    phantom unit. Raises InvalidMac (or ValueError) with a message naming the
    offending line, because "invalid MAC" on a 40-line paste is useless.
    """
    parsed = []
    for number, line in enumerate(raw_text.splitlines(), start=1):
        entry = line.strip()
        if not entry:
            continue
        serial_raw, _, mac_raw = entry.partition(",")
        serial = normalise_serial(serial_raw)
        if not serial:
            raise ValueError(f"Line {number} has a MAC address but no serial number.")
        try:
            mac = normalise_mac(mac_raw)
        except InvalidMac as exc:
            raise InvalidMac(f"Line {number}: {exc}") from exc
        parsed.append((serial, mac))
    return parsed
