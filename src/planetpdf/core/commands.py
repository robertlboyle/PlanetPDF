"""Every PlanetPDF function as a command, for scripts and other programs (user 2026-10-18:
"everything we do should be scriptable … extensible with an API … a plugin architecture").

One list, used by the command line (``planetpdf.cli``: ``planetpdf <command> …``) and the local
API (``ui/api_server.py``, JSON-RPC: method = the command's name, params = its parameters). Each
command names the permission it needs (``READ``, ``WRITE``, ``SIGN``); signing always needs the
user's approval, given through ``Context.approve_signing`` (user 2026-10-18: "The script running
as me would need access to be approved to use any digital signatures. Other functions are fine").

Commands work on files: they open the file, do the work with the same core code the menus use,
and write the result to ``out`` (never over an existing file unless ``overwrite`` is set; the
input file itself only when ``out`` names it). Results are plain JSON-able dicts. Pages are
typed as in the program ("1-3, 7, 10-"), counted from 1. Long work reports
``progress(done, total, stage)`` with real numbers (``core.progress``).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from planetpdf.core.document import PdfError, PdfSession, Protection
from planetpdf.core.pagerange import parse_page_ranges
from planetpdf.core.progress import Progress, report

READ, WRITE, SIGN = "read", "write", "sign"
PERMISSIONS = {
    READ: "Read documents: details, text, form fields, searches, and checks",
    WRITE: "Make new documents and change documents",
    SIGN: "Digitally sign documents with your Digital IDs",
}


class CommandError(PdfError):
    """A command was used wrongly (unknown name, missing or bad parameter)."""


class NotApproved(PdfError):
    """The user didn't approve signing."""


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # file, files, out, folder, pages, text, texts, int, number, flag, choice, secret, json
    help: str
    required: bool = False
    default: object = None
    choices: tuple = ()
    positional: bool = False  # the command line takes it without --name

    def describe(self) -> dict:
        d = {"name": self.name, "type": self.kind, "help": self.help, "required": self.required}
        if self.default is not None:
            d["default"] = self.default
        if self.choices:
            d["choices"] = list(self.choices)
        return d


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    permission: str
    group: str
    params: tuple[Param, ...]
    run: Callable[[dict, Context], dict] = field(repr=False, compare=False)

    def describe(self) -> dict:
        return {"name": self.name, "summary": self.summary, "group": self.group, "permission": self.permission,
                "params": [p.describe() for p in self.params]}


@dataclass
class Context:
    """What a command may use besides its parameters."""

    progress: Progress | None = None
    # asked before anything is signed, with a description of what will be signed; True = go ahead.
    # None = refuse (a caller that can't ask the user can't sign).
    approve_signing: Callable[[str], bool] | None = None
    profile_root: Path | None = None  # the user's PlanetPDF profile (trusted certificates)
    trust_system_roots: bool = True

    def tick(self, done: int, total: int, stage: str = "") -> bool:
        return report(self.progress, done, total, stage)


COMMANDS: dict[str, Command] = {}

# parameters many commands share
FILE = Param("file", "file", "The PDF file", required=True, positional=True)
OUT = Param("out", "out", "Where to write the result", required=True)
NO_PROTECT = Param("no_protect", "flag", "Write the new file without the input's password (by default a "
                   "password-protected input's new files keep it)")
PASSWORD = Param("password", "secret", "The file's password, if it has one")
PAGES = Param("pages", "pages", 'Which pages, e.g. "1-3, 7, 10-" (default: every page)')
OVERWRITE = Param("overwrite", "flag", "Replace the output file if it exists")


def command(name: str, summary: str, permission: str, group: str, *params: Param):
    def register(run):
        COMMANDS[name] = Command(name, summary, permission, group, tuple(params), run)
        return run
    return register


def describe() -> list[dict]:
    return [c.describe() for c in COMMANDS.values()]


# --- running ----------------------------------------------------------------------------------


def _convert(p: Param, value):
    if value is None:
        return None
    try:
        if p.kind in ("file", "out", "folder"):
            return Path(str(value)).expanduser()
        if p.kind == "files":
            items = [value] if isinstance(value, (str, Path)) else list(value)
            return [Path(str(v)).expanduser() for v in items]
        if p.kind == "texts":
            return [str(v) for v in ([value] if isinstance(value, str) else value)]
        if p.kind == "int":
            return int(value)
        if p.kind == "number":
            return float(value)
        if p.kind == "flag":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if p.kind == "choice":
            value = str(value).lower()
            if value not in p.choices:
                raise CommandError(f"{p.name} must be one of: {', '.join(p.choices)}.")
            return value
        if p.kind == "json":
            return json.loads(value) if isinstance(value, str) else value
        return str(value)  # text, secret, pages
    except (TypeError, ValueError) as e:
        raise CommandError(f"{p.name}: {e}") from None


def prepare(cmd: Command, raw: dict) -> dict:
    """``raw`` (names may use - or _) checked and converted to the parameters' types."""
    raw = {str(k).replace("-", "_"): v for k, v in (raw or {}).items()}
    known = {p.name.replace("-", "_") for p in cmd.params}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise CommandError(f"{cmd.name} has no parameter {', '.join(unknown)}.")
    args = {}
    for p in cmd.params:
        key = p.name.replace("-", "_")
        value = _convert(p, raw.get(key))
        if value is None or value == []:
            if p.required:
                raise CommandError(f"{cmd.name} needs {p.name}.")
            value = p.default
        args[key] = value
    return args


def run(name: str, raw: dict | None = None, context: Context | None = None) -> dict:
    cmd = COMMANDS.get(name)
    if cmd is None:
        raise CommandError(f"There's no command {name!r}.")
    return cmd.run(prepare(cmd, raw or {}), context or Context())


# --- helpers ------------------------------------------------------------------------------------


def _open(args: dict) -> PdfSession:
    path = args["file"]
    if not path.is_file():
        raise PdfError(f"There's no file {path}.")
    return PdfSession.open(path, args.get("password") or None)


def _pages(args: dict, count: int, key: str = "pages") -> list[int]:
    text = args.get(key)
    return list(range(count)) if not text else parse_page_ranges(text, count)


def _kept_protection(session: PdfSession, args: dict, result: dict | None = None) -> Protection | None:
    """The input's password protection, for a new file made from it, unless --no-protect (security
    review 2026-11-25: redacted and compressed copies of a protected PDF came out unprotected).
    A file that only limits what may be done (no password to open it) can't be given the same
    limits again without its permissions password: ``result`` then gets a note saying so."""
    if args.get("no_protect"):
        return None
    known, _complete = session.known_protection()
    if known is None:
        return None
    try:
        known.validate()
    except PdfError:
        if result is not None:
            result["note"] = ("The input's permission limits weren't kept: they need its permissions password, "
                              "which PlanetPDF doesn't know.")
        return None
    return known


def _target(args: dict, key: str = "out", *, replaces_input: bool = True) -> Path:
    """The output path, refused if it exists (unless overwrite, or it's the input itself). A
    command whose result isn't the PDF itself (text, Word, pictures, a report) never writes over
    an input (security review 2026-11-25: ``text --out`` the PDF replaced it with its text)."""
    target = Path(args[key]).resolve()
    inputs = [Path(args[k]).resolve() for k in ("file", "old", "new") if args.get(k)]
    if not replaces_input and target in inputs:
        raise CommandError(f"{key}: that's the PDF being read; choose another name for the new file.")
    source = args.get("file")
    same = source is not None and Path(source).resolve() == target
    if target.exists() and not same and not args.get("overwrite"):
        raise PdfError(f"{target} already exists (overwrite replaces it).")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _save(session: PdfSession, args: dict) -> dict:
    target = _target(args)
    session.save_as(target)
    return {"output": str(target), "pages": session.page_count}


def _done(session: PdfSession, args: dict, **extra) -> dict:
    try:
        result = _save(session, args)
    finally:
        session.close()
    result.update(extra)
    return result


def _pairs(items: list[str] | None, what: str) -> dict[str, str]:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise CommandError(f'{what}: write each one as name=value ("{item}" has no =).')
        key, value = item.split("=", 1)
        out[key.strip()] = value
    return out


def _color(text: str | None, default):
    if not text:
        return default
    text = text.strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    names = {"black": (0, 0, 0), "white": (1, 1, 1), "red": (0.8, 0.1, 0.1), "gray": (0.5, 0.5, 0.5),
             "blue": (0.1, 0.2, 0.7)}
    if text.lower() in names:
        return names[text.lower()]
    raise CommandError(f"Color {text!r}: use a name (black, white, red, gray, blue) or hex like #cc1111.")


# --- reading ------------------------------------------------------------------------------------


@command("info", "Details of a PDF: pages, size, metadata, protection, signatures, form fields", READ, "Read",
         FILE, PASSWORD)
def _info(args, ctx):
    from planetpdf.core import properties

    session = _open(args)
    try:
        facts = asdict(properties.facts(session))
        facts["metadata"] = properties.get_metadata(session)
        return facts
    finally:
        session.close()


@command("text", "The text of a PDF's pages, in reading order", READ, "Read",
         FILE, PASSWORD, PAGES, Param("out", "out", "Write it to this text file instead of returning it"), OVERWRITE)
def _text(args, ctx):
    session = _open(args)
    try:
        pages = _pages(args, session.page_count)
        parts = []
        for n, index in enumerate(pages):
            ctx.tick(n, len(pages), "Reading page")
            parts.append(session.doc[index].get_text(sort=True))
        text = "\f".join(parts)
    finally:
        session.close()
    if args.get("out"):
        target = _target(args, replaces_input=False)
        target.write_text(text, encoding="utf-8")
        return {"output": str(target), "pages": len(pages)}
    return {"text": text, "pages": len(pages)}


def _pattern(args):
    from planetpdf.core.redaction import patterns, search

    found = [(search.build_pattern(t, regex=args.get("regex", False), case_sensitive=args.get("match_case", False),
                                   whole_word=args.get("whole_word", False)), t) for t in args.get("find") or []]
    if args.get("preset"):
        preset = patterns.PRESETS[args["preset"]]
        found.append((preset.pattern, preset.label))
    if not found:
        raise CommandError("Give the words to find (find), a preset, or both.")
    return found


FIND_PARAMS = (
    Param("find", "texts", "Words or phrases to find (several allowed)"),
    Param("preset", "choice", "A ready-made pattern instead", choices=("ssn", "phone", "email", "account")),
    Param("regex", "flag", "The words are regular expressions"),
    Param("match_case", "flag", "Match upper and lower case exactly"),
    Param("whole_word", "flag", "Only whole words"),
)


@command("search", "Find words, phrases, or patterns (SSNs, phone numbers…) in a PDF", READ, "Read",
         FILE, PASSWORD, PAGES, *FIND_PARAMS)
def _search(args, ctx):
    from planetpdf.core.redaction import search

    session = _open(args)
    try:
        pages = _pages(args, session.page_count)
        hits = []
        for pattern, _label in _pattern(args):
            for hit in search.search_pages(session, pattern, pages):
                hits.append({"page": hit.page + 1, "text": hit.text, "context": hit.context,
                             "rects": [list(r) for r in hit.rects]})
        return {"matches": len(hits), "hits": hits}
    finally:
        session.close()


@command("fields", "A form's fields: names, kinds, values, and choices", READ, "Forms", FILE, PASSWORD)
def _fields(args, ctx):
    from planetpdf.core.forms import fields

    session = _open(args)
    try:
        out = []
        for f in fields.list_fields(session):
            value = f.value if isinstance(f.value, (str, int, float, bool, type(None), list)) else str(f.value)
            out.append({"name": f.name, "kind": f.kind.name.lower(), "value": value, "page": f.first_page + 1,
                        "choices": list(f.options), "read_only": f.read_only, "required": f.required})
        return {"fields": out}
    finally:
        session.close()


@command("bookmarks", "A PDF's bookmarks", READ, "Read", FILE, PASSWORD)
def _bookmarks(args, ctx):
    from planetpdf.core import links

    session = _open(args)
    try:
        return {"bookmarks": [{"level": b.level, "title": b.title, "page": b.page + 1}
                              for b in links.get_bookmarks(session)]}
    finally:
        session.close()


def _trust(ctx: Context):
    from planetpdf.core.signing.providers import system_store
    from planetpdf.core.signing.providers.base import CombinedTrustStore
    from planetpdf.core.signing.providers.pkcs12 import FileTrustStore

    stores = []
    if ctx.profile_root is not None:
        from planetpdf.core.storage.profile import Profile

        stores.append(FileTrustStore(Profile(ctx.profile_root).trusted_certs_dir))
    if ctx.trust_system_roots and system_store.available():
        stores.append(system_store.trust_store())
    return CombinedTrustStore(*stores) if stores else None


@command("signatures", "Check a PDF's digital signatures", READ, "Sign", FILE,
         Param("check_revocation", "flag", "Also ask online whether the signers' certificates were revoked"))
def _signatures(args, ctx):
    from planetpdf.core.signing.digital import validate_signatures

    if not args["file"].is_file():
        raise PdfError(f"There's no file {args['file']}.")
    out = []
    for s in validate_signatures(args["file"], _trust(ctx), check_revocation=args["check_revocation"]):
        out.append({"field": s.field_name, "signer": s.signer,
                    "signed": s.signing_time.isoformat() if s.signing_time else None,
                    "timestamped": s.timestamped, "intact": s.intact, "trusted": s.trusted,
                    "covers_whole_file": s.covers_whole_file, "changes_after": s.changes_after,
                    "verdict": s.verdict.name.lower(), "summary": s.summary, "certified": s.certified,
                    "revocation_checked": s.revocation_checked, "revoked": s.revoked})
    return {"signatures": out}


@command("signing-ids", "The Digital IDs in Windows' certificate store or the Mac Keychain that can sign", READ,
         "Sign")
def _signing_ids(args, ctx):
    from planetpdf.core.signing.providers import system_store

    if not system_store.available():
        return {"ids": []}
    return {"ids": [{"id": c.thumbprint, "name": c.name, "issuer": c.issuer, "expires": c.expires.isoformat()}
                    for c in system_store.signing_certificates()]}


STANDARD = Param("standard", "choice", "pdfa (PDF/A-2b), pdfx (PDF/X-3) or pdfe (PDF/E-1)", required=True,
                 choices=("pdfa", "pdfx", "pdfe"))


@command("check-compliance", "Check a PDF against PDF/A, PDF/X, or PDF/E", READ, "Standards",
         FILE, PASSWORD, STANDARD)
def _check(args, ctx):
    from planetpdf.core import compliance

    session = _open(args)
    try:
        result = compliance.check(session.doc, args["standard"], ctx.progress)
    finally:
        session.close()
    return {"passed": result.passed, "complete": result.complete, "claimed": result.claimed,
            "checks": [{"title": c.title, "status": c.status, "details": c.details,
                        "pages": [p + 1 for p in c.pages], "fix": c.fix} for c in result.checks]}


@command("space-usage", "What takes the room in a PDF (pictures, fonts, content…)", READ, "Optimize",
         FILE, PASSWORD)
def _space(args, ctx):
    from planetpdf.core import optimize

    session = _open(args)
    try:
        usage = optimize.space_usage(session.doc)
    finally:
        session.close()
    return {"total": usage.total, "scanned_pages": usage.scanned_pages,
            "parts": [{"what": w, "bytes": b, "percent": round(p, 1)} for w, b, p in usage.rows()]}


@command("compare", "Compare the text of two PDFs", READ, "Read",
         Param("old", "file", "The older version", required=True, positional=True),
         Param("new", "file", "The newer version", required=True, positional=True),
         Param("report", "out", "Also write a comparison report (PDF) here"), OVERWRITE)
def _compare(args, ctx):
    import pymupdf

    from planetpdf.core import compare

    with pymupdf.open(args["old"]) as old, pymupdf.open(args["new"]) as new:
        result = compare.compare(old, new, ctx.progress)
        out = {"identical": result.identical, "summary": result.summary(), "counts": result.counts(),
               "changes": [{"kind": c.kind, "old": c.old_text, "new": c.new_text,
                            "old_page": None if c.old_page is None else c.old_page + 1}
                           for c in result.changes]}
        if args.get("report"):
            target = _target(args, "report", replaces_input=False)
            compare.comparison_report(old, new, result, target, progress=ctx.progress)
            out["report"] = str(target)
    return out


# --- making and changing documents -------------------------------------------------------------


EXPORT_FORMATS = ("pdf", "png", "jpg", "tiff", "tiff_multi", "image_pdf", "docx", "doc", "rtf", "txt",
                  "txt_accessible", "xlsx", "xml_spreadsheet", "pptx", "html", "xml", "ps", "eps", "jpx")


@command("export", "Save a PDF's pages as Word, Excel, PowerPoint, pictures, text, HTML…", WRITE, "Export",
         FILE, OUT, PASSWORD, PAGES,
         Param("format", "choice", "What to make", required=True, choices=EXPORT_FORMATS),
         Param("dpi", "int", "Picture resolution", default=150),
         Param("quality", "int", "JPEG quality, 1-100", default=85),
         Param("ocr", "flag", "Recognize text on scanned pages first (text, RTF, Word)"),
         Param("ai", "flag", "Read the pages with the AI text reader, Unlimited-OCR (Word, RTF, text, HTML; needs "
               "the optional AI component and an NVIDIA graphics card)"), NO_PROTECT, OVERWRITE)
def _export(args, ctx):
    from planetpdf.core import export, pages as page_ops

    session = _open(args)
    try:
        pages = _pages(args, session.page_count)
        fmt = export.ExportFormat(args["format"])
        target = _target(args, replaces_input=fmt in (export.ExportFormat.PDF, export.ExportFormat.IMAGE_PDF))
        result: dict = {}
        protection = _kept_protection(session, args, result)
        if fmt is export.ExportFormat.PDF:
            page_ops.export_pages(session, pages, target, protection)
            return {"outputs": [str(target)], "pages": len(pages), **result}
        if fmt is export.ExportFormat.IMAGE_PDF and protection is not None:
            export.export_image_pdf(session.doc, pages, target, export.ExportOptions(dpi=args["dpi"],
                                    jpeg_quality=args["quality"]), ctx.progress, protection)
            return {"outputs": [str(target)], "pages": len(pages), **result}
        if args["ai"]:
            from planetpdf.core import ai_ocr

            ready = ai_ocr.status()
            if not ready.ready:
                raise PdfError(ready.reason)
        options = export.ExportOptions(dpi=args["dpi"], jpeg_quality=args["quality"], ocr_missing_text=args["ocr"],
                                       ai_reading=args["ai"])
        notes: list[str] = []
        files = export.export(session.doc, pages, target, fmt, options, ctx.progress, notes)
        return {"outputs": [str(f) for f in files], "pages": len(pages), "notes": notes}
    finally:
        session.close()


@command("ocr", "Recognize text on scanned pages, so they can be searched and copied", WRITE, "Scan",
         FILE, OUT, PASSWORD, PAGES,
         Param("language", "text", "Tesseract language", default="eng"),
         Param("dpi", "int", "Resolution to read the pages at", default=300),
         Param("all", "flag", "Also pages that already have text"), OVERWRITE)
def _ocr(args, ctx):
    import pymupdf

    from planetpdf.core import ocr

    session = _open(args)
    try:
        pages = _pages(args, session.page_count)
        if not args["all"]:
            pages = [p for p in pages if ocr.page_needs_ocr(session.doc, p)]
        data = session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)
        results = ocr.recognize_pages(data, pages, language=args["language"], dpi=args["dpi"],
                                      progress=lambda d, t: ctx.tick(d, t, "Recognizing text on page"))
        words = 0
        for index, found in results.items():
            words += ocr.insert_ocr_text(session, index, found)
        session.mark_modified()
        return _done(session, args, recognized_pages=len(results), words=words)
    except BaseException:
        session.close()
        raise


QUALITY = {"high": (200, 85), "balanced": (150, 75), "small": (100, 60)}


@command("compress", "Make a PDF smaller (pictures resampled, scans in black and white or gray, fonts cut down)",
         WRITE, "Optimize", FILE, OUT, PASSWORD,
         Param("quality", "choice", "high, balanced, or small", default="balanced", choices=tuple(QUALITY)),
         Param("keep_scan_colors", "flag", "Don't turn scans into black and white or gray"), OVERWRITE)
def _compress(args, ctx):
    import pymupdf

    from planetpdf.core import optimize

    session = _open(args)
    try:
        dpi, quality = QUALITY[args["quality"]]
        options = optimize.ReduceOptions(image_dpi=dpi, jpeg_quality=quality,
                                         scans_black_white=not args["keep_scan_colors"],
                                         scans_gray=not args["keep_scan_colors"])
        before = session.path.stat().st_size
        extra: dict = {}
        protection = _kept_protection(session, args, extra)
        doc = pymupdf.open(stream=session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE), filetype="pdf")
        target = _target(args)
    finally:
        session.close()
    try:
        result = optimize.reduce_size(doc, target, options, before, ctx.progress, protection)
    finally:
        doc.close()
    return {"output": str(target), "before": result.before, "after": result.after,
            "saved_percent": round(result.saved_percent, 1), "black_and_white": result.black_white,
            "gray": result.gray, "protected": protection is not None, **extra}


@command("optimize-scans", "Straighten, clean, and shrink scanned pages", WRITE, "Scan",
         FILE, OUT, PASSWORD, PAGES,
         Param("color", "choice", "auto, bw, gray, color, or keep", default="auto",
               choices=("auto", "bw", "gray", "color", "keep")), OVERWRITE)
def _optimize_scans(args, ctx):
    from planetpdf.core import scan_cleanup

    session = _open(args)
    try:
        pages = _pages(args, session.page_count)
        report_ = scan_cleanup.cleanup_pages(session.doc, pages, scan_cleanup.CleanupOptions(color=args["color"]),
                                             ctx.progress)
        session.mark_modified()
        return _done(session, args, cleaned=[p + 1 for p in report_.changed],
                     not_scanned=[p + 1 for p in report_.not_scanned],
                     turned={str(p + 1): d for p, d in report_.turned.items()},
                     straightened={str(p + 1): round(d, 2) for p, d in report_.straightened.items()},
                     colors={str(p + 1): m for p, m in report_.modes.items()})
    except BaseException:
        session.close()
        raise


@command("remove-blank-pages", "Remove blank pages (scans' empty backs)", WRITE, "Scan", FILE, OUT, PASSWORD,
         OVERWRITE)
def _blank(args, ctx):
    from planetpdf.core import pages as page_ops, scan_cleanup

    session = _open(args)
    try:
        blank = scan_cleanup.find_blank_pages(session.doc, None, ctx.progress)
        if len(blank) == session.page_count:
            raise PdfError("Every page is blank: nothing would be left.")
        if blank:
            page_ops.delete_pages(session, blank)
        return _done(session, args, removed=[p + 1 for p in blank])
    except BaseException:
        session.close()
        raise


@command("combine", "Combine PDFs and pictures into one PDF, in the order given", WRITE, "Create",
         Param("files", "files", "The files", required=True, positional=True), OUT,
         Param("image_page", "choice", "Pages for pictures: image (its size), letter, or a4", default="image",
               choices=("image", "letter", "a4")), OVERWRITE)
def _combine(args, ctx):
    from planetpdf.core import combine

    target = _target(args)
    count = combine.combine_files(args["files"], target, combine.CombineOptions(image_page=args["image_page"]),
                                  ctx.progress)
    return {"output": str(target), "pages": count}


@command("create", "Make a PDF from each file (pictures, text, Office files, XPS, EPUB…)", WRITE, "Create",
         Param("files", "files", "The files", required=True, positional=True),
         Param("folder", "folder", "Where to put the PDFs", required=True))
def _create(args, ctx):
    from planetpdf.core import batch

    results = batch.create_multiple(args["files"], args["folder"], ctx.progress)
    return {"files": [{"source": str(r.source), "output": str(r.output) if r.output else None, "error": r.error}
                      for r in results]}


@command("web-page", "Make a PDF of a web page (through Microsoft Edge or Chrome)", WRITE, "Create",
         Param("address", "text", "The page's address", required=True, positional=True), OUT, OVERWRITE)
def _web(args, ctx):
    from planetpdf.core import web_capture

    target = _target(args)
    web_capture.web_page_to_pdf(args["address"], target)
    return {"output": str(target)}


@command("split", "Split a PDF into several files", WRITE, "Pages",
         FILE, PASSWORD, Param("folder", "folder", "Where to put the files", required=True),
         Param("every", "int", "A new file every N pages"),
         Param("ranges", "texts", 'Page ranges, one file each, e.g. "1-3" "4-9"'),
         Param("bookmarks", "flag", "One file per top-level bookmark"), NO_PROTECT)
def _split(args, ctx):
    from planetpdf.core import pages as page_ops

    session = _open(args)
    try:
        count = session.page_count
        if args.get("every"):
            groups = [("", g) for g in page_ops.split_groups_every(count, args["every"])]
        elif args.get("ranges"):
            groups = [("", parse_page_ranges(r, count)) for r in args["ranges"]]
        elif args.get("bookmarks"):
            groups = page_ops.split_groups_by_bookmarks(session)
        else:
            raise CommandError("Say how to split: every, ranges, or bookmarks.")
        result: dict = {}
        protection = _kept_protection(session, args, result)
        files = page_ops.split_document(session, groups, args["folder"], session.path.stem, ctx.progress,
                                        protection)
        return {"outputs": [str(f) for f in files], "protected": protection is not None, **result}
    finally:
        session.close()


@command("extract", "Make a new PDF of some pages", WRITE, "Pages",
         FILE, OUT, PASSWORD, Param("pages", "pages", "Which pages", required=True), NO_PROTECT, OVERWRITE)
def _extract(args, ctx):
    from planetpdf.core import pages as page_ops

    session = _open(args)
    try:
        pages = _pages(args, session.page_count)
        target = _target(args)
        result: dict = {}
        protection = _kept_protection(session, args, result)
        page_ops.export_pages(session, pages, target, protection)
        return {"output": str(target), "pages": len(pages), "protected": protection is not None, **result}
    finally:
        session.close()


@command("delete-pages", "Delete pages", WRITE, "Pages",
         FILE, OUT, PASSWORD, Param("pages", "pages", "Which pages", required=True), OVERWRITE)
def _delete(args, ctx):
    from planetpdf.core import pages as page_ops

    session = _open(args)
    try:
        page_ops.delete_pages(session, _pages(args, session.page_count))
        return _done(session, args)
    except BaseException:
        session.close()
        raise


@command("rotate", "Turn pages", WRITE, "Pages", FILE, OUT, PASSWORD, PAGES,
         Param("degrees", "choice", "90 (clockwise), 180, or 270", required=True, choices=("90", "180", "270")),
         OVERWRITE)
def _rotate(args, ctx):
    from planetpdf.core import pages as page_ops

    session = _open(args)
    try:
        page_ops.rotate_pages(session, _pages(args, session.page_count), int(args["degrees"]))
        return _done(session, args)
    except BaseException:
        session.close()
        raise


@command("reorder", "Put the pages in a new order", WRITE, "Pages", FILE, OUT, PASSWORD,
         Param("order", "pages", 'The new order, e.g. "3, 1-2, 4-"', required=True), OVERWRITE)
def _reorder(args, ctx):
    from planetpdf.core import pages as page_ops

    session = _open(args)
    try:
        order = _pages(args, session.page_count, "order")
        if sorted(order) != list(range(session.page_count)):
            raise CommandError("The order must name every page once.")
        page_ops.reorder_pages(session, order)
        return _done(session, args)
    except BaseException:
        session.close()
        raise


@command("insert", "Insert another PDF's pages", WRITE, "Pages", FILE, OUT, PASSWORD,
         Param("from", "file", "The PDF whose pages go in", required=True),
         Param("before", "int", "Before this page (default: at the end)"), OVERWRITE)
def _insert(args, ctx):
    from planetpdf.core import pages as page_ops

    session = _open(args)
    try:
        before = args.get("before")
        index = session.page_count if before is None else before - 1
        count, _map = page_ops.insert_pdf_pages(session, args["from"].read_bytes(), index, name=args["from"].name)
        return _done(session, args, inserted=count)
    except BaseException:
        session.close()
        raise


@command("crop", "Crop pages (margins in points: 72 = 1 inch)", WRITE, "Pages", FILE, OUT, PASSWORD, PAGES,
         Param("margins", "text", "left,top,right,bottom", required=True), OVERWRITE)
def _crop(args, ctx):
    from planetpdf.core import pages as page_ops

    try:
        margins = tuple(float(v) for v in args["margins"].split(","))
        assert len(margins) == 4
    except (ValueError, AssertionError):
        raise CommandError("margins: four numbers, left,top,right,bottom.") from None
    session = _open(args)
    try:
        page_ops.crop_pages(session, _pages(args, session.page_count), margins=margins)
        return _done(session, args)
    except BaseException:
        session.close()
        raise


@command("redact", "Remove text for good (words, phrases, patterns), then check the result", WRITE, "Redact",
         FILE, OUT, PASSWORD, PAGES, *FIND_PARAMS,
         Param("fill", "text", "Box color (name or #hex)", default="black"),
         Param("overlay_text", "text", 'Text on the boxes, e.g. "REDACTED"', default=""),
         Param("keep_hidden_information", "flag", "Don't also remove metadata, comments, attachments, scripts…"),
         Param("touching_shapes", "flag", "Also remove shapes that only touch a box"),
         Param("keep_failed", "flag", "Save the file even if its checks fail (by default a failed redaction "
               "saves nothing)"), NO_PROTECT, OVERWRITE)
def _redact(args, ctx):
    from planetpdf.core.redaction import apply, marks, sanitize, search

    if args["keep_failed"] and Path(args["out"]).resolve() == Path(args["file"]).resolve():
        # (a failed copy kept over its own original: security review 2026-11-25)
        raise CommandError("--keep-failed needs --out with another name: a redaction that fails its checks is "
                           "never written over the original.")
    session = _open(args)
    try:
        pages = _pages(args, session.page_count)
        specs = []
        for pattern, label in _pattern(args):
            for hit in search.search_pages(session, pattern, pages):
                specs.append(marks.make_spec(hit.page, hit.rects, label))
        if not specs:
            scanned = [i + 1 for i in pages if search.page_has_images_but_no_text(session, i)]
            note = "Nothing matched: no file was written."
            if scanned:  # (security review 2026-11-25: a page that's only a picture can't be searched)
                note += (f" {len(scanned)} page(s) have no text to search, only pictures (scans?): "
                         f"{', '.join(map(str, scanned[:20]))}{'…' if len(scanned) > 20 else ''}. Recognize their "
                         "text first (planetpdf ocr), or mark them by hand in PlanetPDF.")
            return {"output": None, "marks": 0, "note": note, "pages_without_text": scanned}
        marks.add_marks(session, specs)
        target = _target(args)
        options = apply.RedactionOptions(fill=_color(args["fill"], (0, 0, 0)), overlay_text=args["overlay_text"],
                                         remove_touching_graphics=args["touching_shapes"])
        clean = None if args["keep_hidden_information"] else sanitize.SanitizeOptions()
        import pymupdf

        extra: dict = {}
        protection = _kept_protection(session, args, extra)
        data = session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE)
        finished = apply.redact_copy(data, target, options, clean, progress=ctx.progress, protection=protection)
        result, verdict = finished.result, finished.result.report
        if verdict.passed or args["keep_failed"]:
            apply.finish_redaction(session, finished)
        else:  # (a failed redaction saves nothing: security review 2026-11-21)
            finished.discard()
        return {"output": str(target) if verdict.passed or args["keep_failed"] else None,
                "marks": result.marks_applied, "pages": result.pages_redacted,
                "verified": verdict.passed,
                "checks": [{"status": c.status.value, "title": c.title, "detail": c.detail} for c in verdict.checks],
                "protected": protection is not None, **extra}
    finally:
        session.close()


@command("sanitize", "Remove hidden information: metadata, comments, attachments, scripts, hidden layers…", WRITE,
         "Redact", FILE, OUT, PASSWORD,
         Param("keep", "texts", "What to keep: metadata, xmp_metadata, bookmarks, embedded_files, javascript, "
               "hidden_layers, comments, form_values, structure"),
         Param("remove_signatures", "flag", "A signed PDF: remove its signatures too (needed: a signed file "
               "saved the usual way keeps everything removed in its earlier, signed version)"), OVERWRITE)
def _sanitize(args, ctx):
    from planetpdf.core.redaction import sanitize
    from planetpdf.core.redaction.ocg import LayerError

    options = sanitize.SanitizeOptions()
    for name in args.get("keep") or []:
        if not hasattr(options, name):
            raise CommandError(f"keep: there's nothing called {name!r}.")
        setattr(options, name, False)
    session = _open(args)
    try:
        signed = session.has_digital_signatures
        if signed and not args["remove_signatures"]:  # (security review 2026-11-25)
            raise CommandError("this PDF is digitally signed. Removing hidden information rewrites it as a whole "
                               "new file without the signatures; add --remove-signatures to do that (with --out "
                               "to keep the signed file as it is).")
        try:
            result = sanitize.sanitize(session.doc, options, remove_signatures=signed)
        except LayerError as exc:
            raise PdfError(f"nothing was saved. {exc} To remove the rest, add --keep hidden_layers.") from None
        session.signatures_changed()
        session.mark_modified()
        return _done(session, args, removed=result.removed)
    except BaseException:
        session.close()
        raise


@command("protect", "Password-protect a PDF (AES-256)", WRITE, "Protect", FILE, OUT, PASSWORD,
         Param("open_password", "secret", "Needed to open the file"),
         Param("permissions_password", "secret", "Needed to change what's allowed"),
         Param("no_print", "flag", "Don't allow printing"), Param("no_copy", "flag", "Don't allow copying text"),
         Param("no_edit", "flag", "Don't allow changes"), Param("no_comments", "flag", "Don't allow comments"),
         Param("no_forms", "flag", "Don't allow filling in forms"), OVERWRITE)
def _protect(args, ctx):
    protection = Protection(open_password=args.get("open_password") or "",
                            permissions_password=args.get("permissions_password") or "",
                            allow_print=not args["no_print"], allow_copy=not args["no_copy"],
                            allow_edit=not args["no_edit"], allow_comments=not args["no_comments"],
                            allow_forms=not args["no_forms"])
    protection.validate()
    session = _open(args)
    try:
        session.set_protection(protection)
        return _done(session, args)
    except BaseException:
        session.close()
        raise


@command("unprotect", "Remove a PDF's password protection", WRITE, "Protect", FILE, OUT,
         Param("password", "secret", "The file's password", required=True), OVERWRITE)
def _unprotect(args, ctx):
    session = _open(args)
    try:
        from planetpdf.core.document import REMOVE_PROTECTION

        session.set_protection(REMOVE_PROTECTION)
        return _done(session, args)
    except BaseException:
        session.close()
        raise


@command("set-metadata", "Change a PDF's title, author, subject, or keywords", WRITE, "Read", FILE, OUT, PASSWORD,
         Param("set", "texts", 'name=value, e.g. "title=Annual Report" "author=Jane Doe"', required=True),
         OVERWRITE)
def _metadata(args, ctx):
    from planetpdf.core import properties

    values = _pairs(args["set"], "set")
    session = _open(args)
    try:
        current = properties.get_metadata(session)
        unknown = sorted(set(values) - set(current))
        if unknown:
            raise CommandError(f"set: known names are {', '.join(current)}.")
        properties.set_metadata(session, {**current, **values})
        return _done(session, args)
    except BaseException:
        session.close()
        raise


POSITIONS = ("top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right")


def _marks_spec(args):
    from planetpdf.core import page_marks

    texts = _pairs(args.get("text"), "text")
    bad = sorted(set(texts) - set(POSITIONS))
    if bad:
        raise CommandError(f"text: positions are {', '.join(POSITIONS)}.")
    return page_marks.HeaderFooterSpec(texts=texts, size=args["size"], color=_color(args.get("color"), (0, 0, 0)))


@command("header-footer", "Add headers and footers ({page}, {pages}, {date}, {file})", WRITE, "Pages",
         FILE, OUT, PASSWORD, PAGES,
         Param("text", "texts", 'position=text, e.g. "bottom-center=Page {page} of {pages}"', required=True),
         Param("size", "number", "Text size", default=10.0), Param("color", "text", "Text color"), OVERWRITE)
def _header(args, ctx):
    from planetpdf.core import page_marks

    spec = _marks_spec(args)
    session = _open(args)
    try:
        page_marks.add_header_footer(session, _pages(args, session.page_count), spec)
        return _done(session, args)
    except BaseException:
        session.close()
        raise


@command("bates", "Add Bates numbers", WRITE, "Pages", FILE, OUT, PASSWORD, PAGES,
         Param("prefix", "text", "Before the number", default=""), Param("suffix", "text", "After it", default=""),
         Param("start", "int", "First number", default=1), Param("digits", "int", "Digits", default=6),
         Param("position", "choice", "Where", default="bottom-right", choices=POSITIONS),
         Param("size", "number", "Text size", default=10.0), OVERWRITE)
def _bates(args, ctx):
    from planetpdf.core import page_marks

    spec = page_marks.HeaderFooterSpec(texts={args["position"]: "{bates}"}, size=args["size"],
                                       bates_prefix=args["prefix"], bates_suffix=args["suffix"],
                                       bates_start=args["start"], bates_digits=args["digits"])
    session = _open(args)
    try:
        page_marks.add_header_footer(session, _pages(args, session.page_count), spec)
        return _done(session, args)
    except BaseException:
        session.close()
        raise


@command("watermark", "Add a text or picture watermark", WRITE, "Pages", FILE, OUT, PASSWORD, PAGES,
         Param("text", "text", "The watermark's words", default="CONFIDENTIAL"),
         Param("image", "file", "A picture instead (PNG or JPG)"),
         Param("color", "text", "Text color", default="red"),
         Param("opacity", "number", "0-1", default=0.25), Param("rotation", "number", "Degrees", default=45.0),
         Param("in_front", "flag", "Over the page content instead of behind it"), OVERWRITE)
def _watermark(args, ctx):
    from planetpdf.core import page_marks

    spec = page_marks.WatermarkSpec(text=args["text"], color=_color(args["color"], (0.8, 0.1, 0.1)),
                                    opacity=args["opacity"], rotation=args["rotation"], behind=not args["in_front"],
                                    image=args["image"].read_bytes() if args.get("image") else None)
    session = _open(args)
    try:
        page_marks.add_watermark(session, _pages(args, session.page_count), spec)
        return _done(session, args)
    except BaseException:
        session.close()
        raise


@command("remove-marks", "Remove headers and footers, Bates numbers, watermarks, or backgrounds PlanetPDF added",
         WRITE, "Pages", FILE, OUT, PASSWORD, PAGES,
         Param("kind", "choice", "header, bates, watermark, or background", required=True,
               choices=("header", "bates", "watermark", "background")), OVERWRITE)
def _remove_marks(args, ctx):
    from planetpdf.core import page_marks

    session = _open(args)
    try:
        removed = page_marks.remove_marks(session, args["kind"], _pages(args, session.page_count))
        return _done(session, args, removed=removed)
    except BaseException:
        session.close()
        raise


@command("fill", "Fill in a form's fields", WRITE, "Forms", FILE, OUT, PASSWORD,
         Param("set", "texts", 'name=value (checkboxes: on / off)'),
         Param("data", "json", "Field values as JSON ({\"Name\": \"Jane\", \"Agree\": true})"),
         Param("flatten", "flag", "Make the fields part of the page afterwards"), OVERWRITE)
def _fill(args, ctx):
    from planetpdf.core.forms import fields, flatten

    values = dict(args.get("data") or {})
    for name, value in _pairs(args.get("set"), "set").items():
        values[name] = {"on": True, "off": False, "true": True, "false": False}.get(value.lower(), value)
    if not values:
        raise CommandError("Give the values (set or data).")
    session = _open(args)
    try:
        for n, (name, value) in enumerate(values.items()):
            ctx.tick(n, len(values), "Filling field")
            fields.set_field_value(session, name, value)
        if args["flatten"]:
            flatten.flatten_forms(session)
        session.mark_modified()
        return _done(session, args, filled=len(values))
    except BaseException:
        session.close()
        raise


@command("flatten-forms", "Make a form's fields part of the page", WRITE, "Forms", FILE, OUT, PASSWORD, OVERWRITE)
def _flatten(args, ctx):
    from planetpdf.core.forms import flatten

    session = _open(args)
    try:
        count = flatten.flatten_forms(session)
        session.mark_modified()
        return _done(session, args, flattened=count)
    except BaseException:
        session.close()
        raise


@command("save-as-standard", "Save as PDF/A, PDF/X, or PDF/E (through Ghostscript)", WRITE, "Standards",
         FILE, OUT, PASSWORD, STANDARD, OVERWRITE)
def _standard(args, ctx):
    import pymupdf

    from planetpdf.core import compliance, ghostscript

    session = _open(args)
    try:
        doc = pymupdf.open(stream=session.doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE), filetype="pdf")
        target = _target(args)
    finally:
        session.close()
    try:
        changed = compliance.fix_copy(doc, args["standard"])
        notes = ghostscript.convert_standard(doc, target, args["standard"], ctx.progress)
    finally:
        doc.close()
    return {"output": str(target), "changed": changed, "notes": notes}


# --- signing ------------------------------------------------------------------------------------


@command("sign", "Digitally sign a PDF (always asks you first)", SIGN, "Sign",
         FILE, OUT,
         Param("id", "text", "A Digital ID from signing-ids (Windows' store or the Keychain)"),
         Param("pfx", "file", "Or a Digital ID file (.pfx / .p12)"),
         Param("pfx_password", "secret", "The Digital ID file's password"),
         Param("page", "int", "Page for the visible signature (with rect)", default=1),
         Param("rect", "text", "x0,y0,x1,y1 in points from the page's top-left; none = invisible"),
         Param("reason", "text", "Reason", default=""), Param("location", "text", "Location", default=""),
         Param("timestamp_url", "text", "A timestamp server (RFC 3161)", default=""),
         Param("certify", "choice", "Certify instead: none, forms, or comments (changes allowed after)",
               choices=("none", "forms", "comments")), OVERWRITE)
def _sign(args, ctx):
    from planetpdf.core.signing.digital import SignOptions, sign_pdf

    source = args["file"]
    if not source.is_file():
        raise PdfError(f"There's no file {source}.")
    if bool(args.get("id")) == bool(args.get("pfx")):
        raise CommandError("Give one Digital ID: id (from signing-ids) or pfx.")
    rect = None
    if args.get("rect"):
        try:
            rect = tuple(float(v) for v in args["rect"].split(","))
            assert len(rect) == 4
        except (ValueError, AssertionError):
            raise CommandError("rect: four numbers, x0,y0,x1,y1.") from None
    target = _target(args)
    who = f"the Digital ID {args['id']}" if args.get("id") else f"the Digital ID file {args['pfx']}"
    what = f"Sign {source} with {who}, saving the signed copy as {target}"
    if args.get("certify"):
        what += f" (a certification: {args['certify']})"
    if ctx.approve_signing is None or not ctx.approve_signing(what + "."):
        raise NotApproved("Signing wasn't approved.")
    if args.get("id"):
        from planetpdf.core.signing.providers import system_store

        provider = system_store.provider(args["id"])
    else:
        from planetpdf.core.signing.providers.pkcs12 import Pkcs12Provider

        provider = Pkcs12Provider(args["pfx"], args.get("pfx_password") or "")
    options = SignOptions(reason=args["reason"], location=args["location"], timestamp_url=args["timestamp_url"],
                          certify=args.get("certify") or "")
    sign_pdf(source, target, provider, args["page"] - 1, rect, options)
    return {"output": str(target)}
