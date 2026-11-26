"""Network scanning over AirScan / eSCL (Mac and Linux, 2026-10-14), against a stand-in scanner."""

from __future__ import annotations

import io
import socket
import socketserver
import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from PIL import Image

from planetpdf.core import escl_scanner as E

CAPS = f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities xmlns:scan="{E.SCAN_NS}" xmlns:pwg="{E.PWG_NS}">
  <pwg:Version>2.63</pwg:Version><pwg:MakeAndModel>Canon MF741C/743C</pwg:MakeAndModel>
  <scan:Platen><scan:PlatenInputCaps>
    <scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>3508</scan:MaxHeight>
    <scan:SettingProfiles><scan:SettingProfile>
      <scan:ColorModes><scan:ColorMode>RGB24</scan:ColorMode><scan:ColorMode>Grayscale8</scan:ColorMode></scan:ColorModes>
      <scan:DocumentFormats><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat><pwg:DocumentFormat>application/pdf</pwg:DocumentFormat></scan:DocumentFormats>
      <scan:SupportedResolutions><scan:DiscreteResolutions>
        <scan:DiscreteResolution><scan:XResolution>300</scan:XResolution><scan:YResolution>300</scan:YResolution></scan:DiscreteResolution>
        <scan:DiscreteResolution><scan:XResolution>600</scan:XResolution><scan:YResolution>600</scan:YResolution></scan:DiscreteResolution>
      </scan:DiscreteResolutions></scan:SupportedResolutions>
    </scan:SettingProfile></scan:SettingProfiles>
  </scan:PlatenInputCaps></scan:Platen>
  <scan:Adf><scan:AdfSimplexInputCaps>
    <scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>4200</scan:MaxHeight>
    <scan:SettingProfiles><scan:SettingProfile>
      <scan:ColorModes><scan:ColorMode>RGB24</scan:ColorMode></scan:ColorModes>
      <scan:SupportedResolutions><scan:DiscreteResolutions>
        <scan:DiscreteResolution><scan:XResolution>300</scan:XResolution><scan:YResolution>300</scan:YResolution></scan:DiscreteResolution>
      </scan:DiscreteResolutions></scan:SupportedResolutions>
    </scan:SettingProfile></scan:SettingProfiles>
  </scan:AdfSimplexInputCaps></scan:Adf>
</scan:ScannerCapabilities>""".encode()


class _Server(HTTPServer):
    def server_bind(self):  # (HTTPServer looks up this computer's full name: up to 35 s on a Mac)
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def _jpeg(color: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (85, 110), color).save(out, "JPEG")
    return out.getvalue()


class FakeScanner:
    """A stand-in eSCL scanner: the feeder holds ``pages``; it's "busy" once before the first."""

    def __init__(self, pages: list[str]) -> None:
        self.pages, self.jobs, self.settings, self.deleted, self.busy_once = list(pages), 0, [], [], True
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/eSCL/ScannerCapabilities":
                    return self._send(200, CAPS, "text/xml")
                if self.path.endswith("/NextDocument"):
                    if owner.busy_once:
                        owner.busy_once = False
                        return self._send(503, b"")
                    if not owner.pages:
                        return self._send(404, b"")
                    return self._send(200, _jpeg(owner.pages.pop(0)), "image/jpeg")
                self._send(404, b"")

            def do_POST(self):
                owner.settings.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
                owner.jobs += 1
                self.send_response(201)
                self.send_header("Location", f"/eSCL/ScanJobs/job{owner.jobs}")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_DELETE(self):
                owner.deleted.append(self.path)
                self._send(200, b"")

            def _send(self, code, body, kind="text/plain"):
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = _Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.scanner = E.Scanner("Test Scanner", f"http://127.0.0.1:{self.server.server_port}/eSCL")

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake():
    scanner = FakeScanner(["red", "green", "blue"])
    yield scanner
    scanner.close()


def test_capabilities(fake):
    caps = E.capabilities(fake.scanner)
    assert caps.make_model == "Canon MF741C/743C" and caps.version == "2.63"
    assert caps.sources["glass"]["colors"] == ["color", "gray"] and caps.sources["glass"]["dpis"] == [300, 600]
    assert set(caps.sources) == {"glass", "feeder"}


def test_glass_gives_one_page(fake, tmp_path):
    stages = []
    pages = E.scan(fake.scanner, tmp_path, source="glass", color="gray", dpi=600,
                   progress=lambda done, total, stage="": stages.append(stage) or True)
    assert [p.name for p in pages] == ["scan-001.jpg"]
    assert Image.open(pages[0]).size == (85, 110)
    sent = fake.settings[0]
    assert "<pwg:InputSource>Platen</pwg:InputSource>" in sent and "Grayscale8" in sent and ">600<" in sent
    assert "<pwg:Height>3300</pwg:Height>" in sent  # Letter, within the glass
    assert stages and stages[0] == "Scanning page 1 from the glass…"  # (waited through one "busy")


def test_feeder_gives_every_page_and_numbers_go_on(fake, tmp_path):
    pages = E.scan(fake.scanner, tmp_path, source="feeder", first_number=4)
    assert [p.name for p in pages] == ["scan-004.jpg", "scan-005.jpg", "scan-006.jpg"]
    assert "<pwg:InputSource>Feeder</pwg:InputSource>" in fake.settings[0]


def test_cancel_deletes_the_job(fake, tmp_path):
    with pytest.raises(E.ScanCanceled):
        E.scan(fake.scanner, tmp_path, source="feeder", progress=lambda *a: False)
    assert fake.deleted == ["/eSCL/ScanJobs/job1"]


def test_a_missing_source_or_scanner_is_explained(fake, tmp_path):
    caps = E.capabilities(fake.scanner)
    del caps.sources["feeder"]
    with pytest.raises(E.PdfError, match="has no document feeder"):
        E.scan(fake.scanner, tmp_path, source="feeder", caps=caps)
    gone = E.Scanner("Gone", "http://127.0.0.1:9/eSCL")
    with pytest.raises(E.PdfError, match="didn't answer"):
        E.capabilities(gone, timeout=2)


def test_scanner_by_address():
    assert E.scanner_at("192.168.1.40").url == "http://192.168.1.40/eSCL"
    assert E.scanner_at("https://scan.local:8443/escl/").url == "https://scan.local:8443/escl"


def _name(text: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in text.split(".")) + b"\0"


def _record(name: bytes, rtype: int, rdata: bytes) -> bytes:
    return name + struct.pack("!HHIH", rtype, 0x8001, 120, len(rdata)) + rdata


def test_bonjour_answer_becomes_a_scanner():
    """The records one of the office's Canons sends (2026-10-14), as one mDNS answer."""
    instance = "Canon MF741C/743C (a9:e2:f3) (2)._uscan._tcp.local"
    txt = b"".join(bytes([len(s)]) + s for s in (b"txtvers=1", b"ty=Canon MF741C/743C", b"note=4PP - NOC",
                                                    b"rs=eSCL", b"is=platen,adf", b"duplex=T"))
    records = [
        _record(_name("_uscan._tcp.local"), 12, _name(instance)),
        _record(_name(instance), 33, struct.pack("!HHH", 0, 0, 80) + _name("Canona9e2f3.local")),
        _record(_name(instance), 16, txt),
        _record(_name("Canona9e2f3.local"), 1, socket.inet_aton("10.69.68.48")),
    ]
    packet = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 3) + b"".join(records)
    [scanner] = E.scanners_from_records(E.parse_records(packet))
    assert scanner.name == "Canon MF741C/743C (4PP - NOC)"
    assert scanner.url == "http://10.69.68.48:80/eSCL" and scanner.model == "Canon MF741C/743C"


def test_names_compressed_in_the_answer_are_read():
    header = struct.pack("!HHHHHH", 0, 0x8400, 0, 1, 0, 0)
    first = _name("_uscan._tcp.local")
    pointer = struct.pack("!H", 0xC000 | 12)  # (back to the name at offset 12)
    rdata = bytes([4]) + b"Scan" + pointer
    packet = header + first + struct.pack("!HHIH", 12, 1, 120, len(rdata)) + rdata
    [(name, rtype, value)] = E.parse_records(packet)
    assert (name, rtype, value) == ("_uscan._tcp.local", 12, "Scan._uscan._tcp.local")


def test_scanner_xml_with_a_document_type_is_refused():
    """Security review 2026-11-21: XML from the network holds no DTD or entities."""
    import xml.etree.ElementTree as ET

    from planetpdf.core import escl_scanner as E

    bomb = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]>'
            b'<scan:ScannerCapabilities xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">&b;'
            b'</scan:ScannerCapabilities>')
    with pytest.raises(ET.ParseError, match="document type"):
        E.parse_capabilities(bomb)
    with pytest.raises(ET.ParseError, match="too long"):
        E.parse_capabilities(b"<a>" + b" " * (E.MAX_XML + 1) + b"</a>")


def _https_scanner(tmp_path, name):
    """A stand-in scanner over HTTPS with a self-made certificate; returns (address, stop)."""
    import http.server
    import ssl
    import threading

    from cryptography.hazmat.primitives import serialization

    from tests.fixtures.certs import _self_signed

    key, cert = _self_signed(name)
    pem = tmp_path / f"{name}.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pem)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"https://127.0.0.1:{server.server_address[1]}/eSCL"


def test_a_scanners_certificate_is_remembered_and_a_new_one_refused(tmp_path, monkeypatch):
    """Security review 2026-11-25 (earlier finding 9): scanner HTTPS took any certificate."""
    from planetpdf.core import escl_scanner as E

    monkeypatch.setattr(E, "PINS_FILE", tmp_path / "scanner-certificates.json")
    asked = []
    monkeypatch.setattr(E, "first_use", lambda address, fp: asked.append(address) or True)
    first, url = _https_scanner(tmp_path, "Scanner One")
    with E._open(url, 5) as response:
        assert response.read() == b"ok"
    with E._open(url, 5) as response:  # (the second time: not asked again)
        response.read()
    assert len(asked) == 1
    port = first.server_address[1]
    first.shutdown()
    first.server_close()
    # something else answers at the same address with its own certificate
    import http.server
    import ssl
    import threading

    impostor, _url = _https_scanner(tmp_path, "Impostor")
    impostor.shutdown()
    impostor.server_close()
    impostor = http.server.HTTPServer(("127.0.0.1", port), impostor.RequestHandlerClass)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "Impostor.pem")
    impostor.socket = context.wrap_socket(impostor.socket, server_side=True)
    threading.Thread(target=impostor.serve_forever, daemon=True).start()
    try:
        with pytest.raises(E.CertificateChanged):
            E._open(url, 5)
        E.forget_certificates()  # (the scanner was replaced: asked again)
        with E._open(url, 5) as response:
            response.read()
        assert len(asked) == 2
    finally:
        impostor.shutdown()
        impostor.server_close()


def test_refused_when_nobody_is_there_to_ask(tmp_path, monkeypatch):
    from planetpdf.core import escl_scanner as E
    from planetpdf.core.document import PdfError

    monkeypatch.setattr(E, "PINS_FILE", tmp_path / "pins.json")
    monkeypatch.setattr(E, "first_use", None)
    server, url = _https_scanner(tmp_path, "Lonely")
    try:
        with pytest.raises(PdfError, match="wasn't accepted"):
            E._open(url, 5)
    finally:
        server.shutdown()
        server.server_close()
