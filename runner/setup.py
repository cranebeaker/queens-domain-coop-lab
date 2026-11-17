"""Set up the lab on a new PC. Run with: py -3 -I runner/setup.py <step> [...]

  copies [<game dir>]   make test copies A and B of your Steam install (B hard-links the big files, ~150 MB extra)
  dumper                clone Dumper-7 at the pinned commit into tools/Dumper-7, patch the build, build it
  sdk                   boot copy A, inject Dumper-7, move the dump to sdk/CppSDK, stop A
  kd                    clone Knights' Domain's source branch next to this repo, build its dev flavour, install it
                        into the copies (allowed by its licence for your own use; never commit or share the build)
  bridge                build bridge/qdbridge.dll
  all [<game dir>]      every step above, in order

Default game dir: C:\\Program Files (x86)\\Steam\\steamapps\\common\\QueensDomain
Needs: Visual Studio 2022 with C++, CMake 3.25+, git, Python 3.10+, the game on Steam (Steam running).
"""
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import qdlab  # noqa: E402

DEFAULT_GAME = r"C:\Program Files (x86)\Steam\steamapps\common\QueensDomain"
DUMPER_URL = "https://github.com/Encryqed/Dumper-7.git"
DUMPER_COMMIT = "2c1eae303f3d775f6f59aee6e95990d9ce2e1966"
KD_URL = "https://github.com/Lotisphere/KnightsDomain.git"
KD_DIR = os.path.join(os.path.dirname(LAB), "knights-domain")
SDK_DIR = os.path.join(LAB, "sdk")


def env_no_ndk():
    e = dict(os.environ)
    e.pop("CMAKE_TOOLCHAIN_FILE", None)   # an Android NDK toolchain here breaks every Windows configure
    return e


def run(args, cwd=None):
    print(">", " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, check=True, env=env_no_ndk())


def cmake_build(src, build, *defs):
    run(["cmake", "-S", src, "-B", build, "-G", "Visual Studio 17 2022", "-A", "x64", *defs])
    run(["cmake", "--build", build, "--config", "Release"])


def step_copies(game=DEFAULT_GAME):
    if not os.path.isfile(os.path.join(game, qdlab.EXE_REL)):
        sys.exit(f"no Queen's Domain install at {game}")
    a, b = os.path.join(qdlab.COPIES, "A"), os.path.join(qdlab.COPIES, "B")
    if not os.path.isdir(a):
        print("copying the game to", a, flush=True)
        shutil.copytree(game, a)
    if not os.path.isdir(b):
        for root, _, files in os.walk(a):
            rel = os.path.relpath(root, a)
            if rel.startswith("_localappdata") or rel.startswith(os.path.join("QueensDomain", "Saved")):
                continue
            os.makedirs(os.path.join(b, rel), exist_ok=True)
            for f in files:
                src, dst = os.path.join(root, f), os.path.join(b, rel, f)
                if f.endswith((".pak", ".mp4")):
                    os.link(src, dst)
                else:
                    shutil.copyfile(src, dst)
    for c in (a, b):
        with open(os.path.join(c, "QueensDomain", "Binaries", "Win64", "steam_appid.txt"), "w") as f:
            f.write("1799840\n")
    print("copies ready")


def step_dumper():
    d = os.path.join(LAB, "tools", "Dumper-7")
    if not os.path.isdir(d):
        run(["git", "clone", DUMPER_URL, d])
    run(["git", "checkout", "-q", DUMPER_COMMIT], cwd=d)

    def patch(rel, old, new):
        p = os.path.join(d, rel)
        s = open(p, encoding="utf-8").read()
        if old in s:
            open(p, "w", encoding="utf-8").write(s.replace(old, new))

    # this commit needs C++23 features (explicit object parameters) with MSVC
    patch("CMakeLists.txt", "cxx_std_20", "cxx_std_23")
    patch(os.path.join("cmake", "CompilerFlags.cmake"), "set(CMAKE_CXX_STANDARD 20)", "set(CMAKE_CXX_STANDARD 23)")
    patch(os.path.join("cmake", "CompilerFlags.cmake"), "/MP /std:c++20", "/MP /std:c++latest")
    patch(os.path.join("Dumper", "Settings.h"), '"C:/Dumper-7"', '"' + SDK_DIR.replace("\\", "/") + '"')
    cmake_build(d, os.path.join(d, "build"))


def step_sdk():
    dll = os.path.join(LAB, "tools", "Dumper-7", "build", "bin", "Release", "Dumper-7.dll")
    if not os.path.isfile(dll):
        sys.exit("build Dumper-7 first: runner/setup.py dumper")
    qdlab.stop()
    pid = qdlab.launch("A")
    time.sleep(25)
    qdlab.inject(pid, dll)
    end = time.time() + 300
    found = None
    while time.time() < end and not found:
        time.sleep(10)
        for root, dirs, _ in os.walk(SDK_DIR):
            if "CppSDK" in dirs and os.path.isfile(os.path.join(root, "CppSDK", "SDK", "Basic.cpp")):
                found = os.path.join(root, "CppSDK")
                break
    time.sleep(15)
    qdlab.stop()
    if not found:
        sys.exit("no SDK dump appeared in sdk/")
    target = os.path.join(SDK_DIR, "CppSDK")
    if os.path.abspath(found) != os.path.abspath(target):
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.move(found, target)
    print("SDK at", target)


def step_kd():
    if not os.path.isdir(KD_DIR):
        run(["git", "clone", "-b", "source", KD_URL, KD_DIR])
    sdk = os.path.join(SDK_DIR, "CppSDK").replace("\\", "/")
    build = os.path.join(KD_DIR, "build-dev")
    cmake_build(os.path.join(KD_DIR, "src"), build, "-DQD_DEV_BUILD=ON", f"-DQD_SDK={sdk}")
    out = os.path.join(build, "Release")
    run([os.path.join(out, "qdnet_tests.exe")])
    for c in ("A", "B"):
        w = os.path.join(qdlab.COPIES, c, "QueensDomain", "Binaries", "Win64")
        os.makedirs(os.path.join(w, "KnightsDomain"), exist_ok=True)
        shutil.copyfile(os.path.join(out, "xinput1_3.dll"), os.path.join(w, "xinput1_3.dll"))
        shutil.copyfile(os.path.join(out, "KnightsDomain.dll"), os.path.join(w, "KnightsDomain", "KnightsDomain.dll"))
    print("Knights' Domain dev build installed into A and B")


def step_bridge():
    cmake_build(os.path.join(LAB, "bridge"), os.path.join(LAB, "bridge", "build"))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    game = a[1] if len(a) > 1 else DEFAULT_GAME
    steps = {"copies": lambda: step_copies(game), "dumper": step_dumper, "sdk": step_sdk, "kd": step_kd,
             "bridge": step_bridge}
    if a[0] == "all":
        for s in ("copies", "dumper", "sdk", "kd", "bridge"):
            steps[s]()
    elif a[0] in steps:
        steps[a[0]]()
    else:
        sys.exit(__doc__)
