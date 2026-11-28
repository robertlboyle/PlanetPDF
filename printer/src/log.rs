//! The service's own folder (%ProgramData%\PlanetPDF\Spool: SYSTEM and Administrators only)
//! and its log there (printer.log, a line per event; kept under 1 MB, the older half in
//! printer.old.log).
//!
//! **A folder is used only once it is known to be ours** (security review 2026-11-27, finding 1:
//! a Spool folder a user had made before the first install, and kept a file open in, couldn't be
//! moved aside, and was used as it was, so another user's printout landed where that user could
//! read it). Any user may make folders in ProgramData, so before anything is written:
//!
//! - `ProgramData\PlanetPDF` and its `Spool` must be real folders (not a junction or another
//!   reparse point), owned by SYSTEM or Administrators. `Spool` must let in nobody else
//!   (`private_sddl`).
//! - `ProgramData\PlanetPDF` owned by them but open to others (as ProgramData's own permissions
//!   left it, up to 1.0.6) is made private where it is. Anything else is moved aside, kept for an
//!   administrator, and made again.
//! - When that can't be done (the move refused: someone holds a file open in it), a folder of
//!   SYSTEM's own making is used instead: `ProgramData\PlanetPDF-Spool-<random>`, made with its
//!   permissions in one step, then checked like the rest. Windows' Application event log says
//!   where it is.
//! - When no folder can be trusted, nothing is kept: printouts are refused (`spool_dir` is None).

use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use crate::ffi;

const MAX_LOG: u64 = 1024 * 1024;
/// Owner SYSTEM; full control for SYSTEM and Administrators only, inherited; nothing else.
const PRIVATE_SDDL: &str = "O:SYD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)";
/// The folder used when the usual one can't be made ours (`fallback`).
const FALLBACK_PREFIX: &str = "PlanetPDF-Spool-";

/// The spool folder chosen for this run (chosen again if it goes missing).
static SPOOL: Mutex<Option<PathBuf>> = Mutex::new(None);

/// What makes a folder ours: who may own it, and the permissions a folder is made with.
/// The service's own are `Rules::windows()`; the tests use their own account.
pub struct Rules {
    pub owners: Vec<String>,
    pub new_sddl: String,
}

impl Rules {
    pub fn windows() -> Rules {
        Rules { owners: vec!["SY".into(), "BA".into()], new_sddl: PRIVATE_SDDL.into() }
    }
}

/// %ProgramData%, as Windows has it for this account (never a user's environment: this runs as
/// SYSTEM).
pub fn program_data() -> PathBuf {
    std::env::var_os("ProgramData").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\ProgramData"))
}

/// The spool folder (None: no folder that only SYSTEM and Administrators can use could be had,
/// so nothing may be kept). Checked when first asked for in a run, and again whenever it's gone.
pub fn spool_dir() -> Option<PathBuf> {
    let mut chosen = SPOOL.lock().ok()?;
    if let Some(dir) = chosen.as_ref().filter(|d| matches!(state(d), State::Folder)) {
        return Some(dir.clone());
    }
    let (dir, notes) = choose(&program_data(), &Rules::windows());
    *chosen = dir.clone();
    drop(chosen);  // (log() asks for the folder too)
    let unusual = dir.as_ref().is_none_or(|d| d.file_name().is_some_and(|n| n.to_string_lossy().starts_with(FALLBACK_PREFIX)));
    for note in &notes {
        log(note);
        if unusual {
            event_log(note);
        }
    }
    dir
}

/// What is at a path, without following a junction.
#[derive(Debug, PartialEq)]
pub enum State {
    Missing,
    /// A file, a junction, a symbolic link: anything that isn't a plain folder.
    NotAFolder,
    Folder,
}

pub fn state(path: &Path) -> State {
    let name = ffi::wide(&path.to_string_lossy());
    // SAFETY: a NUL-terminated path. (GetFileAttributesW reports a junction's own attributes.)
    let attributes = unsafe { ffi::GetFileAttributesW(name.as_ptr()) };
    if attributes == ffi::INVALID_FILE_ATTRIBUTES {
        State::Missing
    } else if attributes & ffi::FILE_ATTRIBUTE_REPARSE_POINT != 0 || attributes & ffi::FILE_ATTRIBUTE_DIRECTORY == 0 {
        State::NotAFolder
    } else {
        State::Folder
    }
}

/// A plain folder owned by one of `rules.owners` and letting in nobody else.
pub fn is_ours(path: &Path, rules: &Rules) -> bool {
    state(path) == State::Folder && security_sddl(path).is_some_and(|sddl| private_for(&sddl, &rules.owners))
}

/// The usual folder, or a fallback, made ours (`spool_dir`, apart from remembering it). Returns
/// the folder and what was done, for the log.
pub fn choose(base: &Path, rules: &Rules) -> (Option<PathBuf>, Vec<String>) {
    let mut notes = Vec::new();
    let parent = base.join("PlanetPDF");
    let dir = parent.join("Spool");
    if make_ours(&parent, rules, true, &mut notes) && make_ours(&dir, rules, false, &mut notes) {
        return (Some(dir), notes);
    }
    let fallback = fallback(base, rules);
    notes.push(match &fallback {
        Some(other) => format!("printouts are kept in {} instead, a folder only SYSTEM and Administrators can use",
                               other.display()),
        None => "no folder that only SYSTEM and Administrators can use could be made, so printouts are refused \
                 until one can"
            .to_string(),
    });
    (fallback, notes)
}

/// Makes `folder` ours if it can be, and says whether it is. `fix_in_place`: a folder owned by
/// SYSTEM or Administrators whose permissions let others in gets private permissions where it is
/// (for `ProgramData\PlanetPDF`; what others put in it isn't used). Anything else that isn't ours
/// is moved aside and made again.
fn make_ours(folder: &Path, rules: &Rules, fix_in_place: bool, notes: &mut Vec<String>) -> bool {
    let what = folder.display();
    match state(folder) {
        State::Missing => {}
        State::Folder if is_ours(folder, rules) => return true,
        State::Folder if fix_in_place && owned_by(folder, &rules.owners) => {
            if set_private(folder, rules) && is_ours(folder, rules) {
                notes.push(format!("{what} let other users in; now only SYSTEM and Administrators can use it"));
                return true;
            }
        }
        _ => {}
    }
    if state(folder) != State::Missing {
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_secs());
        let name = folder.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let aside = folder.with_file_name(format!("{name}.moved-{stamp}"));
        if std::fs::rename(folder, &aside).is_err() {
            notes.push(format!("{what} isn't only SYSTEM's and Administrators' (its owner, its permissions, or a \
                                link to elsewhere), and it couldn't be moved aside, so it isn't used"));
            return false;
        }
        notes.push(format!("{what} wasn't only SYSTEM's and Administrators' (its owner, its permissions, or a \
                            link to elsewhere): moved to {} and made again", aside.display()));
    }
    let made = create_private_dir(folder, rules) && is_ours(folder, rules);
    if !made {
        notes.push(format!("{what} couldn't be made with permissions for SYSTEM and Administrators only"));
    }
    made
}

/// A folder of our own making in `base`: an earlier one if it is still ours, else a new one with
/// a name nobody could have guessed. Every one is checked after it's made.
fn fallback(base: &Path, rules: &Rules) -> Option<PathBuf> {
    if let Ok(entries) = std::fs::read_dir(base) {
        for entry in entries.flatten() {
            let path = entry.path();
            if entry.file_name().to_string_lossy().starts_with(FALLBACK_PREFIX) && is_ours(&path, rules) {
                return Some(path);
            }
        }
    }
    for _ in 0..5 {
        let path = base.join(format!("{FALLBACK_PREFIX}{}", random_hex()?));
        if create_private_dir(&path, rules) && is_ours(&path, rules) {
            return Some(path);
        }
    }
    None
}

fn random_hex() -> Option<String> {
    let mut bytes = [0u8; 12];
    // SAFETY: a buffer of 12 bytes for the system's random number generator.
    let status = unsafe {
        ffi::BCryptGenRandom(std::ptr::null_mut(), bytes.as_mut_ptr(), bytes.len() as u32,
                             ffi::BCRYPT_USE_SYSTEM_PREFERRED_RNG)
    };
    (status == 0).then(|| bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// A folder's or file's owner and permissions, as SDDL ("O:SYD:P(A;OICI;FA;;;SY)…").
pub(crate) fn security_sddl(folder: &Path) -> Option<String> {
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

fn owned_by(folder: &Path, owners: &[String]) -> bool {
    security_sddl(folder).and_then(|sddl| owner_of(&sddl).map(|o| owners.iter().any(|w| w == o))).unwrap_or(false)
}

fn owner_of(sddl: &str) -> Option<&str> {
    let rest = sddl.strip_prefix("O:")?;
    // (the owner ends where "G:" or "D:" begins; an owner such as DA starts with one of those letters)
    let end = rest.char_indices().skip(1).find(|&(i, c)| (c == 'D' || c == 'G') && rest[i + 1..].starts_with(':'))?.0;
    Some(&rest[..end])
}

/// Owned by SYSTEM or Administrators, permissions not inherited, and allowing only them.
pub fn private_sddl(sddl: &str) -> bool {
    private_for(sddl, &["SY".to_string(), "BA".to_string()])
}

/// The same with the owners given: owned by one of them, permissions not inherited, and allowing
/// only SYSTEM, Administrators, or them.
fn private_for(sddl: &str, owners: &[String]) -> bool {
    let Some(owner) = owner_of(sddl) else { return false };
    if !owners.iter().any(|o| o == owner) {
        return false;
    }
    let Some(at) = sddl.find("D:") else { return false };
    let dacl = &sddl[at + 2..];
    let first = dacl.find('(').unwrap_or(dacl.len());
    if !dacl[..first].contains('P') {
        return false;
    }
    let aces: Vec<&str> = dacl[first..].split(')').map(|a| a.trim_start_matches('(')).filter(|a| !a.is_empty()).collect();
    !aces.is_empty() && aces.iter().all(|ace| {
        let parts: Vec<&str> = ace.split(';').collect();
        parts.len() == 6 && parts[0] == "A" && (matches!(parts[5], "SY" | "BA") || owners.iter().any(|o| o == parts[5]))
    })
}

/// Makes `folder` with `rules.new_sddl` in one step (never a folder made first and closed after).
fn create_private_dir(folder: &Path, rules: &Rules) -> bool {
    let sddl = ffi::wide(&rules.new_sddl);
    let path = ffi::wide(&folder.to_string_lossy());
    let mut descriptor: *mut std::ffi::c_void = std::ptr::null_mut();
    // SAFETY: valid NUL-terminated strings; the descriptor is freed with LocalFree.
    unsafe {
        if ffi::ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(), ffi::SDDL_REVISION_1,
                                                                     &mut descriptor, std::ptr::null_mut()) == 0 {
            return false;
        }
        let attributes = ffi::SECURITY_ATTRIBUTES {
            nLength: size_of::<ffi::SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor,
            bInheritHandle: 0,
        };
        let made = ffi::CreateDirectoryW(path.as_ptr(), &attributes) != 0;
        ffi::LocalFree(descriptor);
        made
    }
}

/// Gives a folder `rules.new_sddl`'s permissions, not inherited (its owner stays).
fn set_private(folder: &Path, rules: &Rules) -> bool {
    let sddl = ffi::wide(&rules.new_sddl);
    let mut path = ffi::wide(&folder.to_string_lossy());
    let mut descriptor: *mut std::ffi::c_void = std::ptr::null_mut();
    // SAFETY: the descriptor is freed with LocalFree; the DACL points into it.
    unsafe {
        if ffi::ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(), ffi::SDDL_REVISION_1,
                                                                     &mut descriptor, std::ptr::null_mut()) == 0 {
            return false;
        }
        let (mut present, mut defaulted) = (0, 0);
        let mut dacl: *mut std::ffi::c_void = std::ptr::null_mut();
        let ok = ffi::GetSecurityDescriptorDacl(descriptor, &mut present, &mut dacl, &mut defaulted) != 0
            && present != 0
            && ffi::SetNamedSecurityInfoW(path.as_mut_ptr(), ffi::SE_FILE_OBJECT,
                                          ffi::DACL_SECURITY_INFORMATION | ffi::PROTECTED_DACL_SECURITY_INFORMATION,
                                          std::ptr::null_mut(), std::ptr::null_mut(), dacl, std::ptr::null_mut()) == 0;
        ffi::LocalFree(descriptor);
        ok
    }
}

/// A warning in Windows' Application event log (for what an administrator must see even when
/// the usual printer.log isn't where they'd look).
pub fn event_log(message: &str) {
    let source = ffi::wide(crate::SERVICE_NAME);
    let text = ffi::wide(message);
    let strings = [text.as_ptr()];
    // SAFETY: a registered source handle, closed below; one NUL-terminated string.
    unsafe {
        let handle = ffi::RegisterEventSourceW(std::ptr::null(), source.as_ptr());
        if handle.is_null() {
            return;
        }
        ffi::ReportEventW(handle, ffi::EVENTLOG_WARNING_TYPE, 0, 1, std::ptr::null_mut(), 1, 0, strings.as_ptr(),
                          std::ptr::null_mut());
        ffi::DeregisterEventSource(handle);
    }
}

/// One line in printer.log (never fails: logging must not stop printing). Without a spool folder
/// of our own, the line goes to Windows' event log instead.
pub fn log(message: &str) {
    let Some(dir) = spool_dir() else {
        event_log(message);
        return;
    };
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

/// Where printouts are kept, for `planetpdf-printer-setup status` (read only: changes nothing).
pub fn spool_report() -> Result<String, String> {
    let base = program_data();
    let rules = Rules::windows();
    let usual = base.join("PlanetPDF").join("Spool");
    if is_ours(&base.join("PlanetPDF"), &rules) && is_ours(&usual, &rules) {
        return Ok(format!("{} (only SYSTEM and Administrators)", usual.display()));
    }
    let other = std::fs::read_dir(&base).ok().and_then(|entries| {
        entries.flatten().map(|e| e.path()).find(|p| {
            p.file_name().is_some_and(|n| n.to_string_lossy().starts_with(FALLBACK_PREFIX)) && is_ours(p, &rules)
        })
    });
    match other {
        Some(path) => Ok(format!("{} (only SYSTEM and Administrators; {} isn't, so it isn't used)", path.display(),
                                 usual.display())),
        None if state(&usual) == State::Missing => Err(format!("{} isn't there yet (made when the service starts)",
                                                              usual.display())),
        None => Err(format!("{} can be used by others than SYSTEM and Administrators; the service uses it only once \
                             it is made private", usual.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_system_and_administrators_count_as_private() {
        assert!(private_sddl("O:SYD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"));
        assert!(private_sddl("O:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"));
        assert!(private_sddl("O:BAG:SYD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"));  // (with a group)
        assert!(!private_sddl("O:S-1-5-21-1-2-3-1001D:P(A;OICI;FA;;;SY)"));  // (a user's folder)
        assert!(!private_sddl("O:SYD:(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"));  // (inherits ProgramData's)
        assert!(!private_sddl("O:SYD:P(A;OICI;FA;;;SY)(A;OICI;0x1200a9;;;BU)"));  // (users may read)
        assert!(!private_sddl("O:SYD:P(D;;FA;;;WD)(A;OICI;FA;;;SY)"));  // (anything but allow-SY/BA)
        assert!(!private_sddl(""));
    }

    // --- the folder rules on disk, as the account running the tests ---------------------------

    /// This account's SID, as SDDL has it.
    fn me() -> String {
        static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let probe = std::env::temp_dir().join(format!("ppdf-owner-{}-{n}", std::process::id()));
        std::fs::create_dir_all(&probe).unwrap();
        let sddl = security_sddl(&probe).unwrap();
        std::fs::remove_dir_all(&probe).unwrap();
        owner_of(&sddl).unwrap().to_string()
    }

    /// As `Rules::windows()`, with this account in SYSTEM's place.
    fn rules() -> Rules {
        let me = me();
        Rules { new_sddl: format!("O:{me}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{me})"), owners: vec![me] }
    }

    /// An empty folder standing in for ProgramData.
    fn base(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("ppdf-spool-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn a_fresh_folder_is_made_private() {
        let base = base("fresh");
        let rules = rules();
        let (dir, notes) = choose(&base, &rules);
        assert_eq!(dir, Some(base.join("PlanetPDF").join("Spool")));
        assert!(notes.is_empty());
        assert!(is_ours(&base.join("PlanetPDF"), &rules) && is_ours(&base.join("PlanetPDF").join("Spool"), &rules));
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn an_own_parent_open_to_others_is_closed_where_it_is() {
        // (as 1.0.6 left ProgramData\PlanetPDF: owned by SYSTEM, ProgramData's permissions)
        let base = base("open-parent");
        let rules = rules();
        std::fs::create_dir_all(base.join("PlanetPDF")).unwrap();  // (inherits: not private)
        assert!(!is_ours(&base.join("PlanetPDF"), &rules));
        let (dir, notes) = choose(&base, &rules);
        assert_eq!(dir, Some(base.join("PlanetPDF").join("Spool")));
        assert!(notes[0].contains("now only SYSTEM and Administrators"), "{notes:?}");
        assert!(is_ours(&base.join("PlanetPDF"), &rules));
        // (the next start finds it ours and says nothing)
        assert!(choose(&base, &rules).1.is_empty());
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn a_spool_folder_open_to_others_is_moved_aside() {
        let base = base("open-spool");
        let rules = rules();
        std::fs::create_dir_all(base.join("PlanetPDF").join("Spool")).unwrap();
        std::fs::write(base.join("PlanetPDF").join("Spool").join("planted.pdf"), b"x").unwrap();
        let (dir, notes) = choose(&base, &rules);
        let dir = dir.unwrap();
        assert_eq!(dir, base.join("PlanetPDF").join("Spool"));
        assert!(notes.iter().any(|n| n.contains("moved to")), "{notes:?}");
        assert!(is_ours(&dir, &rules) && !dir.join("planted.pdf").exists());
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn a_junction_is_never_followed() {
        let base = base("junction");
        let rules = rules();
        let elsewhere = base.join("elsewhere");
        std::fs::create_dir_all(&elsewhere).unwrap();
        std::fs::create_dir_all(base.join("PlanetPDF")).unwrap();
        let made = std::process::Command::new("cmd")
            .args(["/c", "mklink", "/J", &base.join("PlanetPDF").join("Spool").to_string_lossy(),
                   &elsewhere.to_string_lossy()])
            .output()
            .unwrap();
        assert!(made.status.success());
        assert_eq!(state(&base.join("PlanetPDF").join("Spool")), State::NotAFolder);
        let (dir, _) = choose(&base, &rules);
        let dir = dir.unwrap();
        assert_eq!(state(&dir), State::Folder);
        assert!(is_ours(&dir, &rules));
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn a_folder_that_cant_be_moved_is_not_used() {
        // (finding 1: the folder a user made, with a file of theirs held open in it)
        let base = base("held");
        let rules = rules();
        let spool = base.join("PlanetPDF").join("Spool");
        std::fs::create_dir_all(&spool).unwrap();
        let held = std::fs::File::create(spool.join("held-open.txt")).unwrap();
        let (dir, notes) = choose(&base, &rules);
        let dir = dir.unwrap();
        assert_ne!(dir, spool);
        assert!(dir.file_name().unwrap().to_string_lossy().starts_with(FALLBACK_PREFIX));
        assert!(is_ours(&dir, &rules));
        assert!(notes.iter().any(|n| n.contains("couldn't be moved aside")), "{notes:?}");
        // (the next start uses the same one rather than making another)
        assert_eq!(choose(&base, &rules).0, Some(dir));
        drop(held);
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn owners_are_read_with_or_without_a_group() {
        assert_eq!(owner_of("O:SYD:P(A;;FA;;;SY)"), Some("SY"));
        assert_eq!(owner_of("O:BAG:SYD:P"), Some("BA"));
        assert_eq!(owner_of("O:S-1-5-21-1-2-3-1001D:P"), Some("S-1-5-21-1-2-3-1001"));
        assert_eq!(owner_of("O:DAD:P"), Some("DA"));
        assert_eq!(owner_of("D:P"), None);
    }
}
