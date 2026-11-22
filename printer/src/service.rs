//! The PlanetPDF Print Service: listens on `\\.\pipe\PlanetPDF`, where Windows' Local Port
//! monitor writes what is printed to the "PlanetPDF" printer (a finished PDF from Microsoft Print
//! To PDF). For each connection from the print spooler: the PDF is kept in the private spool
//! folder while it comes, the printer's queue says whose job it is (the one being printed), and
//! deliver.rs hands it to that person. Connections from anything but the spooler are refused, so
//! no program can slip a "printout" to someone. One job at a time (the spooler prints one job at a
//! time per printer); a panic in one job is logged and the next is served.

use std::fs::File;
use std::io::Write;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::atomic::{AtomicBool, Ordering};

use crate::deliver::{self, Job};
use crate::ffi::{self, DWORD, HANDLE};
use crate::log::{log, spool_dir};
use crate::{PIPE_NAME, PRINTER_NAME, SERVICE_NAME};

/// SYSTEM and Administrators full; Everyone may write (the spooler connects with its own,
/// sometimes anonymous, identity): who may *use* it is decided by the client's process
/// (`client_allowed`: the spooler's own process, or a Windows print helper in System32 that the
/// spooler started; both told by Windows, not by the client). Nobody from another computer:
/// network logons are denied here (NU) and the pipe refuses remote clients
/// (`PIPE_REJECT_REMOTE_CLIENTS`; security review 2026-11-21).
const PIPE_SDDL: &str = "D:(D;;GA;;;NU)(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;WD)";
const BUFFER: usize = 64 * 1024;

static STOPPING: AtomicBool = AtomicBool::new(false);
static mut STATUS_HANDLE: HANDLE = std::ptr::null_mut();

struct Owned(HANDLE);

impl Drop for Owned {
    fn drop(&mut self) {
        if !self.0.is_null() && self.0 != ffi::INVALID_HANDLE_VALUE {
            // SAFETY: a handle we own.
            unsafe { ffi::CloseHandle(self.0) };
        }
    }
}

/// Serves the pipe until the service is told to stop. `console` = run by hand for testing.
pub fn serve(console: bool) {
    log(if console { "print service started by hand" } else { "print service started" });
    let sddl = ffi::wide(PIPE_SDDL);
    let mut descriptor: *mut std::ffi::c_void = std::ptr::null_mut();
    // SAFETY: a valid SDDL string; the descriptor lives for the whole service.
    unsafe {
        ffi::ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl.as_ptr(), ffi::SDDL_REVISION_1,
                                                                  &mut descriptor, std::ptr::null_mut());
    }
    let attributes = ffi::SECURITY_ATTRIBUTES {
        nLength: size_of::<ffi::SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: descriptor,
        bInheritHandle: 0,
    };
    let name = ffi::wide(PIPE_NAME);
    while !STOPPING.load(Ordering::Relaxed) {
        // SAFETY: valid name and attributes; the handle is closed by `Owned`.
        let pipe = Owned(unsafe {
            ffi::CreateNamedPipeW(name.as_ptr(), ffi::PIPE_ACCESS_INBOUND,
                                  ffi::PIPE_TYPE_BYTE_WAIT | ffi::PIPE_REJECT_REMOTE_CLIENTS,
                                  ffi::PIPE_UNLIMITED_INSTANCES, 0, BUFFER as u32, 0, &attributes)
        });
        if pipe.0 == ffi::INVALID_HANDLE_VALUE {
            log(&format!("the pipe couldn't be made: {}", std::io::Error::last_os_error()));
            std::thread::sleep(std::time::Duration::from_secs(5));
            continue;
        }
        // SAFETY: waits for a client on our pipe.
        let connected = unsafe { ffi::ConnectNamedPipe(pipe.0, std::ptr::null_mut()) } != 0
            || unsafe { ffi::GetLastError() } == ffi::ERROR_PIPE_CONNECTED;
        if STOPPING.load(Ordering::Relaxed) {
            break;
        }
        if connected {
            if catch_unwind(AssertUnwindSafe(|| take_job(pipe.0))).is_err() {
                log("an internal error (panic) in one job was caught; going on");
            }
        }
        // SAFETY: our connected pipe.
        unsafe { ffi::DisconnectNamedPipe(pipe.0) };
    }
    log("print service stopped");
}

/// Windows' own print helpers the spooler starts, which may write a job to the port themselves:
/// Microsoft Print To PDF is a v4 driver, rendered in printfilterpipelinesvc.exe (user 2026-10-23:
/// printing from Chrome was refused as "not the print spooler", and an earlier job stuck the queue).
const PRINT_HELPERS: [&str; 2] = ["printfilterpipelinesvc.exe", "printisolationhost.exe"];

/// Who connected, for the decision and the log.
struct Client {
    pid: u32,
    parent: Option<u32>,
    image: String,
}

/// Is the pipe's client the print spooler, or one of Windows' print helpers that the spooler
/// started (its program in System32)? Err = why not, for the log.
fn client_allowed(pipe: HANDLE) -> Result<(), String> {
    let mut pid = 0u32;
    // SAFETY: our connected pipe.
    if unsafe { ffi::GetNamedPipeClientProcessId(pipe, &mut pid) } == 0 {
        return Err("the client's process isn't known".into());
    }
    let spooler = spooler_pid().ok_or("the print spooler's process isn't known")?;
    let client = Client { pid, parent: parent_pid(pid), image: image_path(pid).unwrap_or_default() };
    if allowed(&client, spooler, &system_dir()) {
        Ok(())
    } else {
        let parent = client.parent.map_or("unknown".to_string(), |p| p.to_string());
        let image = if client.image.is_empty() { "unknown" } else { &client.image };
        Err(format!("process {} ({image}, started by {parent}; the print spooler is {spooler})", client.pid))
    }
}

/// The rule (`client_allowed`), apart from asking Windows.
fn allowed(client: &Client, spooler: u32, system_dir: &str) -> bool {
    if client.pid == spooler {
        return true;
    }
    let image = client.image.to_lowercase();
    let Some((folder, name)) = image.rsplit_once('\\') else { return false };
    client.parent == Some(spooler)
        && !system_dir.is_empty()
        && folder == system_dir.trim_end_matches('\\').to_lowercase()
        && PRINT_HELPERS.contains(&name)
}

fn image_path(pid: u32) -> Option<String> {
    // SAFETY: the handle is closed by `Owned`; the buffer holds `size` UTF-16 units.
    unsafe {
        let process = Owned(ffi::OpenProcess(ffi::PROCESS_QUERY_LIMITED_INFORMATION, ffi::FALSE, pid));
        if process.0.is_null() {
            return None;
        }
        let mut buffer = [0u16; 1024];
        let mut size = buffer.len() as u32;
        (ffi::QueryFullProcessImageNameW(process.0, 0, buffer.as_mut_ptr(), &mut size) != 0)
            .then(|| String::from_utf16_lossy(&buffer[..size as usize]))
    }
}

fn parent_pid(pid: u32) -> Option<u32> {
    // SAFETY: a process snapshot, closed by `Owned`; entries sized as Windows wants.
    unsafe {
        let snapshot = Owned(ffi::CreateToolhelp32Snapshot(ffi::TH32CS_SNAPPROCESS, 0));
        if snapshot.0 == ffi::INVALID_HANDLE_VALUE {
            return None;
        }
        let mut entry: ffi::PROCESSENTRY32W = std::mem::zeroed();
        entry.dwSize = size_of::<ffi::PROCESSENTRY32W>() as u32;
        let mut more = ffi::Process32FirstW(snapshot.0, &mut entry) != 0;
        while more {
            if entry.th32ProcessID == pid {
                return Some(entry.th32ParentProcessID);
            }
            more = ffi::Process32NextW(snapshot.0, &mut entry) != 0;
        }
        None
    }
}

fn system_dir() -> String {
    let mut buffer = [0u16; ffi::MAX_PATH];
    // SAFETY: a buffer of MAX_PATH UTF-16 units.
    let n = unsafe { ffi::GetSystemDirectoryW(buffer.as_mut_ptr(), buffer.len() as u32) } as usize;
    if n == 0 || n > buffer.len() { String::new() } else { String::from_utf16_lossy(&buffer[..n]) }
}

fn spooler_pid() -> Option<u32> {
    let name = ffi::wide("Spooler");
    // SAFETY: handles closed below; QueryServiceStatusEx fills a SERVICE_STATUS_PROCESS.
    unsafe {
        let manager = ffi::OpenSCManagerW(std::ptr::null(), std::ptr::null(), ffi::SC_MANAGER_CONNECT);
        if manager.is_null() {
            return None;
        }
        let service = ffi::OpenServiceW(manager, name.as_ptr(), ffi::SERVICE_QUERY_STATUS);
        let mut status = ffi::SERVICE_STATUS_PROCESS::default();
        let mut needed = 0;
        let ok = !service.is_null()
            && ffi::QueryServiceStatusEx(service, ffi::SC_STATUS_PROCESS_INFO, &mut status as *mut _ as *mut u8,
                                         size_of::<ffi::SERVICE_STATUS_PROCESS>() as u32, &mut needed) != 0;
        if !service.is_null() {
            ffi::CloseServiceHandle(service);
        }
        ffi::CloseServiceHandle(manager);
        (ok && status.dwProcessId != 0).then_some(status.dwProcessId)
    }
}

/// One connection: read the PDF into the spool folder, find the job, deliver it.
fn take_job(pipe: HANDLE) {
    if let Err(who) = client_allowed(pipe) {
        log(&format!("refused a connection from {who}: not the print spooler or one of its print helpers"));
        return;
    }
    let path = spool_dir().join(format!("print-{}-{}.pdf", std::process::id(), stamp()));
    let mut file = match File::create(&path) {
        Ok(file) => file,
        Err(e) => {
            log(&format!("can't keep a printout in {}: {e}", path.display()));
            return;
        }
    };
    let mut buffer = vec![0u8; BUFFER];
    let mut total = 0u64;
    let mut job: Option<Job> = None;
    loop {
        let mut read = 0;
        // SAFETY: a buffer of BUFFER bytes; our connected pipe.
        let ok = unsafe { ffi::ReadFile(pipe, buffer.as_mut_ptr(), BUFFER as u32, &mut read, std::ptr::null_mut()) };
        if ok == 0 || read == 0 {
            break;  // (the spooler closed its end: the job is all here)
        }
        if job.is_none() {
            job = printing_job();  // (while it's being written, the job shows as printing)
        }
        if file.write_all(&buffer[..read as usize]).is_err() {
            log("a printout couldn't be written to the spool folder");
            break;
        }
        total += read as u64;
    }
    drop(file);
    if total == 0 {
        let _ = std::fs::remove_file(&path);  // (Windows checking the port, e.g. when the printer is added)
        return;
    }
    match job {
        Some(job) if !job.user.is_empty() => match deliver::deliver(&job, &path) {
            Ok(target) => log(&format!("job {} ({}, {total} bytes): {}", job.id, job.user, target.display())),
            Err(e) => log(&format!("job {} ({}): not delivered: {e}", job.id, job.user)),
        },
        _ => log(&format!("a printout of {total} bytes: whose it is isn't known (no job printing on {PRINTER_NAME})")),
    }
    let _ = std::fs::remove_file(&path);
}

fn stamp() -> u128 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0)
}

/// The job the PlanetPDF printer is printing now (else its first job).
fn printing_job() -> Option<Job> {
    let name = ffi::wide(PRINTER_NAME);
    let mut printer: HANDLE = std::ptr::null_mut();
    // SAFETY: a NUL-terminated name; the handle is closed below.
    if unsafe { ffi::OpenPrinterW(name.as_ptr(), &mut printer, std::ptr::null()) } == 0 {
        return None;
    }
    let (mut needed, mut count) = (0u32, 0u32);
    // SAFETY: first the size, then a buffer that big (8-byte aligned) of JOB_INFO_1W entries.
    let found = unsafe {
        ffi::EnumJobsW(printer, 0, 64, 1, std::ptr::null_mut(), 0, &mut needed, &mut count);
        let mut buffer = vec![0u64; (needed as usize).div_ceil(8).max(1)];
        let ok = needed > 0
            && ffi::EnumJobsW(printer, 0, 64, 1, buffer.as_mut_ptr() as *mut u8, needed, &mut needed, &mut count) != 0;
        let jobs = if ok {
            std::slice::from_raw_parts(buffer.as_ptr() as *const ffi::JOB_INFO_1W, count as usize)
        } else {
            &[]
        };
        jobs.iter()
            .find(|j| j.Status & ffi::JOB_STATUS_PRINTING != 0)
            .or_else(|| jobs.first())
            .map(|j| Job { id: j.JobId, user: ffi::from_wide(j.pUserName), document: ffi::from_wide(j.pDocument) })
    };
    // SAFETY: the handle opened above.
    unsafe { ffi::ClosePrinter(printer) };
    found
}

// --- as a Windows service ------------------------------------------------------------------------

fn report(state: DWORD, exit: DWORD) {
    let status = ffi::SERVICE_STATUS {
        dwServiceType: ffi::SERVICE_WIN32_OWN_PROCESS,
        dwCurrentState: state,
        dwControlsAccepted: if state == ffi::SERVICE_RUNNING { ffi::SERVICE_ACCEPT_STOP | ffi::SERVICE_ACCEPT_SHUTDOWN } else { 0 },
        dwWin32ExitCode: exit,
        dwServiceSpecificExitCode: 0,
        dwCheckPoint: 0,
        dwWaitHint: if state == ffi::SERVICE_RUNNING || state == ffi::SERVICE_STOPPED { 0 } else { 5000 },
    };
    // SAFETY: the handle from RegisterServiceCtrlHandlerExW (set before any report).
    unsafe { ffi::SetServiceStatus(STATUS_HANDLE, &status) };
}

unsafe extern "system" fn control(code: DWORD, _type: DWORD, _data: *mut std::ffi::c_void,
                                  _context: *mut std::ffi::c_void) -> DWORD {
    match code {
        ffi::SERVICE_CONTROL_STOP | ffi::SERVICE_CONTROL_SHUTDOWN => {
            report(ffi::SERVICE_STOP_PENDING, 0);
            STOPPING.store(true, Ordering::Relaxed);
            // (wake the listener: it's waiting for a connection)
            let name = ffi::wide(PIPE_NAME);
            let _ = std::fs::OpenOptions::new().write(true).open(String::from_utf16_lossy(&name[..name.len() - 1]));
            0
        }
        ffi::SERVICE_CONTROL_INTERROGATE => 0,
        _ => 120,  // ERROR_CALL_NOT_IMPLEMENTED
    }
}

unsafe extern "system" fn service_main(_argc: DWORD, _argv: *mut ffi::LPWSTR) {
    let name = ffi::wide(SERVICE_NAME);
    // SAFETY: registers our control handler for this service.
    unsafe {
        STATUS_HANDLE = ffi::RegisterServiceCtrlHandlerExW(name.as_ptr(), Some(control), std::ptr::null_mut());
        if STATUS_HANDLE.is_null() {
            return;
        }
    }
    report(ffi::SERVICE_RUNNING, 0);
    serve(false);
    report(ffi::SERVICE_STOPPED, 0);
}

/// Runs as the Windows service (when the Service Control Manager starts the program).
pub fn run_as_service() -> bool {
    let mut name = ffi::wide(SERVICE_NAME);
    let table = [
        ffi::SERVICE_TABLE_ENTRYW { lpServiceName: name.as_mut_ptr(), lpServiceProc: Some(service_main) },
        ffi::SERVICE_TABLE_ENTRYW { lpServiceName: std::ptr::null_mut(), lpServiceProc: None },
    ];
    // SAFETY: a NULL-terminated table whose strings outlive the call (it returns when the service stops).
    unsafe { ffi::StartServiceCtrlDispatcherW(table.as_ptr()) != 0 }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SYSTEM: &str = r"C:\WINDOWS\system32";
    const SPOOLER: u32 = 4508;

    fn client(pid: u32, parent: Option<u32>, image: &str) -> Client {
        Client { pid, parent, image: image.into() }
    }

    #[test]
    fn the_spooler_and_its_print_helpers_may_send_a_job() {
        assert!(allowed(&client(SPOOLER, Some(700), r"C:\WINDOWS\System32\spoolsv.exe"), SPOOLER, SYSTEM));
        assert!(allowed(&client(46972, Some(SPOOLER), r"C:\Windows\System32\PrintFilterPipelineSvc.exe"), SPOOLER, SYSTEM));
        assert!(allowed(&client(5000, Some(SPOOLER), r"C:\Windows\System32\PrintIsolationHost.exe"), SPOOLER, SYSTEM));
    }

    #[test]
    fn anything_else_is_refused() {
        // a helper's name, but not started by the spooler (a user ran it)
        assert!(!allowed(&client(46972, Some(1234), r"C:\Windows\System32\printfilterpipelinesvc.exe"), SPOOLER, SYSTEM));
        // started by the spooler, but not a print helper, or not from System32
        assert!(!allowed(&client(46972, Some(SPOOLER), r"C:\Windows\System32\cmd.exe"), SPOOLER, SYSTEM));
        assert!(!allowed(&client(46972, Some(SPOOLER), r"C:\Users\x\printfilterpipelinesvc.exe"), SPOOLER, SYSTEM));
        assert!(!allowed(&client(46972, Some(SPOOLER), r"C:\Windows\System32\sub\printfilterpipelinesvc.exe"), SPOOLER, SYSTEM));
        // nothing known
        assert!(!allowed(&client(46972, None, ""), SPOOLER, SYSTEM));
        assert!(!allowed(&client(46972, Some(SPOOLER), r"C:\Windows\System32\printfilterpipelinesvc.exe"), SPOOLER, ""));
    }

    #[test]
    fn this_process_is_described_from_windows() {
        let me = std::process::id();
        assert!(image_path(me).is_some_and(|p| p.to_lowercase().ends_with(".exe")));
        assert!(parent_pid(me).is_some());
        assert!(system_dir().to_lowercase().ends_with("system32"));
    }
}
