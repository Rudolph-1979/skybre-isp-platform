"""RADIUS Dynamic Authorization client -- CoA and Disconnect (RFC 5176).

Why this exists
---------------
RADIUS is consulted once, at login, and never again. Everything this platform
does to a live customer therefore has to reach the router some other way:

  * change their speed
  * suspend them
  * restore them
  * move them to a new tariff

Until now the only mechanism was the RouterOS API: log in over 8728 and call
/ppp/active/remove, i.e. cut the customer's session and hope the reconnect
picks up the new answer. That is a blunt instrument and it was also a silent
one -- every failure was swallowed into a log line in a daemon thread, after
the HTTP response had already gone out, so the platform reported success
whether or not anything reached the router.

RFC 5176 is the mechanism actually designed for this. Two packet types:

  CoA-Request (43)        change something about a live session in place
  Disconnect-Request (40) end a live session

A speed change is a CoA. The customer's line does not drop, their session id
does not change, their download does not stall -- the router just re-programs
the queue. Dropping someone's connection to give them a faster one has always
been the wrong shape; it was simply the only shape available.

A disconnect is for when the customer's ADDRESS has to change, which is what
suspension and restoration do here (walled-garden pool in, customer pool out).
A CoA cannot re-address a live PPPoE session, so those still take a reconnect
-- but now a deliberate one, whose success or failure is recorded.

Why hand-rolled rather than pyrad
---------------------------------
The packet format below is about ninety lines and completely specified. A new
pip dependency on a box whose deployment story is "unzip and docker compose
build" is a bigger risk than the code it saves, and pyrad would still need a
Mikrotik dictionary file shipped alongside it. Everything here is stdlib.

What the router needs
---------------------
    /radius incoming set accept=yes port=3799

and the packet has to arrive from an address the router already has in
/radius, signed with that entry's secret -- RouterOS validates incoming
dynamic-authorization packets against its configured servers. That is why
`send()` takes the secret from RadiusNasClient rather than inventing one.
"""
import hashlib
import hmac
import logging
import os
import secrets
import socket
import struct

logger = logging.getLogger(__name__)

# RFC 5176 section 3.
CODE_DISCONNECT_REQUEST = 40
CODE_DISCONNECT_ACK = 41
CODE_DISCONNECT_NAK = 42
CODE_COA_REQUEST = 43
CODE_COA_ACK = 44
CODE_COA_NAK = 45

_ACK_FOR = {CODE_DISCONNECT_REQUEST: CODE_DISCONNECT_ACK, CODE_COA_REQUEST: CODE_COA_ACK}
_NAK_FOR = {CODE_DISCONNECT_REQUEST: CODE_DISCONNECT_NAK, CODE_COA_REQUEST: CODE_COA_NAK}

# Standard attributes (RFC 2865 / 2866), by type number.
ATTR_USER_NAME = 1
ATTR_NAS_IP_ADDRESS = 4
ATTR_FRAMED_IP_ADDRESS = 8
ATTR_VENDOR_SPECIFIC = 26
ATTR_ACCT_SESSION_ID = 44
ATTR_MESSAGE_AUTHENTICATOR = 80
ATTR_ERROR_CAUSE = 101

VENDOR_MIKROTIK = 14988
MIKROTIK_RATE_LIMIT = 8
MIKROTIK_ADDRESS_LIST = 19

DEFAULT_PORT = 3799
DEFAULT_TIMEOUT = 4.0
DEFAULT_RETRIES = 2

# RFC 5176 section 3.6. Only the ones a Mikrotik realistically sends back --
# the rest fall through to "Error-Cause N", which is still more use than
# "it didn't work".
ERROR_CAUSES = {
    201: "Residual session context removed",
    202: "Invalid EAP packet",
    401: "Unsupported attribute",
    402: "Missing attribute",
    403: "NAS identification mismatch",
    404: "Invalid request",
    405: "Unsupported service",
    406: "Unsupported extension",
    407: "Invalid attribute value",
    501: "Administratively prohibited",
    502: "Request not routable (proxy)",
    503: "Session context not found",
    504: "Session context not removable",
    505: "Other proxy processing error",
    506: "Resources unavailable",
    507: "Request initiated",
    508: "Multiple session selection unsupported",
}


class DynAuthError(Exception):
    """Anything that stopped the packet getting a positive answer.

    Carries `error_cause` when the NAS sent one back, because "Session context
    not found" and "Administratively prohibited" call for completely different
    fixes and both used to arrive as the same silent nothing.
    """

    def __init__(self, message, error_cause=None):
        super().__init__(message)
        self.error_cause = error_cause


# --------------------------------------------------------------------------
# packet encoding
# --------------------------------------------------------------------------


def _encode_attribute(attr_type, value):
    if len(value) > 253:
        raise DynAuthError(f"Attribute {attr_type} is {len(value)} bytes; the RADIUS limit is 253.")
    return struct.pack("!BB", attr_type, len(value) + 2) + value


def _encode_vsa(vendor_id, vendor_type, value):
    # RFC 2865 section 5.26, the one-vendor-attribute-per-VSA form every
    # implementation actually uses.
    inner = struct.pack("!BB", vendor_type, len(value) + 2) + value
    return _encode_attribute(ATTR_VENDOR_SPECIFIC, struct.pack("!I", vendor_id) + inner)


def build_attributes(username=None, acct_session_id=None, nas_ip=None, framed_ip=None,
                     rate_limit=None, address_list=None):
    """The session-identification attributes, plus whatever is being changed.

    RFC 5176 section 3 requires enough identification for the NAS to find
    exactly one session. User-Name alone is ambiguous the moment a customer has
    two lines, so Acct-Session-Id is included whenever it is known -- that is
    the value FreeRADIUS already stores in radacct.acctsessionid.
    """
    parts = []
    if username:
        parts.append(_encode_attribute(ATTR_USER_NAME, username.encode("utf-8")))
    if acct_session_id:
        parts.append(_encode_attribute(ATTR_ACCT_SESSION_ID, str(acct_session_id).encode("utf-8")))
    if nas_ip:
        parts.append(_encode_attribute(ATTR_NAS_IP_ADDRESS, socket.inet_aton(nas_ip)))
    if framed_ip:
        parts.append(_encode_attribute(ATTR_FRAMED_IP_ADDRESS, socket.inet_aton(framed_ip)))
    if rate_limit:
        parts.append(_encode_vsa(VENDOR_MIKROTIK, MIKROTIK_RATE_LIMIT, rate_limit.encode("utf-8")))
    if address_list:
        parts.append(_encode_vsa(VENDOR_MIKROTIK, MIKROTIK_ADDRESS_LIST, address_list.encode("utf-8")))
    return b"".join(parts)


def _build_packet(code, identifier, attributes, secret):
    """A signed CoA/Disconnect request.

    Two signatures, and both are needed:

    The Request Authenticator is computed the accounting way (RFC 2866) --
    MD5 over the packet with the authenticator field zeroed, plus the secret.
    Unlike Access-Request it is NOT random; a random one is silently ignored
    by some NASes and rejected by others.

    Message-Authenticator (RFC 3579) is an HMAC-MD5 over the whole packet with
    its own field zeroed. RFC 5176 says SHOULD; it is cheap, and it is what
    stops an off-path attacker forging a disconnect for any customer whose
    username they can guess. Sent always.
    """
    secret_bytes = secret.encode("utf-8")

    # Reserve the Message-Authenticator slot with zeros so the length is right
    # before either signature is computed over it.
    placeholder = _encode_attribute(ATTR_MESSAGE_AUTHENTICATOR, b"\x00" * 16)
    body = attributes + placeholder
    length = 20 + len(body)

    header = struct.pack("!BBH", code, identifier, length)
    zero_auth = b"\x00" * 16

    message_auth = hmac.new(secret_bytes, header + zero_auth + body, hashlib.md5).digest()
    body = attributes + _encode_attribute(ATTR_MESSAGE_AUTHENTICATOR, message_auth)

    authenticator = hashlib.md5(header + zero_auth + body + secret_bytes).digest()
    return header + authenticator + body, authenticator


def _parse_error_cause(payload):
    index = 0
    while index + 2 <= len(payload):
        attr_type = payload[index]
        attr_len = payload[index + 1]
        if attr_len < 2 or index + attr_len > len(payload):
            break
        if attr_type == ATTR_ERROR_CAUSE and attr_len == 6:
            return struct.unpack("!I", payload[index + 2:index + 6])[0]
        index += attr_len
    return None


def _verify_response(data, request_code, identifier, request_authenticator, secret):
    if len(data) < 20:
        raise DynAuthError(f"The NAS replied with {len(data)} bytes; a RADIUS packet is at least 20.")

    code, resp_id, length = struct.unpack("!BBH", data[:4])
    if resp_id != identifier:
        raise DynAuthError(f"Reply was for request id {resp_id}, not {identifier}.")

    received_auth = data[4:20]
    payload = data[20:length]

    # The Response Authenticator proves the reply came from something holding
    # the shared secret. Without this check any host that can reach us could
    # answer ACK and the platform would record a success that never happened
    # -- which is precisely the failure mode this whole module exists to end.
    expected = hashlib.md5(
        data[:4] + request_authenticator + payload + secret.encode("utf-8")
    ).digest()
    if not hmac.compare_digest(expected, received_auth):
        raise DynAuthError(
            "The reply's authenticator doesn't match the shared secret. "
            "The secret on this NAS record and the one on the router differ."
        )

    if code == _ACK_FOR[request_code]:
        return True

    if code == _NAK_FOR[request_code]:
        cause = _parse_error_cause(payload)
        label = ERROR_CAUSES.get(cause, f"Error-Cause {cause}") if cause else "no reason given"
        raise DynAuthError(f"The router refused it: {label}.", error_cause=cause)

    raise DynAuthError(f"Unexpected reply code {code}.")


# --------------------------------------------------------------------------
# sending
# --------------------------------------------------------------------------


def send(code, host, secret, attributes, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):
    """Send one CoA/Disconnect and wait for the ACK. True, or DynAuthError.

    UDP, so retried: a lost request and a lost reply are indistinguishable from
    here, and both are ordinary on a link to a tower. The identifier is kept
    the same across retries so a NAS that did receive the first one treats the
    rest as duplicates rather than as fresh instructions.
    """
    identifier = secrets.randbelow(256)
    packet, authenticator = _build_packet(code, identifier, attributes, secret)

    last_error = None
    for attempt in range(retries + 1):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (host, port))
            data, _ = sock.recvfrom(4096)
            return _verify_response(data, code, identifier, authenticator, secret)
        except socket.timeout:
            last_error = DynAuthError(
                f"No reply from {host}:{port} after {retries + 1} attempts. "
                f"Either the router isn't accepting dynamic authorization "
                f"(/radius incoming set accept=yes port={port}), or UDP {port} can't reach it."
            )
        except DynAuthError as exc:
            # A NAK is a real answer, not a lost packet -- retrying it just
            # asks the same question again and gets the same no.
            raise exc
        except OSError as exc:
            last_error = DynAuthError(f"Couldn't send to {host}:{port}: {exc}")
        finally:
            sock.close()
        if attempt < retries:
            logger.info("Retrying %s to %s (attempt %d)", code, host, attempt + 2)

    raise last_error


def disconnect(host, secret, username, acct_session_id=None, nas_ip=None, framed_ip=None, **kwargs):
    """End a live session. Used when the customer's ADDRESS has to change."""
    return send(
        CODE_DISCONNECT_REQUEST, host, secret,
        build_attributes(username=username, acct_session_id=acct_session_id,
                         nas_ip=nas_ip, framed_ip=framed_ip),
        **kwargs,
    )


def change_rate_limit(host, secret, username, rate_limit, acct_session_id=None, nas_ip=None,
                      framed_ip=None, **kwargs):
    """Re-program a live session's speed WITHOUT dropping it."""
    return send(
        CODE_COA_REQUEST, host, secret,
        build_attributes(username=username, acct_session_id=acct_session_id, nas_ip=nas_ip,
                         framed_ip=framed_ip, rate_limit=rate_limit),
        **kwargs,
    )


def coa_port():
    """Overridable for a NAS that doesn't use 3799 -- Cisco's 1700, mostly."""
    try:
        return int(os.environ.get("RADIUS_COA_PORT", DEFAULT_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PORT
