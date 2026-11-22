//! What both launchers share: which installed version to start (the same rules as
//! `core/update.py current_version`).

use std::path::{Path, PathBuf};

pub const CURRENT_FILE: &str = "current.txt";
pub const VERSIONS: &str = "versions";

/// "1.10.2" -> [1, 10, 2]; None unless it's only numbers and dots.
pub fn version_numbers(name: &str) -> Option<Vec<u64>> {
    if name.is_empty() || name.starts_with('.') || name.ends_with('.') || name.contains("..") {
        return None;
    }
    name.split('.').map(|p| if p.chars().all(|c| c.is_ascii_digit()) { p.parse().ok() } else { None }).collect()
}

pub fn compare(a: &[u64], b: &[u64]) -> std::cmp::Ordering {
    let width = a.len().max(b.len());
    let pad = |v: &[u64]| {
        let mut v = v.to_vec();
        v.resize(width, 0);
        v
    };
    pad(a).cmp(&pad(b))
}

/// `exe` (a file in a version folder) of the version to start, or None when no version is installed.
pub fn program(root: &Path, exe: &str) -> Option<PathBuf> {
    let versions = root.join(VERSIONS);
    if let Ok(text) = std::fs::read_to_string(root.join(CURRENT_FILE)) {
        let name = text.trim().trim_start_matches('\u{feff}');
        if !name.is_empty() {
            let exe = versions.join(name).join(exe);
            if exe.is_file() {
                return Some(exe);
            }
        }
    }
    let mut best: Option<(Vec<u64>, PathBuf)> = None;
    for entry in std::fs::read_dir(&versions).ok()?.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        let Some(numbers) = version_numbers(&name) else { continue };
        let exe = entry.path().join(exe);
        if !exe.is_file() {
            continue;
        }
        if best.as_ref().is_none_or(|(b, _)| compare(&numbers, b).is_gt()) {
            best = Some((numbers, exe));
        }
    }
    best.map(|(_, exe)| exe)
}

#[cfg(test)]
mod tests {
    use super::*;

    const EXE_NAME: &str = "PlanetPDF.exe";

    fn install(versions: &[&str], current: Option<&str>) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "ppdf-launcher-{}-{}",
            std::process::id(),
            versions.join("_") + current.unwrap_or("none")
        ));
        let _ = std::fs::remove_dir_all(&root);
        for v in versions {
            let dir = root.join(VERSIONS).join(v);
            std::fs::create_dir_all(&dir).unwrap();
            std::fs::write(dir.join(EXE_NAME), b"").unwrap();
        }
        std::fs::create_dir_all(root.join(VERSIONS)).unwrap();
        if let Some(c) = current {
            std::fs::write(root.join(CURRENT_FILE), format!("{c}\r\n")).unwrap();
        }
        root
    }

    fn chosen(root: &Path) -> Option<String> {
        program(root, EXE_NAME).map(|p| p.parent().unwrap().file_name().unwrap().to_string_lossy().into_owned())
    }

    #[test]
    fn current_txt_wins_when_its_folder_has_the_program() {
        let root = install(&["1.2.0", "1.10.0"], Some("1.2.0"));
        assert_eq!(chosen(&root).as_deref(), Some("1.2.0"));
    }

    #[test]
    fn otherwise_the_newest_version_by_number() {
        let root = install(&["1.2.0", "1.10.0", "1.9"], Some("2.0.0"));
        assert_eq!(chosen(&root).as_deref(), Some("1.10.0"));
        let root = install(&["0.9", "0.10.1"], None);
        assert_eq!(chosen(&root).as_deref(), Some("0.10.1"));
    }

    #[test]
    fn folders_that_arent_versions_or_lack_the_program_are_ignored() {
        let root = install(&["1.0.0"], None);
        std::fs::create_dir_all(root.join(VERSIONS).join("9.9.9")).unwrap(); // (no program in it)
        std::fs::create_dir_all(root.join(VERSIONS).join("backup-2.0")).unwrap();
        assert_eq!(chosen(&root).as_deref(), Some("1.0.0"));
    }

    #[test]
    fn nothing_installed() {
        let root = install(&[], None);
        assert_eq!(chosen(&root), None);
        assert_eq!(program(&std::env::temp_dir().join("ppdf-launcher-missing"), EXE_NAME), None);
    }

    #[test]
    fn version_names() {
        assert_eq!(version_numbers("1.10.2"), Some(vec![1, 10, 2]));
        assert_eq!(version_numbers("1."), None);
        assert_eq!(version_numbers("v1"), None);
        assert!(compare(&[1, 0], &[1, 0, 0]).is_eq());
    }
}
