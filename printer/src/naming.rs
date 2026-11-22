//! What a printout's file is called: the printed document's name, made safe for a file name,
//! never overwriting an earlier printout ("Quote 1043.pdf", "Quote 1043 (2).pdf"). PlanetPDF then
//! asks where to save it (or uses the folder and name template set in Preferences).

use std::path::{Path, PathBuf};

/// PlanetPDF's inbox for printouts in the user's local app data (Qt's AppLocalDataLocation +
/// "printouts", ui/paths.printouts_dir): PlanetPDF takes anything there as a printout.
pub const INBOX: [&str; 3] = ["Planet Networks", "PlanetPDF", "printouts"];
const FALLBACK: &str = "Printout";
const MAX_CHARS: usize = 120;
/// Programs that put their own name in front of the document's (older Office, some browsers).
const PREFIXES: [&str; 6] = ["Microsoft Word - ", "Microsoft Excel - ", "Microsoft PowerPoint - ", "Microsoft Outlook - ",
                             "Notepad - ", "Untitled - "];
/// The document's own extension goes: "Quote 1043.docx" prints as "Quote 1043.pdf".
const EXTENSIONS: [&str; 18] = [".docx", ".docm", ".doc", ".xlsx", ".xlsm", ".xls", ".pptx", ".ppt", ".txt", ".rtf",
                                ".htm", ".html", ".pdf", ".msg", ".eml", ".csv", ".odt", ".xps"];

/// A file name (without ".pdf") from the print job's document name.
pub fn file_stem(document: &str) -> String {
    let mut name = document.trim().to_string();
    for prefix in PREFIXES {
        if name.len() > prefix.len() && name.to_ascii_lowercase().starts_with(&prefix.to_ascii_lowercase()) {
            name = name[prefix.len()..].to_string();
            break;
        }
    }
    // A path ("C:\Users\…\Quote.docx") or an address ("https://…/page"): its last part. (A slash
    // alone isn't a path: "March/April".)
    if name.contains('\\') || name.contains("://") {
        if let Some(last) = name.rsplit(['\\', '/']).find(|part| !part.trim().is_empty()) {
            name = last.to_string();
        }
    }
    let lower = name.to_ascii_lowercase();
    if let Some(ext) = EXTENSIONS.iter().find(|ext| lower.ends_with(*ext) && lower.len() > ext.len()) {
        name.truncate(name.len() - ext.len());
    }
    let mut clean: String = name
        .chars()
        .map(|c| if c.is_control() || "<>:\"/\\|?*".contains(c) { ' ' } else { c })
        .collect();
    clean = clean.split_whitespace().collect::<Vec<_>>().join(" ");
    if clean.chars().count() > MAX_CHARS {
        clean = clean.chars().take(MAX_CHARS).collect();
    }
    let clean = clean.trim_matches(|c: char| c == '.' || c == ' ').to_string();
    if clean.is_empty() || is_reserved(&clean) { FALLBACK.to_string() } else { clean }
}

/// Windows' reserved device names (CON, PRN, AUX, NUL, COM1…, LPT1…) can't be file names.
fn is_reserved(stem: &str) -> bool {
    let upper = stem.to_ascii_uppercase();
    let base = upper.split('.').next().unwrap_or("");
    matches!(base, "CON" | "PRN" | "AUX" | "NUL")
        || ((base.starts_with("COM") || base.starts_with("LPT"))
            && base.len() == 4
            && base.as_bytes()[3].is_ascii_digit())
}

/// The first "<stem>.pdf", "<stem> (2).pdf", … in `folder` that doesn't exist yet.
pub fn unique_path(folder: &Path, stem: &str) -> PathBuf {
    let first = folder.join(format!("{stem}.pdf"));
    if !first.exists() {
        return first;
    }
    (2..)
        .map(|n| folder.join(format!("{stem} ({n}).pdf")))
        .find(|p| !p.exists())
        .unwrap_or(first)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_from_documents() {
        assert_eq!(file_stem("Quote 1043.docx"), "Quote 1043");
        assert_eq!(file_stem("Microsoft Word - Quote 1043.doc"), "Quote 1043");
        assert_eq!(file_stem(r"C:\Users\Robert\Documents\Budget 2027.xlsx"), "Budget 2027");
        assert_eq!(file_stem("Invoice: March/April?"), "Invoice March April");
        assert_eq!(file_stem("Fiber map v2.1"), "Fiber map v2.1");  // (not an extension we strip)
        assert_eq!(file_stem("Report.pdf"), "Report");
        assert_eq!(file_stem("  ...  "), "Printout");
        assert_eq!(file_stem(""), "Printout");
        assert_eq!(file_stem("CON"), "Printout");
        assert_eq!(file_stem("lpt1.txt"), "Printout");
        assert_eq!(file_stem("Tab\there\nand there"), "Tab here and there");
        assert_eq!(file_stem(&"x".repeat(300)).chars().count(), 120);
        assert_eq!(file_stem("https://www.planet.net/fiber/"), "fiber");
    }

    #[test]
    fn never_overwrites() {
        let dir = std::env::temp_dir().join(format!("planetpdf-naming-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let first = unique_path(&dir, "Quote");
        assert_eq!(first.file_name().unwrap(), "Quote.pdf");
        std::fs::write(&first, b"1").unwrap();
        let second = unique_path(&dir, "Quote");
        assert_eq!(second.file_name().unwrap(), "Quote (2).pdf");
        std::fs::write(&second, b"2").unwrap();
        assert_eq!(unique_path(&dir, "Quote").file_name().unwrap(), "Quote (3).pdf");
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
