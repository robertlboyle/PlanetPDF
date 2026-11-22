//! The monitor's own folder (%ProgramData%\PlanetPDF\Spool: SYSTEM and Administrators only)
//! and its log there (printer.log, a line per event; kept under 1 MB, the older half in
//! printer.old.log).

use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;

use crate::ffi;

const MAX_LOG: u64 = 1024 * 1024;
/// Owner SYSTEM; full control for SYSTEM and Administrators only, inherited; nothing else.
const PRIVATE_SDDL: &str = "O:SYD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)";

/// %ProgramData%\PlanetPDF\Spool, made private the first time.
pub fn spool_dir() -> PathBuf {
    let base = std::env::var_os("ProgramData").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\ProgramData"));
    let parent = base.join("PlanetPDF");
    let dir = parent.join("Spool");
    if !dir.is_dir() {
        let _ = std::fs::create_dir_all(&parent);
        create_private_dir(&dir);
    }
    dir
}

fn create_private_dir(dir: &std::path::Path) {
    let sddl = ffi::wide(PRIVATE_SDDL);
    let path = ffi::wide(&dir.to_string_lossy());
    let mut descriptor: *mut std::ffi::c_void = std::ptr::null_mut();
    // SAFETY: valid NUL-terminated strings; the descriptor is freed with LocalFree.
    unsafe {
        if ffi::ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(), ffi::SDDL_REVISION_1,
                                                                     &mut descriptor, std::ptr::null_mut()) == 0 {
            let _ = std::fs::create_dir_all(dir);
            return;
        }
        let attributes = ffi::SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<ffi::SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        let made = ffi::CreateDirectoryW(path.as_ptr(), &attributes);
        ffi::LocalFree(descriptor);
        if made == 0 && !dir.is_dir() {
            let _ = std::fs::create_dir_all(dir);  // (never lose a job over the folder's permissions)
        }
    }
}

/// One line in printer.log (never fails: logging must not stop printing).
pub fn log(message: &str) {
    let dir = spool_dir();
    let path = dir.join("printer.log");
    if std::fs::metadata(&path).map(|m| m.len() > MAX_LOG).unwrap_or(false) {
        let _ = std::fs::rename(&path, dir.join("printer.old.log"));
    }
    let mut now = ffi::SYSTEMTIME::default();
    // SAFETY: a valid SYSTEMTIME to fill in.
    unsafe { ffi::GetLocalTime(&mut now) };
    let line = format!("{:04}-{:02}-{:02} {:02}:{:02}:{:02} {}\r\n", now.wYear, now.wMonth, now.wDay, now.wHour,
                       now.wMinute, now.wSecond, message);
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = file.write_all(line.as_bytes());
    }
}
