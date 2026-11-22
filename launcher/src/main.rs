//! PlanetPDF's launcher (`PlanetPDF.exe` at the top of the install folder): starts
//! `versions\<current>\PlanetPDF.exe` with the same arguments (the files to open) and exits.
//! Shortcuts and file associations point here, so they never change when an update installs
//! a new version folder. It never checks for updates.
//!
//! Which version: the one named in `current.txt` if that folder has the program, else the
//! newest version folder that has it (the same rules as `core/update.py current_version`).

#![windows_subsystem = "windows"]

use std::ffi::OsString;
use std::path::Path;
use std::process::{Command, ExitCode};

mod common;
use common::{VERSIONS, program};

const EXE_NAME: &str = if cfg!(windows) { "PlanetPDF.exe" } else { "PlanetPDF" };

#[cfg(windows)]
fn show_error(text: &str) {
    use std::ffi::c_void;
    #[link(name = "user32")]
    unsafe extern "system" {
        fn MessageBoxW(hwnd: *mut c_void, text: *const u16, caption: *const u16, kind: u32) -> i32;
    }
    let wide = |s: &str| s.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>();
    let (text, caption) = (wide(text), wide("PlanetPDF"));
    // SAFETY: both strings are NUL-terminated UTF-16 and live until the call returns.
    unsafe {
        MessageBoxW(std::ptr::null_mut(), text.as_ptr(), caption.as_ptr(), 0x10); // MB_ICONERROR
    }
}

#[cfg(not(windows))]
fn show_error(text: &str) {
    eprintln!("{text}");
}

fn main() -> ExitCode {
    let root = match std::env::current_exe() {
        Ok(exe) => exe.parent().map(Path::to_path_buf).unwrap_or_default(),
        Err(e) => {
            show_error(&format!("PlanetPDF couldn't find where it's installed: {e}"));
            return ExitCode::FAILURE;
        }
    };
    let Some(target) = program(&root, EXE_NAME) else {
        show_error(&format!(
            "PlanetPDF isn't installed properly: no version was found in {}.",
            root.join(VERSIONS).display()
        ));
        return ExitCode::FAILURE;
    };
    let args: Vec<OsString> = std::env::args_os().skip(1).collect();
    match Command::new(&target).args(&args).spawn() {
        Ok(_) => ExitCode::SUCCESS,
        Err(e) => {
            show_error(&format!("PlanetPDF couldn't start {}:\n\n{e}", target.display()));
            ExitCode::FAILURE
        }
    }
}

