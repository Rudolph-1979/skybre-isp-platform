"""A minimal RFC 5176 NAS, to prove the client's packets are well formed."""
import hashlib, hmac, socket, struct, threading

class FakeNAS:
    def __init__(self, secret, behaviour="ack", error_cause=None):
        self.secret = secret.encode()
        self.behaviour = behaviour
        self.error_cause = error_cause
        self.received = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                return
            code, ident, length = struct.unpack("!BBH", data[:4])
            auth = data[4:20]
            payload = data[20:length]

            # verify the Request Authenticator exactly as a real NAS would
            zero = b"\x00" * 16
            expect = hashlib.md5(data[:4] + zero + payload + self.secret).digest()
            req_auth_ok = hmac.compare_digest(expect, auth)

            # verify Message-Authenticator
            ma_ok = None
            idx, ma_val, ma_off = 0, None, None
            while idx + 2 <= len(payload):
                t, l = payload[idx], payload[idx+1]
                if l < 2: break
                if t == 80:
                    ma_val = payload[idx+2:idx+l]; ma_off = idx+2
                idx += l
            if ma_val is not None:
                blanked = payload[:ma_off] + b"\x00"*16 + payload[ma_off+16:]
                expect_ma = hmac.new(self.secret, data[:4] + zero + blanked, hashlib.md5).digest()
                ma_ok = hmac.compare_digest(expect_ma, ma_val)

            self.received.append({"code": code, "payload": payload,
                                  "req_auth_ok": req_auth_ok, "msg_auth_ok": ma_ok})

            if self.behaviour == "silent":
                continue
            if self.behaviour == "ack":
                rcode = {40: 41, 43: 44}[code]; rpayload = b""
            elif self.behaviour == "nak":
                rcode = {40: 42, 43: 45}[code]
                rpayload = struct.pack("!BBI", 101, 6, self.error_cause) if self.error_cause else b""
            elif self.behaviour == "wrongsecret":
                rcode = {40: 41, 43: 44}[code]; rpayload = b""

            rlen = 20 + len(rpayload)
            rhdr = struct.pack("!BBH", rcode, ident, rlen)
            key = b"wrong-secret" if self.behaviour == "wrongsecret" else self.secret
            rauth = hashlib.md5(rhdr + auth + rpayload + key).digest()
            self.sock.sendto(rhdr + rauth + rpayload, addr)

    def parse_attrs(self, payload):
        out, idx = [], 0
        while idx + 2 <= len(payload):
            t, l = payload[idx], payload[idx+1]
            if l < 2: break
            v = payload[idx+2:idx+l]
            if t == 26:
                vid = struct.unpack("!I", v[:4])[0]
                out.append(("VSA", vid, v[4], v[6:]))
            else:
                out.append((t, v))
            idx += l
        return out
