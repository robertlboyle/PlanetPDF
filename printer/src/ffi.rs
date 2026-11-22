//! The Windows functions and structures used here, declared by hand (no crates). Layouts follow
//! the Windows SDK headers (winspool.h, winsplp.h, wtsapi32.h, processthreadsapi.h).
#![allow(non_snake_case, non_camel_case_types, clippy::upper_case_acronyms, dead_code)]

use std::ffi::c_void;

pub type BOOL = i32;
pub type DWORD = u32;
pub type HANDLE = *mut c_void;
pub type LPWSTR = *mut u16;
pub type LPCWSTR = *const u16;

pub const TRUE: BOOL = 1;
pub const FALSE: BOOL = 0;

pub const ERROR_INSUFFICIENT_BUFFER: DWORD = 122;
pub const ERROR_INVALID_PARAMETER: DWORD = 87;
pub const ERROR_INVALID_LEVEL: DWORD = 124;
pub const ERROR_NOT_SUPPORTED: DWORD = 50;
pub const ERROR_PRINT_MONITOR_ALREADY_INSTALLED: DWORD = 3006;
pub const ERROR_PRINTER_ALREADY_EXISTS: DWORD = 1802;
pub const ERROR_UNKNOWN_PRINTER_DRIVER: DWORD = 1797;
pub const ERROR_UNKNOWN_PORT: DWORD = 1796;
pub const ERROR_INVALID_PRINTER_NAME: DWORD = 1801;
pub const ERROR_UNKNOWN_PRINT_MONITOR: DWORD = 3000;
pub const ERROR_SUCCESS: DWORD = 0;

pub const JOB_CONTROL_SENT_TO_PRINTER: DWORD = 6;
pub const PRINTER_ATTRIBUTE_LOCAL: DWORD = 0x40;
pub const PRINTER_ALL_ACCESS: DWORD = 0x000F_000C;
pub const PORT_TYPE_WRITE: DWORD = 0x1;
pub const WTS_CURRENT_SERVER_HANDLE: HANDLE = std::ptr::null_mut();
pub const WTS_ACTIVE: i32 = 0;
pub const WTS_USER_NAME: i32 = 5;
pub const WTS_DOMAIN_NAME: i32 = 7;
pub const CREATE_UNICODE_ENVIRONMENT: DWORD = 0x400;
pub const SDDL_REVISION_1: DWORD = 1;
pub const HKEY_LOCAL_MACHINE: HANDLE = 0x8000_0002_usize as HANDLE;
pub const RRF_RT_REG_SZ: DWORD = 0x2;
pub const RRF_SUBKEY_WOW6464KEY: DWORD = 0x10000;

#[repr(C)]
pub struct GUID {
    pub data1: u32,
    pub data2: u16,
    pub data3: u16,
    pub data4: [u8; 8],
}

/// FOLDERID_LocalAppData {F1B32785-6FBA-4FCF-9D55-7B8E7F157091}
pub const FOLDERID_LOCAL_APP_DATA: GUID = GUID {
    data1: 0xF1B3_2785,
    data2: 0x6FBA,
    data3: 0x4FCF,
    data4: [0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91],
};

#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct SYSTEMTIME {
    pub wYear: u16,
    pub wMonth: u16,
    pub wDayOfWeek: u16,
    pub wDay: u16,
    pub wHour: u16,
    pub wMinute: u16,
    pub wSecond: u16,
    pub wMilliseconds: u16,
}

#[repr(C)]
pub struct JOB_INFO_1W {
    pub JobId: DWORD,
    pub pPrinterName: LPWSTR,
    pub pMachineName: LPWSTR,
    pub pUserName: LPWSTR,
    pub pDocument: LPWSTR,
    pub pDatatype: LPWSTR,
    pub pStatus: LPWSTR,
    pub Status: DWORD,
    pub Priority: DWORD,
    pub Position: DWORD,
    pub TotalPages: DWORD,
    pub PagesPrinted: DWORD,
    pub Submitted: SYSTEMTIME,
}

#[repr(C)]
pub struct DOC_INFO_1W {
    pub pDocName: LPWSTR,
    pub pOutputFile: LPWSTR,
    pub pDatatype: LPWSTR,
}

#[repr(C)]
pub struct PORT_INFO_1W {
    pub pName: LPWSTR,
}

#[repr(C)]
pub struct PORT_INFO_2W {
    pub pPortName: LPWSTR,
    pub pMonitorName: LPWSTR,
    pub pDescription: LPWSTR,
    pub fPortType: DWORD,
    pub Reserved: DWORD,
}

#[repr(C)]
pub struct MONITOR_INFO_1W {
    pub pName: LPWSTR,
}

#[repr(C)]
pub struct MONITOR_INFO_2W {
    pub pName: LPWSTR,
    pub pEnvironment: LPWSTR,
    pub pDLLName: LPWSTR,
}

#[repr(C)]
pub struct PRINTER_INFO_2W {
    pub pServerName: LPWSTR,
    pub pPrinterName: LPWSTR,
    pub pShareName: LPWSTR,
    pub pPortName: LPWSTR,
    pub pDriverName: LPWSTR,
    pub pComment: LPWSTR,
    pub pLocation: LPWSTR,
    pub pDevMode: *mut c_void,
    pub pSepFile: LPWSTR,
    pub pPrintProcessor: LPWSTR,
    pub pDatatype: LPWSTR,
    pub pParameters: LPWSTR,
    pub pSecurityDescriptor: *mut c_void,
    pub Attributes: DWORD,
    pub Priority: DWORD,
    pub DefaultPriority: DWORD,
    pub StartTime: DWORD,
    pub UntilTime: DWORD,
    pub Status: DWORD,
    pub cJobs: DWORD,
    pub AveragePPM: DWORD,
}

#[repr(C)]
pub struct DRIVER_INFO_1W {
    pub pName: LPWSTR,
}

#[repr(C)]
pub struct PRINTER_DEFAULTSW {
    pub pDatatype: LPWSTR,
    pub pDevMode: *mut c_void,
    pub DesiredAccess: DWORD,
}

#[repr(C)]
pub struct WTS_SESSION_INFOW {
    pub SessionId: DWORD,
    pub pWinStationName: LPWSTR,
    pub State: i32,
}

#[repr(C)]
pub struct SECURITY_ATTRIBUTES {
    pub nLength: DWORD,
    pub lpSecurityDescriptor: *mut c_void,
    pub bInheritHandle: BOOL,
}

#[repr(C)]
pub struct STARTUPINFOW {
    pub cb: DWORD,
    pub lpReserved: LPWSTR,
    pub lpDesktop: LPWSTR,
    pub lpTitle: LPWSTR,
    pub dwX: DWORD,
    pub dwY: DWORD,
    pub dwXSize: DWORD,
    pub dwYSize: DWORD,
    pub dwXCountChars: DWORD,
    pub dwYCountChars: DWORD,
    pub dwFillAttribute: DWORD,
    pub dwFlags: DWORD,
    pub wShowWindow: u16,
    pub cbReserved2: u16,
    pub lpReserved2: *mut u8,
    pub hStdInput: HANDLE,
    pub hStdOutput: HANDLE,
    pub hStdError: HANDLE,
}

#[repr(C)]
pub struct PROCESS_INFORMATION {
    pub hProcess: HANDLE,
    pub hThread: HANDLE,
    pub dwProcessId: DWORD,
    pub dwThreadId: DWORD,
}

#[link(name = "winspool")]
unsafe extern "system" {
    pub fn OpenPrinterW(pPrinterName: LPCWSTR, phPrinter: *mut HANDLE, pDefault: *const PRINTER_DEFAULTSW) -> BOOL;
    pub fn ClosePrinter(hPrinter: HANDLE) -> BOOL;
    pub fn GetJobW(hPrinter: HANDLE, JobId: DWORD, Level: DWORD, pJob: *mut u8, cbBuf: DWORD, pcbNeeded: *mut DWORD) -> BOOL;
    pub fn SetJobW(hPrinter: HANDLE, JobId: DWORD, Level: DWORD, pJob: *const u8, Command: DWORD) -> BOOL;
    pub fn AddMonitorW(pName: LPCWSTR, Level: DWORD, pMonitors: *const u8) -> BOOL;
    pub fn DeleteMonitorW(pName: LPCWSTR, pEnvironment: LPCWSTR, pMonitorName: LPCWSTR) -> BOOL;
    pub fn EnumMonitorsW(pName: LPCWSTR, Level: DWORD, pMonitor: *mut u8, cbBuf: DWORD, pcbNeeded: *mut DWORD,
                         pcReturned: *mut DWORD) -> BOOL;
    pub fn EnumPortsW(pName: LPCWSTR, Level: DWORD, pPort: *mut u8, cbBuf: DWORD, pcbNeeded: *mut DWORD,
                      pcReturned: *mut DWORD) -> BOOL;
    pub fn EnumPrinterDriversW(pName: LPCWSTR, pEnvironment: LPCWSTR, Level: DWORD, pDriverInfo: *mut u8, cbBuf: DWORD,
                               pcbNeeded: *mut DWORD, pcReturned: *mut DWORD) -> BOOL;
    pub fn AddPrinterW(pName: LPCWSTR, Level: DWORD, pPrinter: *const u8) -> HANDLE;
    pub fn DeletePrinter(hPrinter: HANDLE) -> BOOL;
}

#[link(name = "wtsapi32")]
unsafe extern "system" {
    pub fn WTSEnumerateSessionsW(hServer: HANDLE, Reserved: DWORD, Version: DWORD,
                                 ppSessionInfo: *mut *mut WTS_SESSION_INFOW, pCount: *mut DWORD) -> BOOL;
    pub fn WTSQuerySessionInformationW(hServer: HANDLE, SessionId: DWORD, WTSInfoClass: i32, ppBuffer: *mut LPWSTR,
                                       pBytesReturned: *mut DWORD) -> BOOL;
    pub fn WTSFreeMemory(pMemory: *mut c_void);
    pub fn WTSQueryUserToken(SessionId: DWORD, phToken: *mut HANDLE) -> BOOL;
}

#[link(name = "advapi32")]
unsafe extern "system" {
    pub fn ImpersonateLoggedOnUser(hToken: HANDLE) -> BOOL;
    pub fn RevertToSelf() -> BOOL;
    pub fn CreateProcessAsUserW(hToken: HANDLE, lpApplicationName: LPCWSTR, lpCommandLine: LPWSTR,
                                lpProcessAttributes: *const SECURITY_ATTRIBUTES,
                                lpThreadAttributes: *const SECURITY_ATTRIBUTES, bInheritHandles: BOOL,
                                dwCreationFlags: DWORD, lpEnvironment: *mut c_void, lpCurrentDirectory: LPCWSTR,
                                lpStartupInfo: *const STARTUPINFOW, lpProcessInformation: *mut PROCESS_INFORMATION)
                                -> BOOL;
    pub fn ConvertStringSecurityDescriptorToSecurityDescriptorW(StringSecurityDescriptor: LPCWSTR,
                                                                StringSDRevision: DWORD,
                                                                SecurityDescriptor: *mut *mut c_void,
                                                                SecurityDescriptorSize: *mut DWORD) -> BOOL;
    pub fn RegGetValueW(hkey: HANDLE, lpSubKey: LPCWSTR, lpValue: LPCWSTR, dwFlags: DWORD, pdwType: *mut DWORD,
                        pvData: *mut c_void, pcbData: *mut DWORD) -> i32;
}

#[link(name = "userenv")]
unsafe extern "system" {
    pub fn CreateEnvironmentBlock(lpEnvironment: *mut *mut c_void, hToken: HANDLE, bInherit: BOOL) -> BOOL;
    pub fn DestroyEnvironmentBlock(lpEnvironment: *mut c_void) -> BOOL;
}

#[link(name = "shell32")]
unsafe extern "system" {
    pub fn SHGetKnownFolderPath(rfid: *const GUID, dwFlags: DWORD, hToken: HANDLE, ppszPath: *mut LPWSTR) -> i32;
}

#[link(name = "ole32")]
unsafe extern "system" {
    pub fn CoTaskMemFree(pv: *mut c_void);
}

#[link(name = "kernel32")]
unsafe extern "system" {
    pub fn CloseHandle(hObject: HANDLE) -> BOOL;
    pub fn CreateDirectoryW(lpPathName: LPCWSTR, lpSecurityAttributes: *const SECURITY_ATTRIBUTES) -> BOOL;
    pub fn LocalFree(hMem: *mut c_void) -> *mut c_void;
    pub fn GetLocalTime(lpSystemTime: *mut SYSTEMTIME);
    pub fn GetLastError() -> DWORD;
    pub fn SetLastError(dwErrCode: DWORD);
    pub fn CreateNamedPipeW(lpName: LPCWSTR, dwOpenMode: DWORD, dwPipeMode: DWORD, nMaxInstances: DWORD,
                            nOutBufferSize: DWORD, nInBufferSize: DWORD, nDefaultTimeOut: DWORD,
                            lpSecurityAttributes: *const SECURITY_ATTRIBUTES) -> HANDLE;
    pub fn ConnectNamedPipe(hNamedPipe: HANDLE, lpOverlapped: *mut c_void) -> BOOL;
    pub fn DisconnectNamedPipe(hNamedPipe: HANDLE) -> BOOL;
    pub fn GetNamedPipeClientProcessId(Pipe: HANDLE, ClientProcessId: *mut u32) -> BOOL;
    pub fn WaitNamedPipeW(lpNamedPipeName: LPCWSTR, nTimeOut: DWORD) -> BOOL;
    pub fn ReadFile(hFile: HANDLE, lpBuffer: *mut u8, nNumberOfBytesToRead: DWORD, lpNumberOfBytesRead: *mut DWORD,
                    lpOverlapped: *mut c_void) -> BOOL;
}

// --- the service, the spooler's queue ----------------------------------------------------------------

pub const INVALID_HANDLE_VALUE: HANDLE = usize::MAX as HANDLE;
pub const PIPE_ACCESS_INBOUND: DWORD = 0x1;
pub const PIPE_TYPE_BYTE_WAIT: DWORD = 0x0;
pub const PIPE_REJECT_REMOTE_CLIENTS: DWORD = 0x8;
pub const PIPE_UNLIMITED_INSTANCES: DWORD = 255;
pub const ERROR_PIPE_CONNECTED: DWORD = 535;
pub const ERROR_BROKEN_PIPE: DWORD = 109;
pub const JOB_STATUS_PRINTING: DWORD = 0x10;

pub const SERVICE_WIN32_OWN_PROCESS: DWORD = 0x10;
pub const SERVICE_STOPPED: DWORD = 1;
pub const SERVICE_START_PENDING: DWORD = 2;
pub const SERVICE_STOP_PENDING: DWORD = 3;
pub const SERVICE_RUNNING: DWORD = 4;
pub const SERVICE_ACCEPT_STOP: DWORD = 0x1;
pub const SERVICE_ACCEPT_SHUTDOWN: DWORD = 0x4;
pub const SERVICE_CONTROL_STOP: DWORD = 1;
pub const SERVICE_CONTROL_INTERROGATE: DWORD = 4;
pub const SERVICE_CONTROL_SHUTDOWN: DWORD = 5;
pub const SC_MANAGER_ALL_ACCESS: DWORD = 0xF003F;
pub const SERVICE_ALL_ACCESS: DWORD = 0xF01FF;
pub const SERVICE_QUERY_STATUS: DWORD = 0x4;
pub const SC_MANAGER_CONNECT: DWORD = 0x1;
pub const SERVICE_AUTO_START: DWORD = 2;
pub const SERVICE_ERROR_NORMAL: DWORD = 1;
pub const SERVICE_NO_CHANGE: DWORD = 0xFFFF_FFFF;
pub const SERVICE_CONFIG_DESCRIPTION: DWORD = 1;
pub const SC_STATUS_PROCESS_INFO: i32 = 0;
pub const ERROR_SERVICE_EXISTS: DWORD = 1073;
pub const ERROR_SERVICE_DOES_NOT_EXIST: DWORD = 1060;
pub const ERROR_SERVICE_ALREADY_RUNNING: DWORD = 1056;
pub const ERROR_SERVICE_MARKED_FOR_DELETE: DWORD = 1072;

#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct SERVICE_STATUS {
    pub dwServiceType: DWORD,
    pub dwCurrentState: DWORD,
    pub dwControlsAccepted: DWORD,
    pub dwWin32ExitCode: DWORD,
    pub dwServiceSpecificExitCode: DWORD,
    pub dwCheckPoint: DWORD,
    pub dwWaitHint: DWORD,
}

#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct SERVICE_STATUS_PROCESS {
    pub dwServiceType: DWORD,
    pub dwCurrentState: DWORD,
    pub dwControlsAccepted: DWORD,
    pub dwWin32ExitCode: DWORD,
    pub dwServiceSpecificExitCode: DWORD,
    pub dwCheckPoint: DWORD,
    pub dwWaitHint: DWORD,
    pub dwProcessId: DWORD,
    pub dwServiceFlags: DWORD,
}

pub type ServiceMain = unsafe extern "system" fn(DWORD, *mut LPWSTR);
pub type HandlerEx = unsafe extern "system" fn(DWORD, DWORD, *mut c_void, *mut c_void) -> DWORD;

#[repr(C)]
pub struct SERVICE_TABLE_ENTRYW {
    pub lpServiceName: LPWSTR,
    pub lpServiceProc: Option<ServiceMain>,
}

#[repr(C)]
pub struct SERVICE_DESCRIPTIONW {
    pub lpDescription: LPWSTR,
}

#[link(name = "advapi32")]
unsafe extern "system" {
    pub fn StartServiceCtrlDispatcherW(lpServiceStartTable: *const SERVICE_TABLE_ENTRYW) -> BOOL;
    pub fn RegisterServiceCtrlHandlerExW(lpServiceName: LPCWSTR, lpHandlerProc: Option<HandlerEx>,
                                         lpContext: *mut c_void) -> HANDLE;
    pub fn SetServiceStatus(hServiceStatus: HANDLE, lpServiceStatus: *const SERVICE_STATUS) -> BOOL;
    pub fn OpenSCManagerW(lpMachineName: LPCWSTR, lpDatabaseName: LPCWSTR, dwDesiredAccess: DWORD) -> HANDLE;
    pub fn CreateServiceW(hSCManager: HANDLE, lpServiceName: LPCWSTR, lpDisplayName: LPCWSTR, dwDesiredAccess: DWORD,
                          dwServiceType: DWORD, dwStartType: DWORD, dwErrorControl: DWORD, lpBinaryPathName: LPCWSTR,
                          lpLoadOrderGroup: LPCWSTR, lpdwTagId: *mut DWORD, lpDependencies: LPCWSTR,
                          lpServiceStartName: LPCWSTR, lpPassword: LPCWSTR) -> HANDLE;
    pub fn OpenServiceW(hSCManager: HANDLE, lpServiceName: LPCWSTR, dwDesiredAccess: DWORD) -> HANDLE;
    pub fn ChangeServiceConfigW(hService: HANDLE, dwServiceType: DWORD, dwStartType: DWORD, dwErrorControl: DWORD,
                                lpBinaryPathName: LPCWSTR, lpLoadOrderGroup: LPCWSTR, lpdwTagId: *mut DWORD,
                                lpDependencies: LPCWSTR, lpServiceStartName: LPCWSTR, lpPassword: LPCWSTR,
                                lpDisplayName: LPCWSTR) -> BOOL;
    pub fn ChangeServiceConfig2W(hService: HANDLE, dwInfoLevel: DWORD, lpInfo: *const c_void) -> BOOL;
    pub fn StartServiceW(hService: HANDLE, dwNumServiceArgs: DWORD, lpServiceArgVectors: *const LPCWSTR) -> BOOL;
    pub fn ControlService(hService: HANDLE, dwControl: DWORD, lpServiceStatus: *mut SERVICE_STATUS) -> BOOL;
    pub fn DeleteService(hService: HANDLE) -> BOOL;
    pub fn CloseServiceHandle(hSCObject: HANDLE) -> BOOL;
    pub fn QueryServiceStatusEx(hService: HANDLE, InfoLevel: i32, lpBuffer: *mut u8, cbBufSize: DWORD,
                                pcbBytesNeeded: *mut DWORD) -> BOOL;
}

#[link(name = "winspool")]
unsafe extern "system" {
    pub fn EnumJobsW(hPrinter: HANDLE, FirstJob: DWORD, NoJobs: DWORD, Level: DWORD, pJob: *mut u8, cbBuf: DWORD,
                     pcbNeeded: *mut DWORD, pcReturned: *mut DWORD) -> BOOL;
    pub fn SetPrinterW(hPrinter: HANDLE, Level: DWORD, pPrinter: *mut u8, Command: DWORD) -> BOOL;
}

pub const PRINTER_CONTROL_PURGE: DWORD = 3;

// --- who is on the other end of the pipe (service.rs `client_allowed`) -------------------------------

pub const PROCESS_QUERY_LIMITED_INFORMATION: DWORD = 0x1000;
pub const TH32CS_SNAPPROCESS: DWORD = 0x2;
pub const MAX_PATH: usize = 260;

#[repr(C)]
pub struct PROCESSENTRY32W {
    pub dwSize: DWORD,
    pub cntUsage: DWORD,
    pub th32ProcessID: DWORD,
    pub th32DefaultHeapID: usize,
    pub th32ModuleID: DWORD,
    pub cntThreads: DWORD,
    pub th32ParentProcessID: DWORD,
    pub pcPriClassBase: i32,
    pub dwFlags: DWORD,
    pub szExeFile: [u16; MAX_PATH],
}

#[link(name = "kernel32")]
unsafe extern "system" {
    pub fn OpenProcess(dwDesiredAccess: DWORD, bInheritHandle: BOOL, dwProcessId: DWORD) -> HANDLE;
    pub fn QueryFullProcessImageNameW(hProcess: HANDLE, dwFlags: DWORD, lpExeName: LPWSTR, lpdwSize: *mut DWORD) -> BOOL;
    pub fn CreateToolhelp32Snapshot(dwFlags: DWORD, th32ProcessID: DWORD) -> HANDLE;
    pub fn Process32FirstW(hSnapshot: HANDLE, lppe: *mut PROCESSENTRY32W) -> BOOL;
    pub fn Process32NextW(hSnapshot: HANDLE, lppe: *mut PROCESSENTRY32W) -> BOOL;
    pub fn GetSystemDirectoryW(lpBuffer: LPWSTR, uSize: u32) -> u32;
}

// --- small helpers ------------------------------------------------------------------------------

/// A Rust string as a NUL-terminated UTF-16 buffer.
pub fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

/// A NUL-terminated UTF-16 string from Windows (empty for a null pointer).
///
/// # Safety
/// `p` is null or points to a NUL-terminated UTF-16 string.
pub unsafe fn from_wide(p: *const u16) -> String {
    if p.is_null() {
        return String::new();
    }
    let mut n = 0;
    // SAFETY: the caller promises a NUL-terminated string.
    unsafe {
        while *p.add(n) != 0 {
            n += 1;
        }
        String::from_utf16_lossy(std::slice::from_raw_parts(p, n))
    }
}
