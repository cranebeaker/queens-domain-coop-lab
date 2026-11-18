"""Queen's Domain lab helpers: launch a test copy windowed on the second monitor, inject a DLL, stop copies.

Only ever touches the copies under ../copies. Run with: py -3 -I runner/qdlab.py <command> ...
  launch <A|B> [extra game args...]   -> prints pid
  inject <pid> <dll path>
  stop                                 -> kills every game process started from ../copies
  list
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPIES = os.path.join(LAB, "copies")
EXE_REL = os.path.join("QueensDomain", "Binaries", "Win64", "QueensDomain-Win64-Shipping.exe")
# second monitor (DISPLAY1) origin and window size / slot per copy
MON_X, MON_Y = 1920, 0
WIN_W, WIN_H = 960, 540
SLOTS = {"A": (0, 0), "B": (1, 0), "C": (0, 1), "D": (1, 1)}

SETTINGS = """[/Script/Engine.GameUserSettings]
bUseVSync=False
ResolutionSizeX={w}
ResolutionSizeY={h}
LastUserConfirmedResolutionSizeX={w}
LastUserConfirmedResolutionSizeY={h}
WindowPosX={x}
WindowPosY={y}
FullscreenMode=2
LastConfirmedFullscreenMode=2
PreferredFullscreenMode=2
FrameRateLimit=60.000000

[ScalabilityGroups]
sg.ResolutionQuality=50.000000
sg.ViewDistanceQuality=0
sg.AntiAliasingQuality=0
sg.ShadowQuality=0
sg.PostProcessQuality=0
sg.TextureQuality=0
sg.EffectsQuality=0
sg.FoliageQuality=0
sg.ShadingQuality=0
"""

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)


def copy_dir(name):
    d = os.path.join(COPIES, name)
    if not os.path.isfile(os.path.join(d, EXE_REL)):
        sys.exit(f"no test copy {name} at {d}")
    return d


def local_appdata(name):
    return os.path.join(COPIES, name, "_localappdata")


def mod_saved(name, profile=1):
    d = os.path.join(local_appdata(name), "QueensDomain", "Saved", "KnightsDomain")
    return d if profile < 2 else os.path.join(d, f"DevProfile{profile}")


def window_pos(name):
    cx, cy = SLOTS[name]
    return MON_X + cx * (WIN_W + 20), MON_Y + 40 + cy * (WIN_H + 60)


def seed_settings(name):
    cfg = os.path.join(copy_dir(name), "QueensDomain", "Saved", "Config", "WindowsNoEditor")
    os.makedirs(cfg, exist_ok=True)
    x, y = window_pos(name)
    with open(os.path.join(cfg, "GameUserSettings.ini"), "w", encoding="utf-8") as f:
        f.write(SETTINGS.format(w=WIN_W, h=WIN_H, x=x, y=y))


def pid_windows(pid):
    out = []
    proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        p = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd):
            out.append(hwnd)
        return True

    u32.EnumWindows(proto(cb), 0)
    return out


def place_window(pid, name, wait=60):
    """Move the game window onto the second monitor without activating it."""
    x, y = window_pos(name)
    end = time.time() + wait
    while time.time() < end:
        for hwnd in pid_windows(pid):
            SWP_NOACTIVATE, SWP_NOZORDER = 0x10, 0x4
            u32.SetWindowPos(hwnd, 0, x, y, WIN_W, WIN_H, SWP_NOACTIVATE | SWP_NOZORDER)
            return True
        time.sleep(0.5)
    return False


def keep_logs(name):
    """The mod starts a fresh log on every launch: keep the last one in results/logs (newest 40 per copy)."""
    import shutil
    import time as _t
    out = os.path.join(LAB, "results", "logs")
    for root, _, files in os.walk(local_appdata(name)):
        for f in files:
            if f == "KnightsDomain.log":
                os.makedirs(out, exist_ok=True)
                stamp = _t.strftime("%Y%m%d-%H%M%S", _t.localtime(os.path.getmtime(os.path.join(root, f))))
                shutil.copyfile(os.path.join(root, f), os.path.join(out, f"{stamp}_{name}.log"))
    if os.path.isdir(out):
        old = sorted(x for x in os.listdir(out) if x.endswith(f"_{name}.log"))
        for x in old[:-40]:
            os.remove(os.path.join(out, x))


def launch(name, extra=()):
    d = copy_dir(name)
    seed_settings(name)
    exe = os.path.join(d, EXE_REL)
    x, y = window_pos(name)
    args = [exe, "QueensDomain", "-NotInstalled", "-windowed", f"-ResX={WIN_W}", f"-ResY={WIN_H}",
            f"-WinX={x}", f"-WinY={y}", "-nosplash", *extra]
    # The mod keeps its log/config/devcmd under %LOCALAPPDATA%\QueensDomain\Saved; give each copy its own so nothing
    # lands in the owner's real folder (the engine itself uses the copy's own Saved because of -NotInstalled).
    keep_logs(name)
    env = dict(os.environ)
    env["LOCALAPPDATA"] = local_appdata(name)
    os.makedirs(env["LOCALAPPDATA"], exist_ok=True)
    p = subprocess.Popen(args, cwd=os.path.dirname(exe), env=env)
    place_window(p.pid, name)
    return p.pid


def inject(pid, dll):
    dll = os.path.abspath(dll)
    if not os.path.isfile(dll):
        sys.exit(f"no dll {dll}")
    k32.OpenProcess.restype = wt.HANDLE
    k32.VirtualAllocEx.restype = ctypes.c_void_p
    k32.VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
    k32.WriteProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
    k32.GetModuleHandleW.restype = ctypes.c_void_p
    k32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    k32.GetProcAddress.restype = ctypes.c_void_p
    k32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    k32.CreateRemoteThread.restype = wt.HANDLE
    k32.CreateRemoteThread.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
                                       wt.DWORD, ctypes.c_void_p]
    h = k32.OpenProcess(0x1F0FFF, False, pid)
    if not h:
        sys.exit(f"OpenProcess failed: {ctypes.get_last_error()}")
    buf = ctypes.create_unicode_buffer(dll)
    size = ctypes.sizeof(buf)
    mem = k32.VirtualAllocEx(h, None, size, 0x3000, 0x04)
    k32.WriteProcessMemory(h, mem, buf, size, None)
    load = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
    th = k32.CreateRemoteThread(h, None, 0, load, mem, 0, None)
    if not th:
        sys.exit(f"CreateRemoteThread failed: {ctypes.get_last_error()}")
    k32.WaitForSingleObject(th, 15000)
    k32.CloseHandle(th)
    k32.CloseHandle(h)


def lab_pids():
    """PIDs of game processes whose exe lives under the lab copies folder (never the owner's install)."""
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='QueensDomain-Win64-Shipping.exe'\" | "
          "ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    res = []
    for line in out.splitlines():
        if "|" not in line:
            continue
        pid, path = line.split("|", 1)
        if os.path.normcase(path).startswith(os.path.normcase(COPIES)):
            res.append((int(pid), path))
    return res


def wait_gone(pids, timeout=30):
    """Wait until these processes have exited: a copy relaunched while the old one still holds its files (the mod's
    log, which it rotates at start) runs without a log."""
    end = time.time() + timeout
    while time.time() < end and any(p in pids for p, _ in lab_pids()):
        time.sleep(0.5)


def stop():
    pids = [pid for pid, _ in lab_pids()]
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    wait_gone(pids)


def stop_copy(name):
    """Kill only the copy started from copies/<name>."""
    prefix = os.path.normcase(os.path.join(COPIES, name) + os.sep)
    pids = [pid for pid, path in lab_pids() if os.path.normcase(path).startswith(prefix)]
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    wait_gone(pids)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "launch":
        print(launch(a[1], a[2:]))
    elif a[0] == "inject":
        inject(int(a[1]), a[2])
    elif a[0] == "stop":
        stop()
    elif a[0] == "list":
        for pid, path in lab_pids():
            print(pid, path)
    else:
        sys.exit(__doc__)
