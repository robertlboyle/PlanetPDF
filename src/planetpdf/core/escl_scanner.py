"""Scanning over the network with AirScan / eSCL (Mac, 2026-10-14; works on Linux too).

macOS scans network scanners this way itself: the scanner announces itself with Bonjour
(``_uscan._tcp`` for HTTP, ``_uscans._tcp`` for HTTPS, TXT ``rs`` = the path, usually ``eSCL``)
and takes scan jobs over HTTP (the Mopria / Apple eSCL protocol):

* ``GET  <base>/ScannerCapabilities``  what it can do: glass (Platen) and feeder (Adf), duplex,
  color modes, resolutions, largest area;
* ``POST <base>/ScanJobs``             a ``ScanSettings`` document; the answer's ``Location`` is the job;
* ``GET  <job>/NextDocument``          the next page (JPEG): waits while the scanner works; 404 once
  the feeder is empty (or after the one page on the glass); 503 = busy, ask again;
* ``DELETE <job>``                     cancel.

No new library: the Bonjour query is a small multicast DNS client of our own (``discover``), and
the rest is ``urllib`` and ``xml.etree``. Windows keeps its own scanner support (WIA,
``core/windows_scanner.py``).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import random
import socket
import ssl
import threading
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from planetpdf.core.document import PdfError
from planetpdf.core.progress import report

SERVICES = ("_uscan._tcp.local", "_uscans._tcp.local")
MDNS = ("224.0.0.251", 5353)
SCAN_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"
NS = {"scan": SCAN_NS, "pwg": PWG_NS}
LETTER = (2550, 3300)  # in 1/300 inch, eSCL's unit
PAGE_TIMEOUT = 180  # seconds a page may take (warming up, a slow feeder)
BUSY_TRIES = 60  # 503 "busy" answers to wait through, a second apart

# what PlanetPDF calls them -> eSCL's names
SOURCES = {"glass": "Platen", "feeder": "Feeder", "feeder-duplex": "Feeder"}
COLORS = {"color": "RGB24", "gray": "Grayscale8", "bw": "BlackAndWhite1"}
SOURCE_NAMES = {"glass": "Glass (flatbed)", "feeder": "Document feeder", "feeder-duplex": "Document feeder, both sides"}
COLOR_NAMES = {"color": "Color", "gray": "Grayscale", "bw": "Black and white"}


class ScanCanceled(PdfError):
    pass


@dataclass(frozen=True)
class Scanner:
    name: str  # as the scanner announces itself, e.g. "Canon MF741C/743C (ac:6b:a7)"
    url: str  # the eSCL base, e.g. "http://192.168.1.40:80/eSCL"
    model: str = ""

    @property
    def secure(self) -> bool:
        return self.url.startswith("https:")


@dataclass
class Capabilities:
    make_model: str = ""
    version: str = "2.6"
    sources: dict[str, dict] = field(default_factory=dict)  # "glass" / "feeder" / "feeder-duplex" -> caps


# --- finding scanners (multicast DNS) ----------------------------------------------------------

def _encode_name(name: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".") if p) + b"\0"


def _query(questions: list[tuple[str, int]]) -> bytes:
    """A DNS query; class IN with the "answer by unicast" bit (we listen on our own port)."""
    header = struct.pack("!HHHHHH", random.randrange(1 << 16), 0, len(questions), 0, 0, 0)
    return header + b"".join(_encode_name(n) + struct.pack("!HH", t, 0x8001) for n, t in questions)


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    labels, jumped, end, seen = [], False, offset, 0
    while True:
        if offset >= len(data) or seen > 64:
            raise ValueError("bad name")
        length = data[offset]
        if length & 0xC0 == 0xC0:  # a pointer to a name earlier in the message
            if not jumped:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            jumped, seen = True, seen + 1
            continue
        offset += 1
        if length == 0:
            return ".".join(labels), (end if jumped else offset)
        labels.append(data[offset:offset + length].decode("utf-8", "replace"))
        offset += length


def parse_records(data: bytes) -> list[tuple[str, int, object]]:
    """Every answer / authority / additional record: (name, type, value). Values: PTR -> name,
    SRV -> (port, target), TXT -> {key: value}, A -> "a.b.c.d"."""
    _id, _flags, qd, an, ns, ar = struct.unpack("!HHHHHH", data[:12])
    offset = 12
    for _ in range(qd):
        _name, offset = _read_name(data, offset)
        offset += 4
    records = []
    for _ in range(an + ns + ar):
        name, offset = _read_name(data, offset)
        rtype, _cls, _ttl, length = struct.unpack("!HHIH", data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + length]
        if rtype == 12:  # PTR
            value: object = _read_name(data, offset)[0]
        elif rtype == 33:  # SRV
            _prio, _weight, port = struct.unpack("!HHH", rdata[:6])
            value = (port, _read_name(data, offset + 6)[0])
        elif rtype == 16:  # TXT
            value, i = {}, 0
            while i < len(rdata):
                item = rdata[i + 1:i + 1 + rdata[i]].decode("utf-8", "replace")
                i += 1 + rdata[i]
                key, _, val = item.partition("=")
                value[key.lower()] = val
        elif rtype == 1 and length == 4:  # A
            value = socket.inet_ntoa(rdata)
        else:
            value = None
        records.append((name.lower(), rtype, value))
        offset += length
    return records


def local_address() -> str:
    """This computer's address on the network with the default route (no packet is sent)."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # (TEST-NET: only picks the route)
        return probe.getsockname()[0]
    except OSError:
        return "0.0.0.0"
    finally:
        probe.close()


def _ask(questions: list[tuple[str, int]], seconds: float) -> list[tuple[str, int, object]]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    here = local_address()
    if here != "0.0.0.0":  # (macOS has no multicast route of its own: name the interface)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(here))
    sock.bind(("", 0))
    found = []
    try:
        sock.sendto(_query(questions), MDNS)
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                data, _ = sock.recvfrom(9000)
            except (TimeoutError, socket.timeout):
                break
            try:
                found += parse_records(data)
            except (ValueError, struct.error, IndexError):
                continue  # (someone else's odd packet)
    finally:
        sock.close()
    return found


def scanners_from_records(records: list[tuple[str, int, object]]) -> list[Scanner]:
    ptr = {(n, v.lower()) for n, t, v in records if t == 12 and isinstance(v, str)}
    srv = {n: v for n, t, v in records if t == 33}
    txt = {n: v for n, t, v in records if t == 16}
    ips = {n: v for n, t, v in records if t == 1}
    found: dict[str, Scanner] = {}
    for service, instance in sorted(ptr):
        if service not in SERVICES or instance not in srv:
            continue
        port, target = srv[instance]
        ip = ips.get(target.lower())
        if not ip:
            continue
        props = txt.get(instance, {})
        name = instance[: -len(service) - 1] if instance.endswith(service) else instance
        scheme = "https" if service.startswith("_uscans") else "http"
        path = props.get("rs", "eSCL").strip("/")
        model = props.get("ty", "")
        note = props.get("note", "").strip()  # (where it is, e.g. "2nd floor")
        # Instance names grow "(2) (3)…" whenever two devices clash; the model and its note read better
        label = f"{model} ({note})" if model and note else (name or model or ip)
        if label not in found or scheme == "http":  # (both: HTTP first, no certificate questions)
            found[label] = Scanner(label, f"{scheme}://{ip}:{port}/{path}", model)
    return sorted(found.values(), key=lambda s: s.name.lower())


def discover(seconds: float = 3.0) -> list[Scanner]:
    """The AirScan scanners that answer on this network within ``seconds``."""
    records = _ask([(s, 12) for s in SERVICES], seconds)
    scanners = scanners_from_records(records)
    missing = {n for n, t, v in records if t == 12 and isinstance(v, str)} - {s.name for s in scanners}
    if missing and not scanners:  # (some only answer the PTR: ask for the rest)
        names = {v for n, t, v in records if t == 12 and isinstance(v, str)}
        records += _ask([(n, 33) for n in names] + [(n, 16) for n in names], seconds / 2)
        targets = {v[1] for n, t, v in records if t == 33}
        records += _ask([(t, 1) for t in targets], seconds / 2)
        scanners = scanners_from_records(records)
    return scanners


def scanner_at(address: str) -> Scanner:
    """A scanner typed in by hand: "192.168.1.40", "scanner.local" or a full eSCL address."""
    address = address.strip()
    if "://" not in address:
        address = f"http://{address}/eSCL"
    parts = urllib.parse.urlparse(address)
    path = parts.path.strip("/") or "eSCL"
    return Scanner(parts.hostname or address, f"{parts.scheme}://{parts.netloc}/{path}")


# --- talking to a scanner ------------------------------------------------------------------------

# --- a scanner's certificate, remembered the first time (security review 2026-11-25) -------------
# Scanners make their own certificates, which nothing can vouch for. So the first time PlanetPDF
# talks to a scanner over HTTPS, the user is asked (``first_use``) and its certificate's
# fingerprint is remembered (``PINS_FILE``, in the profile); later a different certificate at the
# same address is refused: something may be pretending to be the scanner.

PINS_FILE: Path | None = None  # (the UI sets it: <profile>/scanner-certificates.json)
first_use: Callable[[str, str], bool] | None = None  # (address, fingerprint) -> allow? (UI thread only)
_pins_lock = threading.Lock()


class CertificateChanged(PdfError):
    """A scanner's certificate isn't the one remembered for its address."""


def fingerprint(der: bytes) -> str:
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _pins() -> dict[str, str]:
    try:
        data = json.loads(PINS_FILE.read_text(encoding="utf-8")) if PINS_FILE else {}
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def check_certificate(address: str, der: bytes) -> None:
    """Accepts the scanner at ``address`` ("host:port") only with the certificate remembered for it,
    or, the first time, when the user allows it; otherwise ``CertificateChanged`` / ``PdfError``."""
    seen = fingerprint(der)
    with _pins_lock:
        pins = _pins()
        known = pins.get(address)
        if known == seen:
            return
        if known is not None:
            raise CertificateChanged(
                f"The scanner at {address} answered with a different certificate than before, so PlanetPDF didn't "
                "use it: something on the network may be pretending to be the scanner. If the scanner was reset "
                "or replaced, click Forget Certificates in the scanner window and connect again.")
        if first_use is None or not first_use(address, seen):
            raise PdfError(f"The scanner at {address} wasn't used: its certificate wasn't accepted.")
        pins[address] = seen
        if PINS_FILE is not None:
            PINS_FILE.parent.mkdir(parents=True, exist_ok=True)
            PINS_FILE.write_text(json.dumps(pins, indent=1), encoding="utf-8")


def forget_certificates() -> None:
    """Forgets every remembered scanner certificate (asked again at the next connection)."""
    with _pins_lock:
        if PINS_FILE is not None:
            PINS_FILE.unlink(missing_ok=True)


class _CheckedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS whose certificate is checked on this very connection (not on an earlier one)."""

    def connect(self) -> None:
        super().connect()
        check_certificate(f"{self.host}:{self.port}", self.sock.getpeercert(binary_form=True) or b"")


class _CheckedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_CheckedHTTPSConnection, req, context=self._context)


def _open(request: urllib.request.Request | str, timeout: float):
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    if not url.startswith("https:"):
        return urllib.request.urlopen(request, timeout=timeout)
    context = ssl.create_default_context()  # (encrypted; who it is: its remembered certificate)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(_CheckedHTTPSHandler(context=context))
    return opener.open(request, timeout=timeout)


def _text(element, path: str) -> str:
    found = element.find(path, NS)
    return (found.text or "").strip() if found is not None else ""


MAX_XML = 1 << 20  # (a scanner's capabilities are a few kilobytes)


def _xml(data: bytes):
    """A scanner's XML, parsed. Anything from the network is held to what eSCL needs (security review
    2026-11-21): no DTD or entities (the standard library's parser never fetches outside entities, and
    Python's expat limits entity expansion, but scanner XML has no use for either), and no more than
    ``MAX_XML`` bytes."""
    if len(data) > MAX_XML:
        raise ET.ParseError("the answer is too long")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ET.ParseError("the answer holds a document type definition")
    return ET.fromstring(data)


def parse_capabilities(xml: bytes) -> Capabilities:
    root = _xml(xml)
    caps = Capabilities(make_model=_text(root, "pwg:MakeAndModel"), version=_text(root, "pwg:Version") or "2.6")
    places = {"glass": "scan:Platen/scan:PlatenInputCaps", "feeder": "scan:Adf/scan:AdfSimplexInputCaps",
              "feeder-duplex": "scan:Adf/scan:AdfDuplexInputCaps"}
    for key, path in places.items():
        input_caps = root.find(path, NS)
        if input_caps is None:
            continue
        colors = {m.text.strip() for m in input_caps.iterfind(".//scan:ColorMode", NS) if m.text}
        dpis = {int(r.text) for r in input_caps.iterfind(".//scan:DiscreteResolution/scan:XResolution", NS)
                if r.text and r.text.strip().isdigit()}
        formats = {f.text.strip() for f in input_caps.iterfind(".//pwg:DocumentFormat", NS) if f.text} | \
                  {f.text.strip() for f in input_caps.iterfind(".//scan:DocumentFormatExt", NS) if f.text}
        width = int(_text(input_caps, "scan:MaxWidth") or LETTER[0])
        height = int(_text(input_caps, "scan:MaxHeight") or LETTER[1])
        caps.sources[key] = {"colors": [k for k, v in COLORS.items() if v in colors],
                             "dpis": sorted(dpis), "formats": formats, "max": (width, height)}
    if "feeder" not in caps.sources and root.find("scan:Adf/scan:AdfDuplexInputCaps", NS) is not None:
        caps.sources["feeder"] = dict(caps.sources["feeder-duplex"])
    return caps


def capabilities(scanner: Scanner, timeout: float = 10) -> Capabilities:
    try:
        with _open(f"{scanner.url}/ScannerCapabilities", timeout) as response:
            return parse_capabilities(response.read(MAX_XML + 1))
    except (urllib.error.URLError, OSError) as exc:
        raise PdfError(f"The scanner “{scanner.name}” didn't answer ({_reason(exc)}). Check that it's on.") from exc
    except ET.ParseError as exc:
        raise PdfError(f"The scanner “{scanner.name}” sent an answer PlanetPDF can't read ({exc}).") from exc


def _reason(exc: Exception) -> str:
    return str(getattr(exc, "reason", None) or getattr(exc, "code", None) or exc)


def settings_xml(source: str, color: str, dpi: int, caps: Capabilities, size: tuple[int, int] = LETTER) -> bytes:
    max_w, max_h = caps.sources.get(source, {}).get("max", size)
    width, height = min(size[0], max_w), min(size[1], max_h)
    formats = caps.sources.get(source, {}).get("formats", set())
    fmt = "image/jpeg" if "image/jpeg" in formats or not formats else sorted(formats)[0]
    duplex = "<scan:Duplex>true</scan:Duplex>" if source == "feeder-duplex" else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{SCAN_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:Version>{caps.version}</pwg:Version>
  <pwg:ScanRegions><pwg:ScanRegion>
    <pwg:Height>{height}</pwg:Height><pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
    <pwg:Width>{width}</pwg:Width><pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>
  </pwg:ScanRegion></pwg:ScanRegions>
  <pwg:InputSource>{SOURCES[source]}</pwg:InputSource>{duplex}
  <scan:ColorMode>{COLORS[color]}</scan:ColorMode>
  <scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>
  <pwg:DocumentFormat>{fmt}</pwg:DocumentFormat><scan:DocumentFormatExt>{fmt}</scan:DocumentFormatExt>
</scan:ScanSettings>""".encode()


EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf", "image/tiff": ".tif"}


def scan(scanner: Scanner, folder: Path, *, source: str = "glass", color: str = "color", dpi: int = 300,
         caps: Capabilities | None = None, progress: Callable[..., bool] | None = None,
         first_number: int = 1) -> list[Path]:
    """Scan with ``scanner`` into ``folder``: one page from the glass, every page in the feeder.
    Returns the page files in order. ``progress`` is told "Scanning page N…"; returning False
    cancels the job on the scanner."""
    caps = caps or capabilities(scanner)
    if source not in caps.sources:
        raise PdfError(f"The scanner “{scanner.name}” has no {SOURCE_NAMES[source].lower()}.")
    request = urllib.request.Request(f"{scanner.url}/ScanJobs", data=settings_xml(source, color, dpi, caps),
                                     method="POST", headers={"Content-Type": "text/xml"})
    try:
        with _open(request, 30) as response:
            location = response.headers.get("Location", "")
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise PdfError(f"The scanner “{scanner.name}” says the {SOURCE_NAMES[source].lower()} is empty or busy."
                           ) from exc
        raise PdfError(f"The scanner “{scanner.name}” refused the scan ({exc.code}).") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise PdfError(f"The scanner “{scanner.name}” didn't answer ({_reason(exc)}).") from exc
    if not location:
        raise PdfError(f"The scanner “{scanner.name}” didn't start the scan.")
    job = urllib.parse.urljoin(scanner.url + "/", location).rstrip("/")
    pages: list[Path] = []
    folder.mkdir(parents=True, exist_ok=True)
    busy = 0
    while True:
        number = first_number + len(pages)
        where = "the glass" if source == "glass" else "the feeder"
        if not report(progress, 0, 0, f"Scanning page {number} from {where}…"):
            _cancel(job)
            raise ScanCanceled("Canceled.")
        try:
            with _open(f"{job}/NextDocument", PAGE_TIMEOUT) as response:
                kind = response.headers.get_content_type()
                data = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # (no more pages)
                break
            if exc.code == 503 and busy < BUSY_TRIES:
                busy += 1
                time.sleep(1)
                continue
            if exc.code == 409 and not pages:
                raise PdfError(f"The {SOURCE_NAMES[source].lower()} is empty: put the pages in and scan again.") from exc
            raise PdfError(f"The scanner stopped after {len(pages)} page(s) ({exc.code}).") from exc
        except (urllib.error.URLError, OSError) as exc:
            if pages:
                break  # (some scanners just close the job after the last page)
            raise PdfError(f"The scanner “{scanner.name}” stopped answering ({_reason(exc)}).") from exc
        busy = 0
        page = folder / f"scan-{number:03d}{EXTENSIONS.get(kind, '.jpg')}"
        page.write_bytes(data)
        pages.append(page)
        if source == "glass":
            break
    if not pages:
        raise PdfError(f"The scanner “{scanner.name}” sent no pages.")
    return pages


def _cancel(job: str) -> None:
    try:
        with _open(urllib.request.Request(job, method="DELETE"), 10):
            pass
    except (urllib.error.URLError, OSError):
        pass
