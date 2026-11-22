"""macOS integration (only imported on macOS).

Run from source, PlanetPDF is the Python interpreter's app bundle, so the menu bar's
application menu said "Python" (user bug 2026-10-10). macOS takes that name from the
bundle's CFBundleName, read when the application starts up: setting it in the main
bundle's info dictionary before QApplication exists makes the menu say PlanetPDF.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

log = logging.getLogger(__name__)


def set_app_name(name: str) -> bool:
    """Call before QApplication is created. True if the name was set."""
    try:
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value

        def send(restype, *argtypes):  # objc_msgSend with exact types (arm64 isn't variadic-safe)
            return ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(address)

        def sel(text: str) -> int:
            return objc.sel_registerName(text.encode())

        bundle = send(ctypes.c_void_p)(objc.objc_getClass(b"NSBundle"), sel("mainBundle"))
        info = send(ctypes.c_void_p)(bundle, sel("infoDictionary")) if bundle else None
        setter = sel("setObject:forKey:")
        if not info or not send(ctypes.c_bool, ctypes.c_void_p)(info, sel("respondsToSelector:"), setter):
            return False
        make = send(ctypes.c_void_p, ctypes.c_char_p)
        nsstring = objc.objc_getClass(b"NSString")
        value = make(nsstring, sel("stringWithUTF8String:"), name.encode())
        key = make(nsstring, sel("stringWithUTF8String:"), b"CFBundleName")
        send(None, ctypes.c_void_p, ctypes.c_void_p)(info, setter, value, key)
        return True
    except (OSError, AttributeError, TypeError, ValueError) as exc:  # never stop the program starting
        log.warning("Couldn't set the menu bar name: %s", exc)
        return False


# --- the default app for PDF files (Mac app, 2026-10-10) ------------------------------------------

PDF_TYPE = "com.adobe.pdf"
_ALL_ROLES = 0xFFFFFFFF  # kLSRolesAll
_UTF8 = 0x08000100  # kCFStringEncodingUTF8


def _frameworks():
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    ls = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreServices"))
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    ls.LSCopyDefaultRoleHandlerForContentType.restype = ctypes.c_void_p
    ls.LSCopyDefaultRoleHandlerForContentType.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ls.LSSetDefaultRoleHandlerForContentType.restype = ctypes.c_int32
    ls.LSSetDefaultRoleHandlerForContentType.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    return cf, ls


def _cfstring(cf, text: str) -> int:
    return cf.CFStringCreateWithCString(None, text.encode(), _UTF8)


def default_app(content_type: str = PDF_TYPE) -> str | None:
    """The bundle identifier of the app that opens this type, e.g. ``com.apple.Preview``."""
    try:
        cf, ls = _frameworks()
        kind = _cfstring(cf, content_type)
        handler = ls.LSCopyDefaultRoleHandlerForContentType(kind, _ALL_ROLES)
        cf.CFRelease(kind)
        if not handler:
            return None
        buffer = ctypes.create_string_buffer(512)
        ok = cf.CFStringGetCString(handler, buffer, len(buffer), _UTF8)
        cf.CFRelease(handler)
        return buffer.value.decode() if ok else None
    except (OSError, AttributeError) as exc:
        log.warning("Couldn't read the default app for %s: %s", content_type, exc)
        return None


def set_default_app(bundle_id: str, content_type: str = PDF_TYPE) -> bool:
    """Make the app with ``bundle_id`` open this type, for the user running this."""
    try:
        cf, ls = _frameworks()
        kind, app = _cfstring(cf, content_type), _cfstring(cf, bundle_id)
        status = ls.LSSetDefaultRoleHandlerForContentType(kind, _ALL_ROLES, app)
        cf.CFRelease(kind)
        cf.CFRelease(app)
    except (OSError, AttributeError) as exc:
        log.warning("Couldn't set the default app for %s: %s", content_type, exc)
        return False
    if status != 0:
        log.warning("Launch Services refused the default app for %s (status %s)", content_type, status)
    return status == 0


def bundle_identifier() -> str | None:
    """This app's bundle identifier (Python's own when run from source)."""
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary(ctypes.util.find_library("Foundation"))
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        send = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(address)
        utf8 = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p)(address)
        bundle = send(objc.objc_getClass(b"NSBundle"), objc.sel_registerName(b"mainBundle"))
        identifier = send(bundle, objc.sel_registerName(b"bundleIdentifier")) if bundle else None
        return utf8(identifier, objc.sel_registerName(b"UTF8String")).decode() if identifier else None
    except (OSError, AttributeError, UnicodeDecodeError) as exc:
        log.warning("Couldn't read the bundle identifier: %s", exc)
        return None
