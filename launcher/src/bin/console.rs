//! The command-line launcher (`PlanetPDF.com` beside `PlanetPDF.exe` in the install folder;
//! 2026-10-18). Command Prompt and PowerShell run a `.com` before an `.exe` of the same name, so
//! `planetpdf info file.pdf` comes here: a console program that runs
//! `versions\<current>\planetpdf-cli.exe` (the console build of PlanetPDF) in this console,
//! waits for it, and returns its exit code. Double-clicks, shortcuts and file associations still
//! use the windowed `PlanetPDF.exe`.

use std::path::Path;
use std::process::{Command, ExitCode};

#[path = "../common.rs"]
mod common;

const EXE_NAME: &str = if cfg!(windows) { "planetpdf-cli.exe" } else { "planetpdf-cli" };

fn main() -> ExitCode {
    let root = match std::env::current_exe() {
        Ok(exe) => exe.parent().map(Path::to_path_buf).unwrap_or_default(),
        Err(e) => {
            eprintln!("planetpdf: couldn't find where PlanetPDF is installed: {e}");
            return ExitCode::FAILURE;
        }
    };
    let Some(target) = common::program(&root, EXE_NAME) else {
        eprintln!("planetpdf: no version with the command line was found in {}.",
                  root.join(common::VERSIONS).display());
        return ExitCode::FAILURE;
    };
    match Command::new(&target).args(std::env::args_os().skip(1)).status() {
        Ok(status) => ExitCode::from(status.code().unwrap_or(1).clamp(0, 255) as u8),
        Err(e) => {
            eprintln!("planetpdf: couldn't start {}: {e}", target.display());
            ExitCode::FAILURE
        }
    }
}
