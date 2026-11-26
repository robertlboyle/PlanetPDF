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

static CHECKED: std::sync::Once = std::sync::Once::new();
static MOVED: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());

/// %ProgramData%\PlanetPDF\Spool, made private the first time. Once per run, it and its parent
/// are checked first (security review 2026-11-25: a folder that already existed was used as it
/// was, and any user can make folders in ProgramData): one not owned by SYSTEM or Administrators,
/// or that lets anyone else in, is moved aside (kept for an administrator) and made again.
pub fn spool_dir() -> PathBuf {
    let base = std::env::var_os("ProgramData").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\ProgramData"));
    let parent = base.join("PlanetPDF");
    let dir = parent.join("Spool");
    CHECKED.call_once(|| {
        for folder in [&parent, &dir] {
            if let Some(note) = make_private(folder) {
                MOVED.lock().map(|mut m| m.push(note)).ok();
            }
        }
    });
    if !dir.is_dir() {
        let _ = std::fs::create_dir_all(&parent);
        create_private_dir(&dir);
    }
    let notes: Vec<String> = MOVED.lock().map(|mut m| std::mem::take(&mut *m)).unwrap_or_default();
    for note in notes {
        log(&note);
    }
    dir
}

/// Makes `folder` private if it isn't. Returns what was done, for the log.
fn make_private(folder: &std::path::Path) -> Option<String> {
    if folder.is_dir() && !security_sddl(folder).is_some_and(|sddl| private_sddl(&sddl)) {
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs());
        let aside = folder.with_file_name(format!("{}.moved-{stamp}",
                                                  folder.file_name().map(|n| n.to_string_lossy()).unwrap_or_default()));
        let moved = std::fs::rename(folder, &aside).is_ok();
        create_private_dir(folder);
        return Some(format!("{} wasn't private (its owner or permissions weren't SYSTEM and Administrators only){}",
                            folder.display(),
                            if moved { format!(": moved to {} and made again", aside.display()) } else { String::new() }));
    }
    if !folder.is_dir() {
        create_private_dir(folder);
    }
    None
}

/// A folder's or file's owner and permissions, as SDDL ("O:SYD:P(A;OICI;FA;;;SY)…").
pub(crate) fn security_sddl(folder: &std::path::Path) -> Option<String> {
    let path = ffi::wide(&folder.to_string_lossy());
    let what = ffi::OWNER_SECURITY_INFORMATION | ffi::DACL_SECURITY_INFORMATION;
    let mut descriptor: *mut std::ffi::c_void = std::ptr::null_mut();
    // SAFETY: Windows fills in a descriptor freed with LocalFree; the string too.
    unsafe {
        if ffi::GetNamedSecurityInfoW(path.as_ptr(), ffi::SE_FILE_OBJECT, what, std::ptr::null_mut(),
                                      std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut(),
                                      &mut descriptor) != 0 {
            return None;
        }
        let mut text: ffi::LPWSTR = std::ptr::null_mut();
        let ok = ffi::ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, ffi::SDDL_REVISION_1, what,
                                                                          &mut text, std::ptr::null_mut()) != 0;
        ffi::LocalFree(descriptor);
        if !ok {
            return None;
        }
        let sddl = ffi::from_wide(text);
        ffi::LocalFree(text as *mut _);
        Some(sddl)
    }
}

/// Owned by SYSTEM or Administrators, permissions not inherited, and allowing only them.
pub fn private_sddl(sddl: &str) -> bool {
    let Some(rest) = sddl.strip_prefix("O:") else { return false };
    let Some(at) = rest.find("D:") else { return false };
    if !matches!(&rest[..at], "SY" | "BA") {
        return false;
    }
    let dacl = &rest[at + 2..];
    let first = dacl.find('(').unwrap_or(dacl.len());
    if !dacl[..first].contains('P') {
        return false;
    }
    let aces: Vec<&str> = dacl[first..].split(')').map(|a| a.trim_start_matches('(')).filter(|a| !a.is_empty()).collect();
    !aces.is_empty() && aces.iter().all(|ace| {
        let parts: Vec<&str> = ace.split(';').collect();
        parts.len() == 6 && parts[0] == "A" && matches!(parts[5], "SY" | "BA")
    })
}

#[cfg(test)]
mod tests {
    use super::private_sddl;

    #[test]
    fn only_system_and_administrators_count_as_private() {
        assert!(private_sddl("O:SYD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"));
        assert!(private_sddl("O:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"));
        assert!(!private_sddl("O:S-1-5-21-1-2-3-1001D:P(A;OICI;FA;;;SY)"));  // (a user's folder)
        assert!(!private_sddl("O:SYD:(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"));  // (inherits ProgramData's)
        assert!(!private_sddl("O:SYD:P(A;OICI;FA;;;SY)(A;OICI;0x1200a9;;;BU)"));  // (users may read)
        assert!(!private_sddl("O:SYD:P(D;;FA;;;WD)(A;OICI;FA;;;SY)"));  // (anything but allow-SY/BA)
        assert!(!private_sddl(""));
    }
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
