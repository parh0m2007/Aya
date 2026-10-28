//! Ядро Аи (Python) как часть приложения: один ярлык вместо двух процессов.
//!
//! При старте, если на 127.0.0.1:8330 никто не слушает, запускаем
//! `core\.venv\Scripts\pythonw.exe core\main.py` без окна консоли, пишем логи
//! в `~/.aya/logs/core.out/err`, перезапускаем при падении (до 3 раз подряд) и
//! аккуратно гасим при выходе. Если ядро уже запущено (start-aya.cmd, второй
//! экземпляр) — ничего не трогаем.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const ADDR: &str = "127.0.0.1:8330";

pub struct CoreProc {
    child: Mutex<Option<Child>>,
    token: String,
    root: Option<PathBuf>,
    stopping: Mutex<bool>,
}

pub fn core_listening() -> bool {
    let addr: SocketAddr = ADDR.parse().expect("addr");
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

/// Папка проекта: AYA_ROOT, затем вверх от exe до `core/main.py`, затем путь сборки.
pub fn project_root() -> Option<PathBuf> {
    let has_core = |p: &Path| p.join("core").join("main.py").is_file();
    if let Ok(r) = std::env::var("AYA_ROOT") {
        let p = PathBuf::from(r);
        if has_core(&p) {
            return Some(p);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().map(Path::to_path_buf);
        while let Some(d) = dir {
            if has_core(&d) {
                return Some(d);
            }
            dir = d.parent().map(Path::to_path_buf);
        }
    }
    let built = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    if has_core(&built) {
        return built.canonicalize().ok();
    }
    None
}

fn python(root: &Path) -> PathBuf {
    // 1) свой Python из установщика (рядом с aya.exe), 2) venv разработчика
    let bundled = root.join("python");
    let venv = root.join("core").join(".venv").join("Scripts");
    for p in [
        bundled.join("pythonw.exe"),
        bundled.join("python.exe"),
        venv.join("pythonw.exe"),
    ] {
        if p.is_file() {
            return p;
        }
    }
    venv.join("python.exe")
}

/// Логи ядра — в ~/.aya/logs: папка установки при обновлении заменяется.
fn logs_dir() -> PathBuf {
    let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"));
    match home {
        Some(h) => PathBuf::from(h).join(".aya").join("logs"),
        None => PathBuf::from("logs"),
    }
}

fn rotate(path: &Path) {
    if path.exists() {
        let old = path.with_extension(format!(
            "{}.1",
            path.extension().and_then(|e| e.to_str()).unwrap_or("log")
        ));
        let _ = fs::remove_file(&old);
        let _ = fs::rename(path, old);
    }
}

fn spawn(root: &Path, token: &str) -> Option<Child> {
    let logs = logs_dir();
    let _ = fs::create_dir_all(&logs);
    let out_p = logs.join("core.out");
    let err_p = logs.join("core.err");
    rotate(&out_p);
    rotate(&err_p);
    let out = OpenOptions::new().create(true).append(true).open(&out_p).ok()?;
    let err = OpenOptions::new().create(true).append(true).open(&err_p).ok()?;
    let mut cmd = Command::new(python(root));
    cmd.arg(root.join("core").join("main.py"))
        .current_dir(root)
        .env("AYA_SHUTDOWN_TOKEN", token)
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::from(out))
        .stderr(Stdio::from(err));
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let child = cmd.spawn().ok()?;
    #[cfg(target_os = "windows")]
    bind_to_app(&child);
    Some(child)
}

/// «Задание» Windows с KILL_ON_JOB_CLOSE: процессы в нём гибнут, как только
/// закрывается последний его дескриптор, — то есть вместе с aya.exe, как бы
/// оно ни завершилось (выход, обновление через std::process::exit, падение,
/// снятие задачи). Иначе ядро оставалось бы жить и держать файлы установки.
#[cfg(target_os = "windows")]
pub struct KillOnClose(windows_sys::Win32::Foundation::HANDLE);

#[cfg(target_os = "windows")]
unsafe impl Send for KillOnClose {}
#[cfg(target_os = "windows")]
unsafe impl Sync for KillOnClose {}

#[cfg(target_os = "windows")]
impl KillOnClose {
    pub fn new() -> Option<Self> {
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                windows_sys::Win32::Foundation::CloseHandle(job);
                return None;
            }
            Some(KillOnClose(job))
        }
    }

    pub fn assign(&self, child: &Child) {
        use std::os::windows::io::AsRawHandle;
        unsafe {
            windows_sys::Win32::System::JobObjects::AssignProcessToJobObject(self.0, child.as_raw_handle() as _);
        }
    }
}

#[cfg(target_os = "windows")]
impl Drop for KillOnClose {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0);
        }
    }
}

/// Одно задание на всё время жизни aya.exe: дескриптор закроет сама Windows при выходе.
#[cfg(target_os = "windows")]
fn bind_to_app(child: &Child) {
    use std::sync::OnceLock;
    static JOB: OnceLock<Option<KillOnClose>> = OnceLock::new();
    if let Some(job) = JOB.get_or_init(KillOnClose::new) {
        job.assign(child);
    }
}

impl CoreProc {
    /// Запустить ядро, если оно ещё не работает, и следить за ним.
    pub fn start() -> Arc<CoreProc> {
        let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
        let token = format!("{:x}{:x}", nanos, std::process::id());
        let root = project_root();
        let proc = Arc::new(CoreProc {
            child: Mutex::new(None),
            token,
            root: root.clone(),
            stopping: Mutex::new(false),
        });
        if core_listening() || root.is_none() {
            return proc; // уже запущено снаружи (или проект не найден) — не вмешиваемся
        }
        *proc.child.lock().unwrap() = spawn(root.as_ref().unwrap(), &proc.token);
        let watch = Arc::clone(&proc);
        thread::spawn(move || watch.supervise());
        proc
    }

    fn supervise(&self) {
        let mut restarts = 0u32;
        let mut last_start = Instant::now();
        loop {
            thread::sleep(Duration::from_secs(5));
            if *self.stopping.lock().unwrap() {
                return;
            }
            let mut guard = self.child.lock().unwrap();
            let exited = match guard.as_mut() {
                Some(c) => matches!(c.try_wait(), Ok(Some(_))),
                None => return,
            };
            if !exited {
                if last_start.elapsed() > Duration::from_secs(120) {
                    restarts = 0; // проработало пару минут — счётчик падений обнуляем
                }
                continue;
            }
            if restarts >= 3 || core_listening() {
                *guard = None;
                return;
            }
            restarts += 1;
            last_start = Instant::now();
            if let Some(root) = &self.root {
                *guard = spawn(root, &self.token);
            }
        }
    }

    /// Выход из приложения: просим ядро завершиться само (MCP, мосты), потом добиваем.
    pub fn stop(&self) {
        *self.stopping.lock().unwrap() = true;
        let mut guard = self.child.lock().unwrap();
        let Some(child) = guard.as_mut() else { return };
        if let Ok(mut s) = TcpStream::connect_timeout(&ADDR.parse().unwrap(), Duration::from_millis(500)) {
            let req = format!(
                "POST /api/_shutdown HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Aya-Token: {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                self.token
            );
            let _ = s.write_all(req.as_bytes());
        }
        let deadline = Instant::now() + Duration::from_millis(2500);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                *guard = None;
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = child.kill();
        let _ = child.wait();
        *guard = None;
    }
}

#[cfg(test)]
mod tests {
    use super::python;
    use std::fs;

    #[test]
    fn python_prefers_bundled_runtime() {
        let dir = std::env::temp_dir().join(format!("aya-py-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let venv = dir.join("core").join(".venv").join("Scripts");
        fs::create_dir_all(&venv).unwrap();
        fs::write(venv.join("pythonw.exe"), b"").unwrap();
        assert_eq!(python(&dir), venv.join("pythonw.exe"), "только venv — берём его");
        let bundled = dir.join("python");
        fs::create_dir_all(&bundled).unwrap();
        fs::write(bundled.join("python.exe"), b"").unwrap();
        assert_eq!(python(&dir), bundled.join("python.exe"), "свой Python важнее venv");
        fs::write(bundled.join("pythonw.exe"), b"").unwrap();
        assert_eq!(python(&dir), bundled.join("pythonw.exe"), "без окна консоли — лучше");
        let _ = fs::remove_dir_all(&dir);
    }
}

#[cfg(all(test, target_os = "windows"))]
mod job_tests {
    use super::KillOnClose;
    use std::process::{Command, Stdio};
    use std::time::{Duration, Instant};

    #[test]
    fn child_dies_when_job_closes() {
        // ядро в «задании» Windows: закрылось приложение (в т.ч. обновлением или
        // принудительно) — Windows сама гасит ядро, оно не держит папку установки
        let job = KillOnClose::new().expect("job");
        let mut child = Command::new("cmd")
            .args(["/c", "ping -n 30 127.0.0.1 >nul"])
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        job.assign(&child);
        assert!(child.try_wait().unwrap().is_none(), "пока задание живо — процесс работает");
        drop(job);
        let t = Instant::now();
        while child.try_wait().unwrap().is_none() {
            assert!(t.elapsed() < Duration::from_secs(5), "процесс не завершился");
            std::thread::sleep(Duration::from_millis(50));
        }
    }
}
