//! planetpdf-printer-setup.exe: adds or removes the PlanetPDF printer (run by the installer, as
//! administrator).
//!
//!   planetpdf-printer-setup install     the service (started), the pipe port, the printer
//!   planetpdf-printer-setup uninstall   the printer, the port, the service
//!   planetpdf-printer-setup stop        stops the service (the installer, before replacing files)
//!   planetpdf-printer-setup status      what is there (exit 0 when everything is)
//!
//! The service program is taken from beside this one.

#[cfg(not(windows))]
fn main() {
    eprintln!("The PlanetPDF printer is for Windows.");
    std::process::exit(2);
}

#[cfg(windows)]
fn main() {
    planetpdf_printer::system32_libraries_only();
    let action = std::env::args().nth(1).unwrap_or_default();
    let result = match action.as_str() {
        "install" => windows::install(),
        "uninstall" => windows::uninstall(),
        "stop" => windows::stop_service().map(|_| "The PlanetPDF Print Service is stopped.".to_string()),
        "status" => windows::status(),
        "check-folder" => windows::check_folder(std::env::args().nth(2).unwrap_or_default()),
        _ => Err("Use: planetpdf-printer-setup install | uninstall | stop | status | check-folder FOLDER".to_string()),
    };
    match result {
        Ok(message) => println!("{message}"),
        Err(message) => {
            eprintln!("{message}");
            std::process::exit(1);
        }
    }
}

#[cfg(windows)]
mod windows {
    use std::path::PathBuf;
    use std::process::Command;
    use std::time::{Duration, Instant};

    use planetpdf_printer::acl;
    use planetpdf_printer::ffi::{self, HANDLE};
    use planetpdf_printer::log::spool_report;
    use planetpdf_printer::service::{describe_holder, pipe_server_pid, system_dir};
    use planetpdf_printer::{DRIVER_NAME, OLD_MONITOR_DLL, OLD_MONITOR_NAME, PIPE_NAME, PRINTER_NAME,
                            SERVICE_DESCRIPTION, SERVICE_DISPLAY_NAME, SERVICE_EXE, SERVICE_NAME};

    const PDF_FEATURE: &str = "Printing-PrintToPDFServices-Features";
    /// A port left by the pipe tests of 2026-10-18 (removed with the rest).
    const TEST_PORT: &str = r"\\.\pipe\PlanetPDFTest";

    fn last_error() -> u32 {
        // SAFETY: reads this thread's last-error value.
        unsafe { ffi::GetLastError() }
    }

    fn describe(code: u32) -> String {
        std::io::Error::from_raw_os_error(code as i32).to_string()
    }

    /// Windows' System32 folder, asked of Windows: this runs as administrator, and neither the
    /// folder nor the programs started from it are taken from the environment (SystemRoot, PATH).
    fn system32() -> PathBuf {
        PathBuf::from(system_dir())
    }

    /// Windows PowerShell for the print cmdlets (every name in the script is one of ours).
    fn powershell(script: &str) -> Result<(), String> {
        let output = Command::new(system32().join("WindowsPowerShell").join("v1.0").join("powershell.exe"))
            .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])
            .output()
            .map_err(|e| e.to_string())?;
        if output.status.success() {
            Ok(())
        } else {
            Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
        }
    }

    // --- what's there --------------------------------------------------------------------------

    /// An Enum…W call: asks for the size, then fills an 8-byte aligned buffer.
    fn enumerate(call: impl Fn(*mut u8, u32, *mut u32, *mut u32) -> i32) -> (Vec<u64>, usize) {
        let (mut needed, mut count) = (0u32, 0u32);
        call(std::ptr::null_mut(), 0, &mut needed, &mut count);
        if needed == 0 {
            return (Vec::new(), 0);
        }
        let mut buffer = vec![0u64; (needed as usize).div_ceil(8)];
        if call(buffer.as_mut_ptr() as *mut u8, needed, &mut needed, &mut count) == 0 {
            return (Vec::new(), 0);
        }
        (buffer, count as usize)
    }

    /// The first string of each of `count` entries of `size` bytes.
    fn names(buffer: &[u64], count: usize, size: usize) -> Vec<String> {
        let base = buffer.as_ptr() as *const u8;
        (0..count)
            .map(|i| {
                // SAFETY: Windows filled `count` entries of `size` bytes, each starting with a string pointer.
                unsafe { ffi::from_wide(*(base.add(i * size) as *const *const u16)) }
            })
            .collect()
    }

    fn ports() -> Vec<String> {
        let (buffer, count) = enumerate(|b, s, n, c| unsafe { ffi::EnumPortsW(std::ptr::null(), 1, b, s, n, c) });
        names(&buffer, count, size_of::<ffi::PORT_INFO_1W>())
    }

    fn port_listed(port: &str) -> bool {
        ports().iter().any(|n| n.eq_ignore_ascii_case(port))
    }

    fn driver_installed() -> bool {
        let (buffer, count) = enumerate(|b, s, n, c| unsafe {
            ffi::EnumPrinterDriversW(std::ptr::null(), std::ptr::null(), 1, b, s, n, c)
        });
        names(&buffer, count, size_of::<ffi::DRIVER_INFO_1W>()).iter().any(|n| n.eq_ignore_ascii_case(DRIVER_NAME))
    }

    fn old_monitor_installed() -> bool {
        let (buffer, count) = enumerate(|b, s, n, c| unsafe { ffi::EnumMonitorsW(std::ptr::null(), 1, b, s, n, c) });
        names(&buffer, count, size_of::<ffi::MONITOR_INFO_1W>()).iter().any(|n| n == OLD_MONITOR_NAME)
    }

    fn open_printer(access: u32) -> Option<HANDLE> {
        let name = ffi::wide(PRINTER_NAME);
        let defaults = ffi::PRINTER_DEFAULTSW { pDatatype: std::ptr::null_mut(), pDevMode: std::ptr::null_mut(),
                                                DesiredAccess: access };
        let mut handle: HANDLE = std::ptr::null_mut();
        // SAFETY: a NUL-terminated name and valid defaults.
        let ok = unsafe {
            ffi::OpenPrinterW(name.as_ptr(), &mut handle, if access == 0 { std::ptr::null() } else { &defaults })
        };
        (ok != 0).then_some(handle)
    }

    fn printer_exists() -> bool {
        match open_printer(0) {
            Some(handle) => {
                // SAFETY: a handle OpenPrinterW gave us.
                unsafe { ffi::ClosePrinter(handle) };
                true
            }
            None => false,
        }
    }

    // --- the service ---------------------------------------------------------------------------

    struct Sc(HANDLE);

    impl Drop for Sc {
        fn drop(&mut self) {
            // SAFETY: a service-control handle we own.
            unsafe { ffi::CloseServiceHandle(self.0) };
        }
    }

    fn manager(access: u32) -> Result<Sc, String> {
        // SAFETY: opens the local Service Control Manager.
        let handle = unsafe { ffi::OpenSCManagerW(std::ptr::null(), std::ptr::null(), access) };
        if handle.is_null() {
            return Err(format!("the Service Control Manager: {}", describe(last_error())));
        }
        Ok(Sc(handle))
    }

    fn open_service(manager: &Sc, access: u32) -> Option<Sc> {
        let name = ffi::wide(SERVICE_NAME);
        // SAFETY: a NUL-terminated service name.
        let handle = unsafe { ffi::OpenServiceW(manager.0, name.as_ptr(), access) };
        (!handle.is_null()).then_some(Sc(handle))
    }

    fn service_state(service: &Sc) -> u32 {
        let mut status = ffi::SERVICE_STATUS_PROCESS::default();
        let mut needed = 0;
        // SAFETY: a SERVICE_STATUS_PROCESS-sized buffer.
        let ok = unsafe {
            ffi::QueryServiceStatusEx(service.0, ffi::SC_STATUS_PROCESS_INFO, &mut status as *mut _ as *mut u8,
                                      size_of::<ffi::SERVICE_STATUS_PROCESS>() as u32, &mut needed)
        };
        if ok != 0 { status.dwCurrentState } else { 0 }
    }

    /// The service's process (0 while it isn't running).
    fn service_pid(service: &Sc) -> u32 {
        let mut status = ffi::SERVICE_STATUS_PROCESS::default();
        let mut needed = 0;
        // SAFETY: a SERVICE_STATUS_PROCESS-sized buffer.
        let ok = unsafe {
            ffi::QueryServiceStatusEx(service.0, ffi::SC_STATUS_PROCESS_INFO, &mut status as *mut _ as *mut u8,
                                      size_of::<ffi::SERVICE_STATUS_PROCESS>() as u32, &mut needed)
        };
        if ok != 0 && status.dwCurrentState == ffi::SERVICE_RUNNING { status.dwProcessId } else { 0 }
    }

    /// Whether the pipe is served by the service's own process, as Windows says (security review
    /// 2026-11-27, finding 4: a program that held the pipe's name before the first install got
    /// the printer added for it, and received other people's printouts; "a pipe of that name is
    /// there" proved nothing).
    fn pipe_is_services(service: &Sc) -> bool {
        let pid = service_pid(service);
        pid != 0 && pipe_server_pid(PIPE_NAME) == Some(pid)
    }

    /// Waits up to `seconds` for the service to run and hold its pipe. Err says what holds it
    /// instead.
    fn wait_for_pipe(service: &Sc, seconds: u64) -> Result<(), String> {
        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(seconds) {
            if pipe_is_services(service) {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        Err(match pipe_server_pid(PIPE_NAME) {
            Some(_) => format!("The PlanetPDF printer wasn't added: another program holds its pipe, {PIPE_NAME}                                 ({}), so printouts would go to that program. End it (or restart the computer),                                 then run PlanetPDF's installer again.", describe_holder(PIPE_NAME)),
            None => "The PlanetPDF printer wasn't added: the PlanetPDF Print Service didn't open its pipe                      (see %ProgramData%\\PlanetPDF\\Spool\\printer.log, or Windows' Application event log)."
                .to_string(),
        })
    }

    fn wait_for_state(service: &Sc, state: u32, seconds: u64) -> bool {
        let start = Instant::now();
        while start.elapsed() < Duration::from_secs(seconds) {
            if service_state(service) == state {
                return true;
            }
            std::thread::sleep(Duration::from_millis(200));
        }
        false
    }

    pub fn stop_service() -> Result<(), String> {
        let manager = manager(ffi::SC_MANAGER_ALL_ACCESS)?;
        let Some(service) = open_service(&manager, ffi::SERVICE_ALL_ACCESS) else { return Ok(()) };
        if service_state(&service) != ffi::SERVICE_STOPPED {
            let mut status = ffi::SERVICE_STATUS::default();
            // SAFETY: asks our service to stop.
            unsafe { ffi::ControlService(service.0, ffi::SERVICE_CONTROL_STOP, &mut status) };
            if !wait_for_state(&service, ffi::SERVICE_STOPPED, 20) {
                return Err("the PlanetPDF Print Service didn't stop".to_string());
            }
        }
        Ok(())
    }

    fn install_service() -> Result<(), String> {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?.with_file_name(SERVICE_EXE);
        if !exe.is_file() {
            return Err(format!("{SERVICE_EXE} isn't beside this program"));
        }
        // The service runs as SYSTEM: never from where someone other than an administrator can
        // put another program in its place (security list 2026-11-26, item 3).
        let open = acl::open_parts(&exe);
        if !open.is_empty() {
            return Err(format!("The PlanetPDF printer wasn't added: its service runs with system rights, and here \
                                other users could replace its program.\n{}\nInstall PlanetPDF in Program Files, \
                                or let only administrators write to its folder, then run this again.",
                               open.join("\n")));
        }
        let manager = manager(ffi::SC_MANAGER_ALL_ACCESS)?;
        let name = ffi::wide(SERVICE_NAME);
        let display = ffi::wide(SERVICE_DISPLAY_NAME);
        let path = ffi::wide(&format!("\"{}\"", exe.display()));
        let service = match open_service(&manager, ffi::SERVICE_ALL_ACCESS) {
            Some(service) => {
                stop_service()?;
                // SAFETY: points the existing service at this copy of the program.
                let ok = unsafe {
                    ffi::ChangeServiceConfigW(service.0, ffi::SERVICE_NO_CHANGE, ffi::SERVICE_AUTO_START,
                                              ffi::SERVICE_NO_CHANGE, path.as_ptr(), std::ptr::null(),
                                              std::ptr::null_mut(), std::ptr::null(), std::ptr::null(),
                                              std::ptr::null(), display.as_ptr())
                };
                if ok == 0 {
                    return Err(format!("the service couldn't be updated: {}", describe(last_error())));
                }
                service
            }
            None => {
                // SAFETY: valid NUL-terminated strings; LocalSystem (no account name).
                let handle = unsafe {
                    ffi::CreateServiceW(manager.0, name.as_ptr(), display.as_ptr(), ffi::SERVICE_ALL_ACCESS,
                                        ffi::SERVICE_WIN32_OWN_PROCESS, ffi::SERVICE_AUTO_START,
                                        ffi::SERVICE_ERROR_NORMAL, path.as_ptr(), std::ptr::null(),
                                        std::ptr::null_mut(), std::ptr::null(), std::ptr::null(), std::ptr::null())
                };
                if handle.is_null() {
                    return Err(format!("the service couldn't be added: {}", describe(last_error())));
                }
                Sc(handle)
            }
        };
        let mut text = ffi::wide(SERVICE_DESCRIPTION);
        let description = ffi::SERVICE_DESCRIPTIONW { lpDescription: text.as_mut_ptr() };
        // SAFETY: a valid SERVICE_DESCRIPTIONW.
        unsafe {
            ffi::ChangeServiceConfig2W(service.0, ffi::SERVICE_CONFIG_DESCRIPTION, &description as *const _ as *const _)
        };
        // If the service ever ends without being told to, Windows starts it again (security list
        // 2026-11-26, item 4: until someone did by hand, jobs failed in the queue and the pipe's
        // name was free for the taking): after 5 seconds, then after 30; a third time in a day,
        // it stays down for an administrator to look at.
        let mut steps = [
            ffi::SC_ACTION { Type: ffi::SC_ACTION_RESTART, Delay: 5_000 },
            ffi::SC_ACTION { Type: ffi::SC_ACTION_RESTART, Delay: 30_000 },
            ffi::SC_ACTION { Type: ffi::SC_ACTION_NONE, Delay: 0 },
        ];
        let failure = ffi::SERVICE_FAILURE_ACTIONSW {
            dwResetPeriod: 24 * 60 * 60,
            lpRebootMsg: std::ptr::null_mut(),
            lpCommand: std::ptr::null_mut(),
            cActions: steps.len() as u32,
            lpsaActions: steps.as_mut_ptr(),
        };
        // SAFETY: a valid SERVICE_FAILURE_ACTIONSW whose array outlives the call.
        unsafe {
            ffi::ChangeServiceConfig2W(service.0, ffi::SERVICE_CONFIG_FAILURE_ACTIONS, &failure as *const _ as *const _)
        };
        // SAFETY: starts our service.
        if unsafe { ffi::StartServiceW(service.0, 0, std::ptr::null()) } == 0
            && last_error() != ffi::ERROR_SERVICE_ALREADY_RUNNING
        {
            return Err(format!("the service didn't start: {}", describe(last_error())));
        }
        if !wait_for_state(&service, ffi::SERVICE_RUNNING, 20) {
            return Err("the PlanetPDF Print Service didn't start".to_string());
        }
        wait_for_pipe(&service, 20)
    }

    fn remove_service() -> Result<bool, String> {
        stop_service()?;
        let manager = manager(ffi::SC_MANAGER_ALL_ACCESS)?;
        let Some(service) = open_service(&manager, ffi::SERVICE_ALL_ACCESS) else { return Ok(false) };
        // SAFETY: marks our service for deletion.
        if unsafe { ffi::DeleteService(service.0) } == 0 && last_error() != ffi::ERROR_SERVICE_MARKED_FOR_DELETE {
            return Err(format!("the service couldn't be removed: {}", describe(last_error())));
        }
        Ok(true)
    }

    // --- the first design's monitor (removed if an early test left it) -------------------------

    fn remove_old_monitor() {
        if old_monitor_installed() {
            let name = ffi::wide(OLD_MONITOR_NAME);
            // SAFETY: a NUL-terminated monitor name.
            unsafe { ffi::DeleteMonitorW(std::ptr::null(), std::ptr::null(), name.as_ptr()) };
        }
        let _ = std::fs::remove_file(system32().join(OLD_MONITOR_DLL));
    }

    // --- printer and port ----------------------------------------------------------------------

    fn ensure_driver() -> Result<(), String> {
        if driver_installed() {
            return Ok(());
        }
        // (Windows' "Microsoft Print to PDF" feature: on by default on PCs, sometimes off on servers)
        let _ = Command::new(system32().join("dism.exe"))
            .args(["/online", "/enable-feature", &format!("/featurename:{PDF_FEATURE}"), "/all", "/norestart",
                   "/quiet"])
            .output();
        if driver_installed() {
            Ok(())
        } else {
            Err(format!("Windows' \"{DRIVER_NAME}\" driver isn't installed and couldn't be turned on (Windows \
                         feature {PDF_FEATURE})"))
        }
    }

    fn add_port() -> Result<(), String> {
        if port_listed(PIPE_NAME) {
            return Ok(());
        }
        // (a Local Port named after the pipe: Windows only adds it while the pipe exists)
        powershell(&format!("Add-PrinterPort -Name '{PIPE_NAME}' -ErrorAction Stop"))
            .map_err(|e| format!("the PlanetPDF port couldn't be added: {e}"))
    }

    fn add_printer() -> Result<bool, String> {
        if printer_exists() {
            return Ok(false);
        }
        powershell(&format!("Add-Printer -Name '{PRINTER_NAME}' -DriverName '{DRIVER_NAME}' -PortName '{PIPE_NAME}' \
                             -Comment 'Prints to a PDF and opens it in PlanetPDF' -ErrorAction Stop"))
            .map_err(|e| format!("the PlanetPDF printer couldn't be added: {e}"))?;
        Ok(true)
    }

    fn remove_port(port: &str) -> Result<(), String> {
        if !port_listed(port) {
            return Ok(());
        }
        // (a printer that was just deleted can hold its port for a moment)
        let mut last = String::new();
        for _ in 0..5 {
            match powershell(&format!("Remove-PrinterPort -Name '{port}' -ErrorAction Stop")) {
                Ok(()) => return Ok(()),
                Err(e) => last = e,
            }
            std::thread::sleep(Duration::from_secs(1));
        }
        Err(last)
    }

    pub fn install() -> Result<String, String> {
        remove_old_monitor();
        let _ = remove_port(TEST_PORT);
        install_service()?;
        ensure_driver()?;
        add_port()?;
        let added = add_printer()?;
        Ok(format!("The PlanetPDF printer is ready (the printer was {}).",
                   if added { "added" } else { "already there" }))
    }

    pub fn uninstall() -> Result<String, String> {
        let mut done: Vec<String> = Vec::new();
        if let Some(handle) = open_printer(ffi::PRINTER_ALL_ACCESS) {
            // SAFETY: a handle opened with full access; its queue is emptied, then it's deleted.
            unsafe {
                ffi::SetPrinterW(handle, 0, std::ptr::null_mut(), ffi::PRINTER_CONTROL_PURGE);
                if ffi::DeletePrinter(handle) != 0 {
                    done.push("the printer was removed".into());
                }
                ffi::ClosePrinter(handle);
            }
        }
        if port_listed(PIPE_NAME) {
            match remove_port(PIPE_NAME) {
                Ok(()) => done.push("its port was removed".into()),
                Err(e) => done.push(format!("its port is left ({e})")),
            }
        }
        let _ = remove_port(TEST_PORT);
        if remove_service()? {
            done.push("the PlanetPDF Print Service was removed".into());
        }
        remove_old_monitor();
        Ok(if done.is_empty() { "The PlanetPDF printer wasn't installed.".to_string() } else { done.join("; ") + "." })
    }

    /// Whether anyone other than an administrator can change anything in `folder` (exit 0: nobody
    /// can; 1: the lines say what). The installer asks it, with its own copy of this program, before
    /// it closes PlanetPDF's folder (security review 2026-11-27, finding 3).
    pub fn check_folder(folder: String) -> Result<String, String> {
        let folder = std::path::PathBuf::from(folder);
        if !folder.is_dir() {
            return Ok(format!("{} isn't there yet", folder.display()));
        }
        let found = acl::open_in_tree(&folder, 20);
        if found.is_empty() {
            Ok(format!("only administrators can change {}", folder.display()))
        } else {
            Err(found.join("\n"))
        }
    }

    pub fn status() -> Result<String, String> {
        let manager = manager(ffi::SC_MANAGER_CONNECT)?;
        let service = open_service(&manager, ffi::SERVICE_QUERY_STATUS);
        let running = service.as_ref().is_some_and(|s| service_state(s) == ffi::SERVICE_RUNNING);
        let ours = service.as_ref().is_some_and(pipe_is_services);
        let held_by_other = !ours && pipe_server_pid(PIPE_NAME).is_some();
        let spool = spool_report();
        let open = std::env::current_exe().map(|e| acl::open_parts(&e.with_file_name(SERVICE_EXE))).unwrap_or_default();
        let checks = [
            ("PlanetPDF Print Service installed", service.is_some()),
            ("PlanetPDF Print Service running", running),
            // (served by the service's own process, as Windows says; security review 2026-11-27)
            ("its pipe is the service's own", ours),
            ("nothing else holds its pipe", !held_by_other),
            // (a folder only SYSTEM and Administrators can use; security review 2026-11-27)
            ("its spool folder is private", spool.is_ok()),
            // (the service runs as SYSTEM: its program must be out of other users' reach)
            ("only administrators can change its program and PlanetPDF's folder", open.is_empty()),
            ("Microsoft Print To PDF driver", driver_installed()),
            ("PlanetPDF port", port_listed(PIPE_NAME)),
            ("PlanetPDF printer", printer_exists()),
        ];
        let mut report: Vec<String> =
            checks.iter().map(|(what, ok)| format!("{}  {what}", if *ok { "OK     " } else { "MISSING" })).collect();
        report.extend(open.iter().map(|line| format!("         {line}")));
        if held_by_other {
            report.push(format!("         {PIPE_NAME} is held by {}", describe_holder(PIPE_NAME)));
        }
        report.push(format!("         spool folder: {}", spool.clone().unwrap_or_else(|e| e)));
        if checks.iter().all(|(_, ok)| *ok) { Ok(report.join("\n")) } else { Err(report.join("\n")) }
    }
}
