//! A finished print job to the person who printed it: their Windows session, PlanetPDF's inbox
//! in their local app data (written as them), then PlanetPDF started in their session with it.
//! PlanetPDF asks where to save it, or uses the folder set in Preferences (ui/printouts.py).

use std::ffi::c_void;
use std::fs::File;
use std::io;
use std::path::{Path, PathBuf};

use crate::ffi::{self, HANDLE};
use crate::naming;

pub struct Job {
    pub id: u32,
    pub user: String,
    pub document: String,
}

/// Closes a Windows handle when dropped.
struct Handle(HANDLE);

impl Drop for Handle {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: a handle we own.
            unsafe { ffi::CloseHandle(self.0) };
        }
    }
}

/// While alive the thread acts as the user (their file permissions, their network drives).
struct Impersonation;

impl Impersonation {
    fn start(token: &Handle) -> io::Result<Impersonation> {
        // SAFETY: a valid primary token from WTSQueryUserToken.
        if unsafe { ffi::ImpersonateLoggedOnUser(token.0) } == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Impersonation)
    }
}

impl Drop for Impersonation {
    fn drop(&mut self) {
        // SAFETY: undoes ImpersonateLoggedOnUser on this thread.
        unsafe { ffi::RevertToSelf() };
    }
}

/// Puts the spooled PDF in the job's user's PlanetPDF inbox and opens it in PlanetPDF there.
/// Returns where it went.
pub fn deliver(job: &Job, spooled: &Path) -> Result<PathBuf, String> {
    let session = user_session(&job.user).ok_or_else(|| format!("{} isn't signed in here", job.user))?;
    let token = user_token(session).map_err(|e| format!("no access to {}'s session {session}: {e}", job.user))?;
    let local = local_app_data(&token).map_err(|e| format!("{}'s local app data folder: {e}", job.user))?;
    let mut source = File::open(spooled).map_err(|e| format!("the spooled job: {e}"))?;  // (as SYSTEM)
    let target = {
        let _as_user = Impersonation::start(&token).map_err(|e| format!("acting as {}: {e}", job.user))?;
        // a folder per job, so two printouts of the same name never meet
        let folder = naming::INBOX.iter().fold(local.clone(), |path, part| path.join(part))
            .join(format!("job-{}-{}", job.id, std::process::id()));
        std::fs::create_dir_all(&folder).map_err(|e| format!("{}: {e}", folder.display()))?;
        let target = naming::unique_path(&folder, &naming::file_stem(&job.document));
        let mut out = File::create(&target).map_err(|e| format!("{}: {e}", target.display()))?;
        io::copy(&mut source, &mut out).map_err(|e| format!("{}: {e}", target.display()))?;
        out.sync_all().map_err(|e| format!("{}: {e}", target.display()))?;
        target
    };
    if let Err(e) = open_in_planetpdf(&token, &target, &local) {
        crate::log::log(&format!("job {}: saved {}, but PlanetPDF didn't start: {e}", job.id, target.display()));
    }
    Ok(target)
}

/// The session of `user` (an active one first; a disconnected one gets the file when they come back).
fn user_session(user: &str) -> Option<u32> {
    let mut sessions: *mut ffi::WTS_SESSION_INFOW = std::ptr::null_mut();
    let mut count = 0;
    // SAFETY: WTS fills in an array of `count` entries that we free with WTSFreeMemory.
    unsafe {
        if ffi::WTSEnumerateSessionsW(ffi::WTS_CURRENT_SERVER_HANDLE, 0, 1, &mut sessions, &mut count) == 0 {
            return None;
        }
        let list = std::slice::from_raw_parts(sessions, count as usize);
        let mut found: Option<(bool, u32)> = None;
        for info in list {
            if session_user(info.SessionId).is_some_and(|name| name.eq_ignore_ascii_case(user)) {
                let active = info.State == ffi::WTS_ACTIVE;
                if found.is_none_or(|(was_active, _)| active && !was_active) {
                    found = Some((active, info.SessionId));
                }
            }
        }
        ffi::WTSFreeMemory(sessions as *mut c_void);
        found.map(|(_, id)| id)
    }
}

fn session_user(session: u32) -> Option<String> {
    let mut buffer: ffi::LPWSTR = std::ptr::null_mut();
    let mut bytes = 0;
    // SAFETY: WTS returns a NUL-terminated string that we free.
    unsafe {
        if ffi::WTSQuerySessionInformationW(ffi::WTS_CURRENT_SERVER_HANDLE, session, ffi::WTS_USER_NAME, &mut buffer,
                                            &mut bytes) == 0 {
            return None;
        }
        let name = ffi::from_wide(buffer);
        ffi::WTSFreeMemory(buffer as *mut c_void);
        (!name.is_empty()).then_some(name)
    }
}

fn user_token(session: u32) -> io::Result<Handle> {
    let mut token: HANDLE = std::ptr::null_mut();
    // SAFETY: the spooler runs as SYSTEM, which may ask for a signed-in user's token.
    if unsafe { ffi::WTSQueryUserToken(session, &mut token) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(Handle(token))
}

fn local_app_data(token: &Handle) -> io::Result<PathBuf> {
    let mut path: ffi::LPWSTR = std::ptr::null_mut();
    // SAFETY: the shell returns a string we free with CoTaskMemFree.
    unsafe {
        let hr = ffi::SHGetKnownFolderPath(&ffi::FOLDERID_LOCAL_APP_DATA, 0, token.0, &mut path);
        if hr < 0 {
            return Err(io::Error::other(format!("error 0x{:08X}", hr as u32)));
        }
        let folder = PathBuf::from(ffi::from_wide(path));
        ffi::CoTaskMemFree(path as *mut c_void);
        Ok(folder)
    }
}

/// PlanetPDF's launcher (the installer records its folder), else None.
pub fn launcher() -> Option<PathBuf> {
    let key = ffi::wide(r"SOFTWARE\Planet Networks\PlanetPDF");
    let value = ffi::wide("InstallDir");
    let mut buffer = vec![0u16; 1024];
    let mut bytes = (buffer.len() * 2) as u32;
    // SAFETY: a buffer of `bytes` bytes for a string value.
    let status = unsafe {
        ffi::RegGetValueW(ffi::HKEY_LOCAL_MACHINE, key.as_ptr(), value.as_ptr(),
                          ffi::RRF_RT_REG_SZ | ffi::RRF_SUBKEY_WOW6464KEY, std::ptr::null_mut(),
                          buffer.as_mut_ptr() as *mut c_void, &mut bytes)
    };
    if status != 0 {
        return None;
    }
    // SAFETY: RegGetValueW NUL-terminates what it wrote.
    let folder = unsafe { ffi::from_wide(buffer.as_ptr()) };
    let exe = Path::new(&folder).join("PlanetPDF.exe");
    exe.is_file().then_some(exe)
}

/// Starts PlanetPDF in the user's session (as them, on their desktop) with the file.
fn open_in_planetpdf(token: &Handle, file: &Path, folder: &Path) -> io::Result<()> {
    let exe = launcher().ok_or_else(|| io::Error::other("PlanetPDF's install folder isn't recorded"))?;
    let exe_w = ffi::wide(&exe.to_string_lossy());
    let mut command = ffi::wide(&format!("\"{}\" \"{}\"", exe.display(), file.display()));
    let folder = ffi::wide(&folder.to_string_lossy());
    let mut desktop = ffi::wide(r"winsta0\default");
    let mut environment: *mut c_void = std::ptr::null_mut();
    // SAFETY: all strings are NUL-terminated buffers that outlive the call; handles are closed.
    unsafe {
        if ffi::CreateEnvironmentBlock(&mut environment, token.0, 0) == 0 {
            environment = std::ptr::null_mut();
        }
        let mut startup: ffi::STARTUPINFOW = std::mem::zeroed();
        startup.cb = std::mem::size_of::<ffi::STARTUPINFOW>() as u32;
        startup.lpDesktop = desktop.as_mut_ptr();
        let mut process: ffi::PROCESS_INFORMATION = std::mem::zeroed();
        let ok = ffi::CreateProcessAsUserW(token.0, exe_w.as_ptr(), command.as_mut_ptr(), std::ptr::null(),
                                           std::ptr::null(), 0, ffi::CREATE_UNICODE_ENVIRONMENT, environment,
                                           folder.as_ptr(), &startup, &mut process);
        let error = io::Error::last_os_error();
        if !environment.is_null() {
            ffi::DestroyEnvironmentBlock(environment);
        }
        if ok == 0 {
            return Err(error);
        }
        ffi::CloseHandle(process.hThread);
        ffi::CloseHandle(process.hProcess);
    }
    Ok(())
}
