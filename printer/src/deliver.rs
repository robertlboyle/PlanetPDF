//! A finished print job to the person who printed it: their Windows session, PlanetPDF's inbox
//! in their local app data (written as them), then PlanetPDF started in their session with it.
//! PlanetPDF asks where to save it, or uses the folder set in Preferences (ui/printouts.py).

use std::ffi::c_void;
use std::fs::File;
use std::io;
use std::path::{Path, PathBuf};

use crate::ffi::{self, HANDLE};
use crate::naming;

#[derive(Clone, Debug, PartialEq)]
pub struct Job {
    pub id: u32,
    pub user: String,
    /// The account's SID when Windows said who wrote the job ("" when only the queue's name is known).
    pub sid: String,
    pub document: String,
}

/// Why a printout wasn't delivered.
#[derive(Clone, Debug, PartialEq)]
pub enum Failure {
    /// It can't be told for sure whose session it belongs in: the printout is held, never guessed.
    Unsure(String),
    /// Whose it is is known, but it couldn't be handed over (they signed out, a folder failed…).
    Failed(String),
}

impl std::fmt::Display for Failure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Failure::Unsure(why) | Failure::Failed(why) => f.write_str(why),
        }
    }
}

/// Someone signed in to this computer: their session, and the account as Windows knows it.
#[derive(Clone, Debug, PartialEq)]
pub struct Session {
    pub id: u32,
    pub active: bool,
    pub user: String,
    pub sid: String,
}

/// The session a printout belongs in (security list 2026-11-26, item 4: sessions were matched by
/// user name alone, so a local "alice" and a domain "alice" signed in together could get each
/// other's printouts). With the account's SID, only that account's session (an active one first; a
/// disconnected one gets the file when they come back). With only a name (the queue's, when the
/// spooler's own account wrote the job): that name's session, unless two different accounts
/// carry the name, and then nobody's. ``others``: the SIDs of the accounts this computer knows by
/// that name (its own and the domain's, ``accounts_named``): security review 2026-11-27, finding
/// 11: with only one of them signed in, a printout by the other went to it (the driver gives only
/// the name for every job), so a name two accounts carry is never enough, signed in or not.
pub fn pick_session(sessions: &[Session], user: &str, sid: &str, others: &[String]) -> Result<u32, Failure> {
    let best = |list: &[&Session]| list.iter().find(|s| s.active).or(list.first()).map(|s| s.id);
    let gone = || Failure::Failed(format!("{user} isn't signed in here"));
    if !sid.is_empty() {
        let theirs: Vec<&Session> = sessions.iter().filter(|s| s.sid.eq_ignore_ascii_case(sid)).collect();
        return best(&theirs).ok_or_else(gone);
    }
    let named: Vec<&Session> = sessions.iter().filter(|s| s.user.eq_ignore_ascii_case(user)).collect();
    let one_account = named.iter().all(|s| !s.sid.is_empty() && s.sid.eq_ignore_ascii_case(&named[0].sid));
    if named.len() > 1 && !one_account {
        return Err(Failure::Unsure(format!("more than one account named {user} is signed in here, and Windows \
                                            gave only the name")));
    }
    if let Some(first) = named.first() {
        if others.iter().any(|other| !other.eq_ignore_ascii_case(&first.sid)) {
            return Err(Failure::Unsure(format!("two accounts are named {user} on this computer (its own and the \
                                                domain's), and Windows gave only the name")));
        }
    }
    best(&named).ok_or_else(gone)
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
pub fn deliver(job: &Job, spooled: &Path) -> Result<PathBuf, Failure> {
    let others = if job.sid.is_empty() { accounts_named(&job.user) } else { Vec::new() };
    let session = pick_session(&sessions(), &job.user, &job.sid, &others)?;
    let failed = Failure::Failed;
    let token = user_token(session)
        .map_err(|e| failed(format!("no access to {}'s session {session}: {e}", job.user)))?;
    let local = local_app_data(&token).map_err(|e| failed(format!("{}'s local app data folder: {e}", job.user)))?;
    let mut source = File::open(spooled).map_err(|e| failed(format!("the spooled job: {e}")))?;  // (as SYSTEM)
    let target = {
        let _as_user = Impersonation::start(&token).map_err(|e| failed(format!("acting as {}: {e}", job.user)))?;
        // a folder per job, so two printouts of the same name never meet
        let folder = naming::INBOX.iter().fold(local.clone(), |path, part| path.join(part))
            .join(format!("job-{}-{}", job.id, std::process::id()));
        std::fs::create_dir_all(&folder).map_err(|e| failed(format!("{}: {e}", folder.display())))?;
        let target = naming::unique_path(&folder, &naming::file_stem(&job.document));
        let mut out = File::create(&target).map_err(|e| failed(format!("{}: {e}", target.display())))?;
        io::copy(&mut source, &mut out).map_err(|e| failed(format!("{}: {e}", target.display())))?;
        out.sync_all().map_err(|e| failed(format!("{}: {e}", target.display())))?;
        target
    };
    if let Err(e) = open_in_planetpdf(&token, &target, &local) {
        crate::log::log(&format!("job {}: saved {}, but PlanetPDF didn't start: {e}", job.id, target.display()));
    }
    Ok(target)
}

/// Everyone signed in here, each with their account's SID (read from their session's own token).
fn sessions() -> Vec<Session> {
    let mut list: *mut ffi::WTS_SESSION_INFOW = std::ptr::null_mut();
    let mut count = 0;
    // SAFETY: WTS fills in an array of `count` entries that we free with WTSFreeMemory.
    unsafe {
        if ffi::WTSEnumerateSessionsW(ffi::WTS_CURRENT_SERVER_HANDLE, 0, 1, &mut list, &mut count) == 0 {
            return Vec::new();
        }
        let found = std::slice::from_raw_parts(list, count as usize)
            .iter()
            .filter_map(|info| {
                let user = session_user(info.SessionId)?;
                let sid = user_token(info.SessionId).ok().and_then(|token| token_sid(token.0)).unwrap_or_default();
                Some(Session { id: info.SessionId, active: info.State == ffi::WTS_ACTIVE, user, sid })
            })
            .collect();
        ffi::WTSFreeMemory(list as *mut c_void);
        found
    }
}

/// The account a token stands for, as its SID in text ("S-1-5-21-…"); None when Windows won't say.
pub fn token_sid(token: HANDLE) -> Option<String> {
    // SAFETY: the buffer is sized as Windows asks (8-byte aligned); the SID string is freed.
    unsafe {
        let mut needed = 0u32;
        ffi::GetTokenInformation(token, ffi::TOKEN_USER, std::ptr::null_mut(), 0, &mut needed);
        let mut buffer = vec![0u64; (needed as usize).div_ceil(8).max(1)];
        if ffi::GetTokenInformation(token, ffi::TOKEN_USER, buffer.as_mut_ptr() as *mut _, needed, &mut needed) == 0 {
            return None;
        }
        let sid = *(buffer.as_ptr() as *const *mut c_void);  // (TOKEN_USER.User.Sid)
        let mut text: ffi::LPWSTR = std::ptr::null_mut();
        if ffi::ConvertSidToStringSidW(sid, &mut text) == 0 {
            return None;
        }
        let value = ffi::from_wide(text);
        ffi::LocalFree(text as *mut _);
        (!value.is_empty()).then_some(value)
    }
}

/// The SIDs of the accounts this computer knows by ``user``: its own local account, and the
/// domain's (by its user principal name, when the computer is in a domain).
fn accounts_named(user: &str) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    let mut names = vec![format!("{}\\{user}", computer_name())];
    if let Some(domain) = dns_domain() {
        names.push(format!("{user}@{domain}"));
    }
    for name in names {
        if let Some(sid) = account_sid(&name) {
            if !found.iter().any(|f| f.eq_ignore_ascii_case(&sid)) {
                found.push(sid);
            }
        }
    }
    found
}

fn account_sid(name: &str) -> Option<String> {
    let wide = ffi::wide(name);
    let mut sid = vec![0u64; 16];  // (a SID is at most 68 bytes)
    let mut sid_bytes = (sid.len() * 8) as u32;
    let mut domain = [0u16; 256];
    let mut domain_len = domain.len() as u32;
    let mut kind = 0i32;
    // SAFETY: buffers of the sizes given; the SID string is freed.
    unsafe {
        if ffi::LookupAccountNameW(std::ptr::null(), wide.as_ptr(), sid.as_mut_ptr() as *mut c_void, &mut sid_bytes,
                                   domain.as_mut_ptr(), &mut domain_len, &mut kind) == 0 || kind != 1 {
            return None;  // (none, or not a user: SidTypeUser = 1)
        }
        let mut text: ffi::LPWSTR = std::ptr::null_mut();
        if ffi::ConvertSidToStringSidW(sid.as_mut_ptr() as *mut c_void, &mut text) == 0 {
            return None;
        }
        let value = ffi::from_wide(text);
        ffi::LocalFree(text as *mut _);
        Some(value)
    }
}

fn computer_name() -> String {
    computer_name_ex(0)  // (ComputerNameNetBIOS)
        .unwrap_or_default()
}

fn dns_domain() -> Option<String> {
    computer_name_ex(2).filter(|d| !d.is_empty())  // (ComputerNameDnsDomain: empty when not in a domain)
}

fn computer_name_ex(kind: i32) -> Option<String> {
    let mut buffer = [0u16; 256];
    let mut size = buffer.len() as u32;
    // SAFETY: a buffer of `size` UTF-16 units.
    (unsafe { ffi::GetComputerNameExW(kind, buffer.as_mut_ptr(), &mut size) } != 0)
        .then(|| String::from_utf16_lossy(&buffer[..size as usize]))
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

#[cfg(test)]
mod tests {
    use super::{pick_session, Failure, Session};

    fn session(id: u32, active: bool, user: &str, sid: &str) -> Session {
        Session { id, active, user: user.into(), sid: sid.into() }
    }

    const LOCAL_ALICE: &str = "S-1-5-21-1-1-1-1001";
    const DOMAIN_ALICE: &str = "S-1-5-21-9-9-9-2001";

    #[test]
    fn the_printout_goes_to_the_account_not_just_the_name() {
        // a local "alice" and a domain "alice", both signed in (the local one active)
        let both = [session(2, true, "alice", LOCAL_ALICE), session(3, false, "Alice", DOMAIN_ALICE),
                    session(4, true, "bob", "S-1-5-21-1-1-1-1002")];
        assert_eq!(pick_session(&both, "alice", DOMAIN_ALICE, &[]), Ok(3));  // (hers, though the other alice is active)
        assert_eq!(pick_session(&both, "alice", LOCAL_ALICE, &[]), Ok(2));
        // the account isn't signed in: nobody else with the name gets it
        assert!(matches!(pick_session(&both, "alice", "S-1-5-21-7-7-7-3001", &[]), Err(Failure::Failed(_))));
    }

    #[test]
    fn an_account_in_two_sessions_gets_it_in_the_active_one() {
        let twice = [session(2, false, "alice", LOCAL_ALICE), session(5, true, "alice", LOCAL_ALICE)];
        assert_eq!(pick_session(&twice, "alice", LOCAL_ALICE, &[]), Ok(5));
        assert_eq!(pick_session(&twice, "ALICE", "", &[]), Ok(5));  // (only a name, but one account)
        assert_eq!(pick_session(&twice[..1], "alice", LOCAL_ALICE, &[]), Ok(2));  // (disconnected: when she's back)
    }

    #[test]
    fn a_name_two_accounts_carry_is_nobodys_without_the_account() {
        let both = [session(2, true, "alice", LOCAL_ALICE), session(3, false, "alice", DOMAIN_ALICE)];
        assert!(matches!(pick_session(&both, "alice", "", &[]), Err(Failure::Unsure(_))));
        // (a session whose account couldn't be read counts as another account)
        let unread = [session(2, true, "alice", LOCAL_ALICE), session(3, false, "alice", "")];
        assert!(matches!(pick_session(&unread, "alice", "", &[]), Err(Failure::Unsure(_))));
        // one alice only, and the only account of that name: hers, as before
        assert_eq!(pick_session(&both[..1], "alice", "", &[LOCAL_ALICE.to_string()]), Ok(2));
        assert_eq!(pick_session(&[session(7, true, "alice", "")], "alice", "", &[]), Ok(7));
        assert!(matches!(pick_session(&both, "carol", "", &[]), Err(Failure::Failed(_))));
        assert!(matches!(pick_session(&[], "alice", LOCAL_ALICE, &[]), Err(Failure::Failed(_))));
    }

    #[test]
    fn a_name_another_account_carries_is_held_even_when_only_one_is_signed_in() {
        // (security review 2026-11-27, finding 11: the domain alice signed in, the local alice printed
        // with only her name, and it went to the domain alice)
        let domain_only = [session(3, true, "alice", DOMAIN_ALICE)];
        let accounts = [LOCAL_ALICE.to_string(), DOMAIN_ALICE.to_string()];
        assert!(matches!(pick_session(&domain_only, "alice", "", &accounts), Err(Failure::Unsure(_))));
        // with the account Windows gave, still hers
        assert_eq!(pick_session(&domain_only, "alice", DOMAIN_ALICE, &accounts), Ok(3));
        // and with only her own account of that name on the computer, hers
        assert_eq!(pick_session(&domain_only, "alice", "", &[DOMAIN_ALICE.to_string()]), Ok(3));
    }
}
