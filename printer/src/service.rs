//! The PlanetPDF Print Service: listens on `\\.\pipe\PlanetPDF`, where Windows' Local Port
//! monitor writes what is printed to the "PlanetPDF" printer (a finished PDF from Microsoft Print
//! To PDF). For each connection from the print spooler: the PDF is kept in the private spool
//! folder while it comes, the printer's queue says whose job it is (the one being printed), and
//! deliver.rs hands it to that person. Connections from anything but the spooler are refused, so
//! no program can slip a "printout" to someone. One job at a time (the spooler prints one job at a
//! time per printer); a panic in one job is logged and the next is served.

use std::io::Write;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::atomic::{AtomicBool, Ordering};

use crate::deliver::{self, Job};
use crate::ffi::{self, DWORD, HANDLE};
use crate::log::{event_log, log, spool_dir};
use crate::{PIPE_NAME, PRINTER_NAME, SERVICE_NAME};

/// SYSTEM and Administrators full; Everyone may write (the spooler connects with its own,
/// sometimes anonymous, identity): who may *use* it is decided by the client's process
/// (`client_allowed`: the spooler's own process, or a Windows print helper in System32 that the
/// spooler started; both told by Windows, not by the client). Nobody from another computer: the
/// pipe refuses remote clients (`PIPE_REJECT_REMOTE_CLIENTS`; security review 2026-11-21).
///
/// **Everyone also needs DELETE (`SD`)** (user 2026-11-22: every job from Visio and Notepad stuck
/// as "Error, Printing, Retained", printer.log with no connection at all). Process Monitor showed
/// the spooler, impersonating the printing user, opening the port with
/// `Generic Write | Read Attributes | Delete`, OpenIf, share Read: ACCESS DENIED, twice, 10 s
/// apart, then the job failed. The same request on a test pipe was refused without `SD` and
/// allowed with it. Administrators always had it (GA), so a print from an administrator
/// PowerShell worked and hid this. (0.1.17's "deny network logons" entry, dropped in 0.1.19 while
/// looking for this, isn't put back: the flag above already keeps other computers out.)
const PIPE_SDDL: &str = "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGWSD;;;WD)";
const BUFFER: usize = 64 * 1024;
// Limits on what the service keeps (security list 2026-11-26, item 4: nothing bounded it, so
// printouts nobody could be given, or one endless job, could fill the system drive).
/// A single printout larger than this is thrown away (the rest of it is read and dropped, so the
/// queue isn't left with a failed job).
const MAX_JOB_BYTES: u64 = 4 * 1024 * 1024 * 1024;
/// Held printouts (whose owner couldn't be told) are kept this long for an administrator…
const HELD_SECONDS: u64 = 30 * 24 * 60 * 60;
/// …and at most this much of them, the oldest going first.
const HELD_MAX_BYTES: u64 = 1024 * 1024 * 1024;
/// A job file left in the spool folder by a run that ended mid-job is removed after this long.
const LEFTOVER_SECONDS: u64 = 24 * 60 * 60;

static STOPPING: AtomicBool = AtomicBool::new(false);
/// The service holds the pipe's name (so stopping may wake its listener by connecting to it).
static HOLDING: AtomicBool = AtomicBool::new(false);
/// What the printer is set to send to while the service doesn't hold the pipe: Windows' own
/// "print to file" port, which asks the person printing for a file name, so nothing a job holds
/// can reach a program that took the pipe's name.
const PORT_WHILE_AWAY: &str = "FILE:";
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
    warn_if_open_to_others();
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
    // The only instance there can be, made once and kept until the service stops (security review
    // 2026-11-25): Everyone's write access includes the right to make another instance of a pipe,
    // and the spooler needs that access, so a program could otherwise open a second instance and
    // receive other people's printouts. FIRST_PIPE_INSTANCE: if the name is taken already,
    // something else got there first.
    //
    // Then the printer must never send there (security review 2026-11-27, findings 4 and 10: a
    // user's program that held the name before the first install, or while the service was
    // stopped, received other people's printouts; pausing the printer wasn't enough, since
    // resuming it sent them on). So the printer is paused *and* pointed at Windows' own file port,
    // and the service stays running, taking the name as soon as it is free, and naming in the log
    // the program that holds it.
    let mut said = false;
    let pipe = loop {
        // SAFETY: valid name and attributes; the handle is closed by `Owned`.
        let handle = unsafe {
            ffi::CreateNamedPipeW(name.as_ptr(), ffi::PIPE_ACCESS_INBOUND | ffi::FILE_FLAG_FIRST_PIPE_INSTANCE,
                                  ffi::PIPE_TYPE_BYTE_WAIT | ffi::PIPE_REJECT_REMOTE_CLIENTS, 1, 0, BUFFER as u32,
                                  0, &attributes)
        };
        if handle != ffi::INVALID_HANDLE_VALUE {
            break Owned(handle);
        }
        // SAFETY: reads this thread's last-error value.
        let error = unsafe { ffi::GetLastError() };
        printer_away();
        if !said {
            said = true;
            let message = if matches!(error, ffi::ERROR_ACCESS_DENIED | ffi::ERROR_PIPE_BUSY) {
                format!("another program already holds {PIPE_NAME} ({}), so the {PRINTER_NAME} printer is paused \
                         and set to print to a file until the print service can take the pipe back: find and end \
                         that program, or restart the computer", describe_holder(PIPE_NAME))
            } else {
                format!("the pipe couldn't be made ({}), so the {PRINTER_NAME} printer is paused and set to print \
                         to a file until it can", std::io::Error::from_raw_os_error(error as i32))
            };
            log(&message);
            event_log(&message);
        }
        for _ in 0..10 {
            if STOPPING.load(Ordering::Relaxed) {
                log("print service stopped");
                return;
            }
            std::thread::sleep(std::time::Duration::from_millis(200));
        }
    };
    HOLDING.store(true, Ordering::Relaxed);
    // Held printouts are kept 30 days: looked at every day, not only when the service starts
    // (security review 2026-11-27: one that ran for months kept them all).
    std::thread::spawn(|| {
        let mut waited = 0u64;
        while !STOPPING.load(Ordering::Relaxed) {
            std::thread::sleep(std::time::Duration::from_secs(5));
            waited += 5;
            if waited >= 24 * 60 * 60 {
                waited = 0;
                tidy_spool();
            }
        }
    });
    if said {
        log(&format!("the print service now holds {PIPE_NAME}"));
    }
    printer_back();  // (paused and sent to a file while the service wasn't there to receive the jobs)
    tidy_spool();
    while !STOPPING.load(Ordering::Relaxed) {
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
    printer_away();  // (while nothing receives them, jobs wait in the queue, and can't go to the pipe)
    HOLDING.store(false, Ordering::Relaxed);
    drop(pipe);
    log("print service stopped");
}

/// Says in the log when someone other than an administrator can change this program or the
/// folders it is in (security list 2026-11-26, item 3): whoever can, can have a program of their
/// own run as SYSTEM in its place. The service can't stop that from the inside (the program
/// replaced would be this one), so it says what to do; the installer closes the folder, and
/// setup refuses to add the service where it is open.
fn warn_if_open_to_others() {
    let Ok(exe) = std::env::current_exe() else { return };
    for line in crate::acl::open_parts(&exe) {
        log(&format!("WARNING: {line}. Whoever can change it can run a program with system rights. Run \
                      PlanetPDF's installer again (it closes the folder to everyone but administrators), or \
                      take away their right to write there."));
    }
}

/// A printer handle, closed with ClosePrinter.
struct Printer(HANDLE);

impl Drop for Printer {
    fn drop(&mut self) {
        // SAFETY: a handle OpenPrinterW gave us.
        unsafe { ffi::ClosePrinter(self.0) };
    }
}

/// The PlanetPDF printer, opened to be administered (None: it isn't there yet).
fn open_printer_admin() -> Option<Printer> {
    let name = ffi::wide(PRINTER_NAME);
    let defaults = ffi::PRINTER_DEFAULTSW {
        pDatatype: std::ptr::null_mut(),
        pDevMode: std::ptr::null_mut(),
        DesiredAccess: ffi::PRINTER_ACCESS_ADMINISTER,
    };
    let mut printer: HANDLE = std::ptr::null_mut();
    // SAFETY: a NUL-terminated name and valid defaults; the handle is closed by `Printer`.
    (unsafe { ffi::OpenPrinterW(name.as_ptr(), &mut printer, &defaults) } != 0).then_some(Printer(printer))
}

fn set_paused(printer: &Printer, paused: bool) {
    let command = if paused { ffi::PRINTER_CONTROL_PAUSE } else { ffi::PRINTER_CONTROL_RESUME };
    // SAFETY: an administer handle; level 0 with a command.
    unsafe { ffi::SetPrinterW(printer.0, 0, std::ptr::null_mut(), command) };
}

/// The printer's PRINTER_INFO_5W, in an 8-byte aligned buffer.
fn printer_info5(printer: &Printer) -> Option<Vec<u64>> {
    let mut needed = 0u32;
    // SAFETY: first the size, then a buffer that big.
    unsafe {
        ffi::GetPrinterW(printer.0, 5, std::ptr::null_mut(), 0, &mut needed);
        let mut buffer = vec![0u64; (needed as usize).div_ceil(8).max(1)];
        (needed != 0 && ffi::GetPrinterW(printer.0, 5, buffer.as_mut_ptr() as *mut u8, needed, &mut needed) != 0)
            .then_some(buffer)
    }
}

/// The printer's port.
fn printer_port(printer: &Printer) -> Option<String> {
    let info = printer_info5(printer)?;
    // SAFETY: a PRINTER_INFO_5W filled in by Windows, its strings inside the same buffer.
    Some(unsafe { ffi::from_wide((*(info.as_ptr() as *const ffi::PRINTER_INFO_5W)).pPortName) })
}

/// Points the printer at another port (its other settings as they are).
fn set_printer_port(printer: &Printer, port: &str) -> bool {
    let Some(mut info) = printer_info5(printer) else { return false };
    let mut port = ffi::wide(port);
    let entry = info.as_mut_ptr() as *mut ffi::PRINTER_INFO_5W;
    // SAFETY: the printer's own PRINTER_INFO_5W with its port changed; `port` outlives the call.
    unsafe {
        (*entry).pPortName = port.as_mut_ptr();
        ffi::SetPrinterW(printer.0, 5, entry as *mut u8, 0) != 0
    }
}

/// While the service doesn't hold the pipe: the printer paused and sent to Windows' file port, so
/// that even resumed, it sends nothing to whoever holds the pipe's name.
fn printer_away() {
    let Some(printer) = open_printer_admin() else { return };
    set_paused(&printer, true);
    if printer_port(&printer).is_some_and(|p| p.eq_ignore_ascii_case(PIPE_NAME))
        && !set_printer_port(&printer, PORT_WHILE_AWAY)
    {
        log(&format!("the {PRINTER_NAME} printer couldn't be set to print to a file: {}",
                     std::io::Error::last_os_error()));
    }
}

/// Once the service holds the pipe: the printer sent there again (if `printer_away` had moved it)
/// and resumed.
fn printer_back() {
    let Some(printer) = open_printer_admin() else { return };
    if printer_port(&printer).is_some_and(|p| p.eq_ignore_ascii_case(PORT_WHILE_AWAY))
        && !set_printer_port(&printer, PIPE_NAME)
    {
        log(&format!("the {PRINTER_NAME} printer couldn't be set back to {PIPE_NAME}: {}",
                     std::io::Error::last_os_error()));
        return;  // (left paused: resumed, it would print to a file)
    }
    set_paused(&printer, false);
}

/// The process that serves a pipe (asked of Windows by connecting to it). None: there is none,
/// or it couldn't be told.
pub fn pipe_server_pid(pipe: &str) -> Option<u32> {
    let name = ffi::wide(pipe);
    // SAFETY: a NUL-terminated name; the handle is closed by `Owned`.
    let handle = unsafe {
        ffi::CreateFileW(name.as_ptr(), ffi::FILE_READ_ATTRIBUTES, 0, std::ptr::null(), ffi::OPEN_EXISTING, 0,
                         std::ptr::null_mut())
    };
    if handle == ffi::INVALID_HANDLE_VALUE {
        return None;
    }
    let handle = Owned(handle);
    let mut pid = 0u32;
    // SAFETY: a handle to the pipe's client end.
    (unsafe { ffi::GetNamedPipeServerProcessId(handle.0, &mut pid) } != 0 && pid != 0).then_some(pid)
}

/// Which program holds a pipe, in words for the log.
pub fn describe_holder(pipe: &str) -> String {
    match pipe_server_pid(pipe) {
        Some(pid) => format!("process {pid}, {}", image_path(pid).unwrap_or_else(|| "its program unknown".into())),
        None => "which program couldn't be told".to_string(),
    }
}

/// Who is writing to the pipe, as Windows knows them (the spooler writes as the printing user:
/// Process Monitor, 2026-11-22). None when it can't be told.
struct Writer {
    name: String,
    sid: String,
}

fn pipe_writer(pipe: HANDLE) -> Option<Writer> {
    // SAFETY: impersonates the pipe's client only to open its token, then reverts at once; the
    // token is closed by `Owned`; buffers are sized as Windows asks.
    unsafe {
        if ffi::ImpersonateNamedPipeClient(pipe) == 0 {
            return None;
        }
        let mut token: HANDLE = std::ptr::null_mut();
        let opened = ffi::OpenThreadToken(ffi::GetCurrentThread(), ffi::TOKEN_QUERY, ffi::TRUE, &mut token);
        ffi::RevertToSelf();
        if opened == 0 {
            return None;
        }
        let token = Owned(token);
        let mut needed = 0u32;
        ffi::GetTokenInformation(token.0, ffi::TOKEN_USER, std::ptr::null_mut(), 0, &mut needed);
        let mut buffer = vec![0u64; (needed as usize).div_ceil(8).max(1)];
        if ffi::GetTokenInformation(token.0, ffi::TOKEN_USER, buffer.as_mut_ptr() as *mut _, needed, &mut needed) == 0 {
            return None;
        }
        let sid = *(buffer.as_ptr() as *const *mut std::ffi::c_void);  // (TOKEN_USER.User.Sid)
        let mut text: ffi::LPWSTR = std::ptr::null_mut();
        let sid_text = if ffi::ConvertSidToStringSidW(sid, &mut text) != 0 {
            let value = ffi::from_wide(text);
            ffi::LocalFree(text as *mut _);
            value
        } else {
            String::new()
        };
        let mut name = [0u16; 256];
        let mut domain = [0u16; 256];
        let (mut name_len, mut domain_len, mut kind) = (name.len() as u32, domain.len() as u32, 0i32);
        if ffi::LookupAccountSidW(std::ptr::null(), sid, name.as_mut_ptr(), &mut name_len, domain.as_mut_ptr(),
                                  &mut domain_len, &mut kind) == 0 {
            return None;
        }
        Some(Writer { name: String::from_utf16_lossy(&name[..name_len as usize]), sid: sid_text })
    }
}

/// A job in the PlanetPDF printer's queue, and whether it's the one printing.
struct QueueJob {
    job: Job,
    printing: bool,
}

/// Whose printout it is (security review 2026-11-25: it was guessed from the queue): the person
/// Windows says is writing it; only when that's a service account (the spooler's own) the one job
/// printing, if there's exactly one. Otherwise Err: it's held, never handed to a guess.
fn choose_job(writer: Option<&Writer>, queue: &[QueueJob]) -> Result<Job, String> {
    const SERVICE_ACCOUNTS: [&str; 3] = ["S-1-5-18", "S-1-5-19", "S-1-5-20"];
    if let Some(writer) = writer.filter(|w| !w.name.is_empty() && !SERVICE_ACCOUNTS.contains(&w.sid.as_str())) {
        let theirs = |q: &&QueueJob| q.job.user.eq_ignore_ascii_case(&writer.name);
        let found = queue.iter().filter(|q| q.printing).find(theirs).or_else(|| queue.iter().find(theirs));
        // (the account itself goes along, so the printout reaches that account's session and not
        // another account's of the same name)
        return Ok(match found {
            Some(q) => Job { user: writer.name.clone(), sid: writer.sid.clone(), ..q.job.clone() },
            None => Job { id: 0, user: writer.name.clone(), sid: writer.sid.clone(), document: "Printout".into() },
        });
    }
    let printing: Vec<&QueueJob> = queue.iter().filter(|q| q.printing).collect();
    match printing.as_slice() {
        [one] if !one.job.user.is_empty() => Ok(one.job.clone()),
        _ => Err(format!("whose printout it is isn't known for sure ({} jobs printing on {PRINTER_NAME}, and the \
                          writer is {})", printing.len(), writer.map_or("unknown", |w| w.name.as_str()))),
    }
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

/// Windows' System32 folder, asked of Windows (never read from an environment variable).
pub fn system_dir() -> String {
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
    let mut buffer = vec![0u8; BUFFER];
    let Some(spool) = spool_dir() else {
        // (no folder only SYSTEM and Administrators can use: the job is read and dropped, never kept
        // where others could read it; log() says so in Windows' event log)
        while read_some(pipe, &mut buffer) > 0 {}
        log("a printout was refused: there is no folder only SYSTEM and Administrators can use to keep it in");
        return;
    };
    let path = spool.join(format!("print-{}-{}.pdf", std::process::id(), stamp()));
    // (a new file, never one already there)
    let mut file = match std::fs::OpenOptions::new().write(true).create_new(true).open(&path) {
        Ok(file) => file,
        Err(e) => {
            log(&format!("can't keep a printout in {}: {e}", path.display()));
            while read_some(pipe, &mut buffer) > 0 {}
            return;
        }
    };
    let mut total = 0u64;
    let mut found: Option<(Option<Writer>, Vec<QueueJob>)> = None;
    loop {
        let read = read_some(pipe, &mut buffer);
        if read == 0 {
            break;  // (the spooler closed its end: the job is all here)
        }
        if found.is_none() {
            // (who writes it, once data has come; and the queue while the job shows as printing)
            found = Some((pipe_writer(pipe), queue_jobs()));
        }
        total += read as u64;
        if total > MAX_JOB_BYTES {
            continue;  // (too large to keep: the rest is read and dropped, so the spooler's job ends normally)
        }
        if file.write_all(&buffer[..read as usize]).is_err() {
            log("a printout couldn't be written to the spool folder");
            break;
        }
    }
    drop(file);
    if total == 0 {
        let _ = std::fs::remove_file(&path);  // (Windows checking the port, e.g. when the printer is added)
        return;
    }
    if total > MAX_JOB_BYTES {
        let _ = std::fs::remove_file(&path);
        log(&format!("a printout of {total} bytes was thrown away: larger than the {} MB the service keeps",
                     MAX_JOB_BYTES / (1024 * 1024)));
        return;
    }
    let (writer, queue) = found.unwrap_or((None, Vec::new()));
    match choose_job(writer.as_ref(), &queue) {
        Ok(job) => match deliver::deliver(&job, &path) {
            Ok(target) => log(&format!("job {} ({}, {total} bytes): {}", job.id, job.user, target.display())),
            Err(deliver::Failure::Unsure(why)) => return hold(&path, total, &why),
            // (kept for an administrator, never thrown away: security review 2026-11-27, a failed
            // delivery deleted the printout)
            Err(deliver::Failure::Failed(e)) => {
                return hold(&path, total, &format!("job {} ({}): not delivered: {e}", job.id, job.user));
            }
        },
        Err(why) => return hold(&path, total, &why),
    }
    let _ = std::fs::remove_file(&path);
}

/// Reads what has come on the pipe into `buffer` (0: the other end closed it).
fn read_some(pipe: HANDLE, buffer: &mut [u8]) -> u32 {
    let mut read = 0;
    // SAFETY: a buffer of buffer.len() bytes; our connected pipe.
    let ok = unsafe { ffi::ReadFile(pipe, buffer.as_mut_ptr(), buffer.len() as u32, &mut read, std::ptr::null_mut()) };
    if ok == 0 { 0 } else { read }
}

/// Keeps a printout nobody can be given for sure in the private spool folder, for an
/// administrator: never delivered to a guess. Older held printouts make room (`tidy_spool`).
fn hold(path: &std::path::Path, total: u64, why: &str) {
    let held = path.with_file_name(format!("held-{}.pdf", stamp()));
    let kept = std::fs::rename(path, &held).is_ok();
    log(&format!("a printout of {total} bytes was held: {why}{}",
                 if kept { format!(" ({})", held.display()) } else { String::new() }));
    tidy_spool();
}

/// A file in the spool folder: its name, its age in seconds, its size.
#[derive(Clone, Debug, PartialEq)]
struct Kept {
    name: String,
    age: u64,
    bytes: u64,
}

/// Which of the spool folder's files go (the rule, apart from the folder): held printouts older
/// than `HELD_SECONDS`; then the oldest held ones until what's left is within `HELD_MAX_BYTES`;
/// and job files a run that ended mid-job left behind (`print-….pdf` older than
/// `LEFTOVER_SECONDS`). Anything else in the folder (the log) stays.
fn to_remove(files: &[Kept]) -> Vec<String> {
    let mut gone: Vec<String> = Vec::new();
    let mut held: Vec<&Kept> = Vec::new();
    for file in files {
        let pdf = file.name.ends_with(".pdf");
        if pdf && file.name.starts_with("print-") && file.age > LEFTOVER_SECONDS {
            gone.push(file.name.clone());
        } else if pdf && file.name.starts_with("held-") {
            if file.age > HELD_SECONDS { gone.push(file.name.clone()) } else { held.push(file) }
        }
    }
    held.sort_by_key(|f| std::cmp::Reverse(f.age));  // (oldest first)
    let mut total: u64 = held.iter().map(|f| f.bytes).sum();
    for file in held {
        if total <= HELD_MAX_BYTES {
            break;
        }
        total -= file.bytes;
        gone.push(file.name.clone());
    }
    gone
}

/// Applies `to_remove` to the spool folder, saying in the log what went.
fn tidy_spool() {
    let Some(folder) = spool_dir() else { return };
    let now = std::time::SystemTime::now();
    let Ok(entries) = std::fs::read_dir(&folder) else { return };
    let files: Vec<Kept> = entries
        .flatten()
        .filter_map(|entry| {
            let info = entry.metadata().ok().filter(|m| m.is_file())?;
            let age = info.modified().ok().and_then(|m| now.duration_since(m).ok()).map_or(0, |d| d.as_secs());
            Some(Kept { name: entry.file_name().to_string_lossy().into_owned(), age, bytes: info.len() })
        })
        .collect();
    for name in to_remove(&files) {
        if std::fs::remove_file(folder.join(&name)).is_ok() {
            log(&format!("{name} was removed from the spool folder (kept {} days, or {} MB of held printouts, \
                          at most)", HELD_SECONDS / 86_400, HELD_MAX_BYTES / (1024 * 1024)));
        }
    }
}

fn stamp() -> u128 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0)
}

/// The PlanetPDF printer's queue.
fn queue_jobs() -> Vec<QueueJob> {
    let name = ffi::wide(PRINTER_NAME);
    let mut printer: HANDLE = std::ptr::null_mut();
    // SAFETY: a NUL-terminated name; the handle is closed below.
    if unsafe { ffi::OpenPrinterW(name.as_ptr(), &mut printer, std::ptr::null()) } == 0 {
        return Vec::new();
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
            .map(|j| QueueJob {
                job: Job { id: j.JobId, user: ffi::from_wide(j.pUserName), sid: String::new(),
                           document: ffi::from_wide(j.pDocument) },
                printing: j.Status & ffi::JOB_STATUS_PRINTING != 0,
            })
            .collect()
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
            // (wake the listener, waiting for a connection: only when the pipe is ours, never another
            // program's that holds the name)
            if HOLDING.load(Ordering::Relaxed) {
                let _ = std::fs::OpenOptions::new().write(true).open(PIPE_NAME);
            }
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

    fn queued(id: u32, user: &str, printing: bool) -> QueueJob {
        QueueJob { job: Job { id, user: user.into(), sid: String::new(), document: format!("doc{id}") }, printing }
    }

    fn writer(name: &str, sid: &str) -> Writer {
        Writer { name: name.into(), sid: sid.into() }
    }

    #[test]
    fn the_printout_goes_to_who_windows_says_wrote_it() {
        let queue = [queued(1, "alice", true), queued(2, "bob", false)];
        let bob = writer("bob", "S-1-5-21-1-2-3-1001");
        // (bob is writing: his, even though alice's job shows as printing)
        assert_eq!(choose_job(Some(&bob), &queue).unwrap(),
                   Job { id: 2, user: "bob".into(), sid: "S-1-5-21-1-2-3-1001".into(), document: "doc2".into() });
        let carol = writer("carol", "S-1-5-21-1-2-3-1002");
        assert_eq!(choose_job(Some(&carol), &queue).unwrap().user, "carol");  // (not in the queue: still hers)
        assert_eq!(choose_job(Some(&carol), &queue).unwrap().sid, "S-1-5-21-1-2-3-1002");  // (her account goes along)
    }

    fn kept(name: &str, days: u64, megabytes: u64) -> Kept {
        Kept { name: name.into(), age: days * 86_400 + 60, bytes: megabytes * 1024 * 1024 }
    }

    #[test]
    fn held_printouts_are_kept_a_month_and_a_gigabyte_at_most() {
        let files = [kept("held-1.pdf", 40, 1), kept("held-2.pdf", 3, 1), kept("printer.log", 400, 1),
                     kept("print-99-1.pdf", 2, 5), kept("print-99-2.pdf", 0, 5), kept("notes.txt", 90, 1)];
        // the old held one, and the job file a run left behind two days ago; nothing else
        assert_eq!(to_remove(&files), vec!["held-1.pdf".to_string(), "print-99-1.pdf".to_string()]);
        // more than a gigabyte held: the oldest go until the rest fits
        let many = [kept("held-a.pdf", 1, 500), kept("held-b.pdf", 9, 500), kept("held-c.pdf", 5, 500)];
        assert_eq!(to_remove(&many), vec!["held-b.pdf".to_string()]);
        let huge = [kept("held-a.pdf", 1, 2000)];
        assert_eq!(to_remove(&huge), vec!["held-a.pdf".to_string()]);  // (even alone it is over)
        assert!(to_remove(&[kept("held-a.pdf", 1, 900)]).is_empty());
    }

    #[test]
    fn a_guess_is_held_not_delivered() {
        let system = writer("SYSTEM", "S-1-5-18");
        assert_eq!(choose_job(Some(&system), &[queued(1, "alice", true)]).unwrap().user, "alice");  // (only one)
        assert!(choose_job(Some(&system), &[queued(1, "alice", true), queued(2, "bob", true)]).is_err());
        assert!(choose_job(None, &[queued(1, "alice", false)]).is_err());  // (none printing: was "the first")
        assert!(choose_job(None, &[]).is_err());
    }

    #[test]
    fn a_pipe_s_server_is_told_by_windows() {
        let pipe = format!(r"\\.\pipe\PlanetPDF-test-{}", std::process::id());
        assert_eq!(pipe_server_pid(&pipe), None);  // (nobody serves it yet)
        let name = ffi::wide(&pipe);
        // SAFETY: a valid name; default security; closed by `Owned`.
        let served = Owned(unsafe {
            ffi::CreateNamedPipeW(name.as_ptr(), ffi::PIPE_ACCESS_INBOUND | ffi::FILE_FLAG_FIRST_PIPE_INSTANCE,
                                  ffi::PIPE_TYPE_BYTE_WAIT | ffi::PIPE_REJECT_REMOTE_CLIENTS, 1, 0, 4096, 0,
                                  std::ptr::null())
        });
        assert_ne!(served.0, ffi::INVALID_HANDLE_VALUE);
        // (one look takes the pipe's one instance until its server lets go and listens again, as
        // the service does after each client: so the first look says who, in words)
        assert!(describe_holder(&pipe).contains(&format!("process {}", std::process::id())));
        assert_eq!(pipe_server_pid(&pipe), None);  // (busy now: told apart from "ours" by the caller)
    }

    #[test]
    fn this_process_is_described_from_windows() {
        let me = std::process::id();
        assert!(image_path(me).is_some_and(|p| p.to_lowercase().ends_with(".exe")));
        assert!(parent_pid(me).is_some());
        assert!(system_dir().to_lowercase().ends_with("system32"));
    }
}
