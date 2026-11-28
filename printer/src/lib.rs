//! The PlanetPDF printer (user 2026-10-18: print from any program to a PDF that opens in
//! PlanetPDF, on PCs and terminal servers alike).
//!
//! The "PlanetPDF" printer prints with Windows' own Microsoft Print To PDF driver to a Local Port
//! that is a named pipe, `\\.\pipe\PlanetPDF`. Our service (service.rs, LocalSystem) listens
//! there: it accepts only the print spooler, keeps the PDF in its private folder, asks the
//! printer's queue whose job it is, then hands it to that person: PlanetPDF's inbox in their
//! local app data, written as them, and PlanetPDF started in their session (deliver.rs), which
//! asks where to save it.
//!
//! (A port monitor of our own was the first design; Windows refuses Microsoft Print To PDF with
//! any non-inbox port monitor, and the service keeps our code out of the spooler anyway.)

pub mod acl;
pub mod naming;

#[cfg(windows)]
pub mod deliver;
#[cfg(windows)]
pub mod ffi;
#[cfg(windows)]
pub mod log;
#[cfg(windows)]
pub mod service;

/// Every library loaded while this program runs (not only the ones it imports: those are
/// `/DEPENDENTLOADFLAG`, `.cargo/config.toml`) comes from Windows' System32 folder, never from
/// this program's own folder (security review 2026-11-27, finding 3: the print service loaded a
/// DLL a user had put beside it). Called first thing in `main`.
#[cfg(windows)]
pub fn system32_libraries_only() {
    const LOAD_LIBRARY_SEARCH_SYSTEM32: u32 = 0x800;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn SetDefaultDllDirectories(flags: u32) -> i32;
    }
    // SAFETY: a documented flag; it only narrows where Windows looks.
    unsafe { SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32) };
}

pub const PIPE_NAME: &str = r"\\.\pipe\PlanetPDF";
pub const SERVICE_NAME: &str = "PlanetPDFPrint";
pub const SERVICE_DISPLAY_NAME: &str = "PlanetPDF Print Service";
pub const SERVICE_DESCRIPTION: &str =
    "Receives what is printed to the PlanetPDF printer and opens it in PlanetPDF for the person who printed it.";
pub const SERVICE_EXE: &str = "planetpdf-print-service.exe";
pub const PRINTER_NAME: &str = "PlanetPDF";
pub const DRIVER_NAME: &str = "Microsoft Print To PDF";
/// The first design's port monitor (2026-10-18), removed where an early test left it.
pub const OLD_MONITOR_NAME: &str = "PlanetPDF Port";
pub const OLD_MONITOR_DLL: &str = "planetpdf_port.dll";
