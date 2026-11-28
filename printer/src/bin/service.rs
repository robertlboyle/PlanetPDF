//! planetpdf-print-service.exe: the PlanetPDF Print Service (installed and started by
//! planetpdf-printer-setup.exe). `--console` runs it by hand, for testing, until it's closed.

#[cfg(not(windows))]
fn main() {
    eprintln!("The PlanetPDF printer is for Windows.");
    std::process::exit(2);
}

#[cfg(windows)]
fn main() {
    planetpdf_printer::system32_libraries_only();
    if std::env::args().any(|a| a == "--console") {
        planetpdf_printer::service::serve(true);
        return;
    }
    if !planetpdf_printer::service::run_as_service() {
        eprintln!("This is the PlanetPDF Print Service: Windows starts it. (--console runs it by hand.)");
        std::process::exit(1);
    }
}
