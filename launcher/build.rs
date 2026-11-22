//! Gives the launcher PlanetPDF's icon (shortcuts and "Open with" show it) and a version
//! resource with PlanetPDF's version, read from src/planetpdf/__init__.py (one place to change).

use std::path::PathBuf;

fn main() {
    let manifest = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let project = manifest.parent().unwrap().to_path_buf();
    let init = project.join("src").join("planetpdf").join("__init__.py");
    let icon = project.join("src").join("planetpdf").join("resources").join("icons").join("planetpdf.ico");
    println!("cargo:rerun-if-changed={}", init.display());
    println!("cargo:rerun-if-changed={}", icon.display());
    println!("cargo:rerun-if-changed=build.rs");

    let text = std::fs::read_to_string(&init).unwrap_or_default();
    let version = text
        .lines()
        .find_map(|l| l.trim().strip_prefix("__version__"))
        .and_then(|rest| rest.split('"').nth(1))
        .unwrap_or("0.0.0")
        .to_string();
    let mut parts: Vec<u16> = version.split('.').filter_map(|p| p.parse().ok()).collect();
    parts.resize(4, 0);
    let numbers = parts.iter().map(|n| n.to_string()).collect::<Vec<_>>().join(",");

    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let icon_path = icon.display().to_string().replace('\\', "\\\\");
    let rc = format!(
        r#"1 ICON "{icon_path}"
1 VERSIONINFO
FILEVERSION {numbers}
PRODUCTVERSION {numbers}
FILEOS 0x40004
FILETYPE 0x1
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904B0"
    BEGIN
      VALUE "CompanyName", "Planet Networks"
      VALUE "FileDescription", "PlanetPDF"
      VALUE "FileVersion", "{version}"
      VALUE "InternalName", "PlanetPDF launcher"
      VALUE "OriginalFilename", "PlanetPDF.exe"
      VALUE "ProductName", "PlanetPDF"
      VALUE "ProductVersion", "{version}"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
"#
    );
    let out = PathBuf::from(std::env::var("OUT_DIR").unwrap()).join("launcher.rc");
    std::fs::write(&out, rc).unwrap();
    embed_resource::compile(&out, embed_resource::NONE).manifest_optional().unwrap();
}
