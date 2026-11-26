//! Who can change a file or folder (security list 2026-11-26, item 3).
//!
//! The print service runs as SYSTEM from PlanetPDF's install folder. In Program Files only
//! administrators can change what is there; an install folder somewhere else (D:\Apps…) often
//! inherits permissions that let every user write, and then any user can put a program of their
//! own in the service's place and have it run as SYSTEM. The installer closes the folder
//! (packaging\PlanetPDF.iss); this is the check: setup refuses to add the service where others can
//! change its program, `status` reports it, and the service says so in its log when it starts.
//!
//! The check reads a security descriptor as text (SDDL: "O:BAD:PAI(A;OICI;FA;;;SY)…"), which keeps
//! the rule one that can be tested without Windows.

/// What lets its holder change a file or a folder's contents: write, append / add, delete a
/// folder's children, delete, change permissions, take ownership, and the generic write and all.
pub const CHANGE: u32 = 0x2 | 0x4 | 0x40 | 0x0001_0000 | 0x0004_0000 | 0x0008_0000 | 0x4000_0000 | 0x1000_0000;

const TRUSTED_INSTALLER: &str = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";

/// SYSTEM, Administrators, TrustedInstaller, the built-in Administrator account, and the domain's
/// administrators: whoever can change everything on the computer anyway.
fn trusted(sid: &str) -> bool {
    if matches!(sid, "SY" | "BA" | "LA" | "DA" | "EA" | "S-1-5-18" | "S-1-5-32-544" | TRUSTED_INSTALLER) {
        return true;
    }
    // (an account or group of a domain or of this computer: S-1-5-21-<three numbers>-<which>)
    sid.strip_prefix("S-1-5-21-").is_some_and(|rest| {
        let parts: Vec<&str> = rest.split('-').collect();
        parts.len() == 4 && matches!(parts[3], "500" | "512" | "519")
    })
}

/// The access mask an ACE's rights field names: "0x1200a9", or two-letter codes ("FA", "FRFX").
fn rights_mask(text: &str) -> Option<u32> {
    if let Some(hex) = text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
        return u32::from_str_radix(hex, 16).ok();
    }
    if text.len() % 2 != 0 || !text.is_ascii() {
        return None;
    }
    let mut mask = 0u32;
    for at in (0..text.len()).step_by(2) {
        mask |= match &text[at..at + 2] {
            "GA" => 0x1000_0000,
            "GX" => 0x2000_0000,
            "GW" => 0x4000_0000,
            "GR" => 0x8000_0000,
            "SD" => 0x0001_0000,
            "RC" => 0x0002_0000,
            "WD" => 0x0004_0000,
            "WO" => 0x0008_0000,
            "FA" => 0x001F_01FF,
            "FR" => 0x0012_0089,
            "FW" => 0x0012_0116,
            "FX" => 0x0012_00A0,
            "CC" => 0x1,
            "DC" => 0x2,
            "LC" => 0x4,
            "SW" => 0x8,
            "RP" => 0x10,
            "WP" => 0x20,
            "DT" => 0x40,
            "LO" => 0x80,
            "CR" => 0x100,
            _ => return None,
        };
    }
    Some(mask)
}

/// Who, besides administrators and the system, can change what this security descriptor guards:
/// their SIDs (or SDDL's two-letter names: "BU" users, "AU" signed-in users, "WD" everyone), with
/// "owner " in front of an owner. Empty: nobody else. `Err`: it can't be read (count that as open).
pub fn others_who_can_change(sddl: &str) -> Result<Vec<String>, String> {
    let rest = sddl.strip_prefix("O:").ok_or("no owner")?;
    // (the only colons outside the ACEs follow the letters that start each part: O: G: D: S:)
    let colon = rest.find(':').ok_or("no permissions")?;
    if colon == 0 {
        return Err("no owner".into());
    }
    let owner = &rest[..colon - 1];
    let mut others = Vec::new();
    if !trusted(owner) {
        others.push(format!("owner {owner}"));
    }
    let start = sddl.find("D:").filter(|at| *at >= 2 + owner.len()).ok_or("no permissions")?;
    let dacl = &sddl[start + 2..];
    let dacl = &dacl[..dacl.find("S:").unwrap_or(dacl.len())];
    let first = dacl.find('(').unwrap_or(dacl.len());
    if dacl[..first].contains("NO_ACCESS_CONTROL") {
        return Ok(vec!["everyone (no permissions are set)".into()]);
    }
    for ace in dacl[first..].split(')').map(|a| a.trim_start_matches('(')).filter(|a| !a.is_empty()) {
        let parts: Vec<&str> = ace.split(';').collect();
        if parts.len() < 6 {
            return Err(format!("an entry that can't be read: ({ace})"));
        }
        let (kind, flags, rights, sid) = (parts[0], parts[1], parts[2], parts[5]);
        if !matches!(kind, "A" | "OA" | "XA") {
            continue; // (a refusal or an audit entry gives nobody anything)
        }
        if flags.contains("IO") || matches!(sid, "CO" | "OW" | "S-1-3-0" | "S-1-3-4") {
            continue; // (only for what is made inside later, or for whoever makes or owns it)
        }
        let mask = rights_mask(rights).ok_or_else(|| format!("rights that can't be read: {rights}"))?;
        if mask & CHANGE != 0 && !trusted(sid) && !others.iter().any(|o| o == sid) {
            others.push(sid.to_string());
        }
    }
    Ok(others)
}

/// One of `others_who_can_change`'s names in words, where SDDL has a short name for it.
pub fn in_words(who: &str) -> String {
    if let Some(owner) = who.strip_prefix("owner ") {
        return format!("its owner, {}", in_words(owner));
    }
    let name = match who {
        "BU" | "S-1-5-32-545" => "Users",
        "AU" | "S-1-5-11" => "Authenticated Users",
        "WD" | "S-1-1-0" => "Everyone",
        "IU" | "S-1-5-4" => "Interactive users",
        "DU" => "Domain Users",
        "BG" | "LG" | "DG" => "Guests",
        "PU" => "Power Users",
        _ => return who.to_string(),
    };
    format!("{name} ({who})")
}

/// The same for a file or folder on disk (`Err`: it isn't there, or Windows wouldn't say).
#[cfg(windows)]
pub fn path_open_to(path: &std::path::Path) -> Result<Vec<String>, String> {
    let sddl = crate::log::security_sddl(path).ok_or_else(|| format!("{} can't be checked", path.display()))?;
    others_who_can_change(&sddl)
}

/// What of PlanetPDF's install can be changed by someone other than an administrator: the
/// service's program and its folder (`service_exe`: …\printer\planetpdf-print-service.exe), the
/// install folder above it, and the launcher there. One line per finding; empty when it's closed.
#[cfg(windows)]
pub fn open_parts(service_exe: &std::path::Path) -> Vec<String> {
    let mut found = Vec::new();
    let folder = service_exe.parent();
    let root = folder.and_then(|f| f.parent());
    let launcher = root.map(|r| r.join("PlanetPDF.exe"));
    let parts = [Some(service_exe), folder, root, launcher.as_deref().filter(|l| l.is_file())];
    for part in parts.into_iter().flatten() {
        match path_open_to(part) {
            Ok(others) if others.is_empty() => {}
            Ok(others) => {
                let who: Vec<String> = others.iter().map(|o| in_words(o)).collect();
                found.push(format!("{} can be changed by {}", part.display(), who.join(", ")));
            }
            Err(why) => found.push(format!("{}: {why}", part.display())),
        }
    }
    found
}

#[cfg(test)]
mod tests {
    use super::{others_who_can_change as others, rights_mask};

    const PROGRAM_FILES: &str = "O:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)(A;OICI;0x1200a9;;;AC)";

    #[test]
    fn a_folder_as_program_files_keeps_it_is_closed() {
        assert_eq!(others(PROGRAM_FILES), Ok(vec![]));
        // (as Windows itself writes Program Files' own: TrustedInstaller, and creators' rights
        // only on what they make)
        let windows = "O:S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464D:PAI\
                       (A;;0x1301bf;;;S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464)\
                       (A;CIIO;GA;;;S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464)\
                       (A;;0x1301bf;;;SY)(A;OICIIO;GA;;;SY)(A;;0x1301bf;;;BA)(A;OICIIO;GA;;;BA)\
                       (A;;0x1200a9;;;BU)(A;OICIIO;GXGR;;;BU)(A;OICIIO;GA;;;CO)(A;;0x1200a9;;;AC)";
        assert_eq!(others(windows), Ok(vec![]));
        assert_eq!(others("O:SYD:P(A;OICI;FA;;;SY)(A;OICI;FRFX;;;WD)"), Ok(vec![]));
        assert_eq!(others("O:S-1-5-21-1-2-3-512D:(A;;FA;;;S-1-5-21-1-2-3-500)"), Ok(vec![])); // (domain administrators)
    }

    #[test]
    fn a_folder_users_can_write_is_open() {
        // (a new folder on a data drive: signed-in users may modify)
        let data_drive = "O:BAD:AI(A;OICIID;FA;;;BA)(A;OICIID;FA;;;SY)(A;OICIID;0x1301bf;;;AU)(A;OICIID;0x1200a9;;;BU)";
        assert_eq!(others(data_drive), Ok(vec!["AU".to_string()]));
        assert_eq!(others("O:BAD:(A;;FA;;;BA)(A;;FW;;;BU)(A;;GA;;;WD)(A;;FW;;;BU)"),
                   Ok(vec!["BU".to_string(), "WD".to_string()]));
        assert_eq!(others("O:BAD:(A;;FA;;;BA)(A;;SD;;;BU)"), Ok(vec!["BU".to_string()])); // (delete alone)
        assert_eq!(others("O:BAD:(A;;FA;;;BA)(A;;WD;;;S-1-5-21-1-2-3-1001)"),
                   Ok(vec!["S-1-5-21-1-2-3-1001".to_string()])); // (may change the permissions)
        assert_eq!(others("O:BAD:NO_ACCESS_CONTROL"), Ok(vec!["everyone (no permissions are set)".to_string()]));
    }

    #[test]
    fn an_owner_who_is_not_an_administrator_counts() {
        // (whoever owns a file can always give themselves the rest)
        assert_eq!(others("O:S-1-5-21-1-2-3-1001D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"),
                   Ok(vec!["owner S-1-5-21-1-2-3-1001".to_string()]));
        assert_eq!(others("O:BUG:BUD:(A;;FA;;;BA)"), Ok(vec!["owner BU".to_string()]));
    }

    #[test]
    fn refusals_and_entries_for_later_give_nothing() {
        assert_eq!(others("O:BAD:(D;;FA;;;BU)(A;OICIIO;FA;;;BU)(A;;FA;;;CO)(A;;FR;;;BU)"), Ok(vec![]));
    }

    #[test]
    fn what_cannot_be_read_is_not_taken_for_closed() {
        assert!(others("").is_err());
        assert!(others("D:(A;;FA;;;BA)").is_err());
        assert!(others("O:BA").is_err());
        assert!(others("O:BAD:(A;;??;;;BU)").is_err());
        assert!(others("O:BAD:(A;;FA)").is_err());
    }

    #[cfg(windows)]
    #[test]
    fn real_files_and_folders_are_read() {
        let system = std::path::PathBuf::from(crate::service::system_dir());
        assert_eq!(super::path_open_to(&system), Ok(vec![]));
        assert_eq!(super::path_open_to(&system.join("kernel32.dll")), Ok(vec![]));
        // the user's own temporary folder: theirs to change
        assert!(!super::path_open_to(&std::env::temp_dir()).unwrap_or_default().is_empty());
        assert!(super::path_open_to(&system.join("no-such-file-here.dll")).is_err());
    }

    #[test]
    fn the_usual_groups_are_named() {
        assert_eq!(super::in_words("AU"), "Authenticated Users (AU)");
        assert_eq!(super::in_words("owner BU"), "its owner, Users (BU)");
        assert_eq!(super::in_words("S-1-5-21-1-2-3-1001"), "S-1-5-21-1-2-3-1001");
    }

    #[test]
    fn rights_are_read_both_ways() {
        assert_eq!(rights_mask("0x1200a9"), Some(0x1200a9));
        assert_eq!(rights_mask("FRFX"), Some(0x0012_0089 | 0x0012_00A0));
        assert_eq!(rights_mask("FA").map(|m| m & super::CHANGE != 0), Some(true));
        assert_eq!(rights_mask("F"), None);
    }
}
