"""Probe v15: where did the files actually go after the surprising Q2/Q3 of probe14?

Q2: posix_rename(file -> existing_directory path) reported success but the
    directory survived. Where is the source file now?
Q3: posix_rename(file -> relative path) reported success but the file is not
    at cwd/relative_dest.json. Where is it?
"""
import ctypes
import os
import struct
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="probe15_"))

DELETE = 0x00010000
FILE_READ_ATTRIBUTES = 0x0080
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
FILE_SHARE_DELETE = 0x4
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID = ctypes.c_void_p(-1).value

FileRenameInfoEx = 22
FLAG_REPLACE_IF_EXISTS = 0x00000001
FLAG_POSIX_SEMANTICS = 0x00000002

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                             ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                             ctypes.c_void_p]
k32.CreateFileW.restype = ctypes.c_void_p
k32.CloseHandle.argtypes = [ctypes.c_void_p]
k32.SetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                            ctypes.c_void_p, ctypes.c_uint32]
k32.SetFileInformationByHandle.restype = ctypes.c_int


def say(*a):
    print(*a)
    sys.stdout.flush()


def create_handle(path, access, share):
    h = k32.CreateFileW(str(path), access, share, None, OPEN_EXISTING,
                        FILE_ATTRIBUTE_NORMAL, None)
    if not h or h == INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    return h


def posix_replace(src, dst):
    h = create_handle(src, DELETE | FILE_READ_ATTRIBUTES,
                      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
    try:
        raw = str(dst).encode("utf-16-le")
        buf = bytearray()
        buf += struct.pack("<I", FLAG_REPLACE_IF_EXISTS | FLAG_POSIX_SEMANTICS)
        buf += b"\x00" * 4
        buf += struct.pack("<Q", 0)
        buf += struct.pack("<I", len(raw))
        buf += raw
        holder = (ctypes.c_char * len(buf)).from_buffer(buf)
        if not k32.SetFileInformationByHandle(h, FileRenameInfoEx, holder, len(buf)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        k32.CloseHandle(h)


if __name__ == "__main__":
    # --- Q2 revisited: file -> existing directory ------------------------
    src2 = TMP / "src2.json"
    src2.write_text('{"v": 2}', encoding="utf-8")
    directory = TMP / "some_dir"
    directory.mkdir()
    try:
        posix_replace(src2, directory)
        say("Q2 rename reported OK")
    except OSError as exc:
        say("Q2 rename FAIL", exc)
    say("Q2 src2.json exists:", src2.exists())
    say("Q2 some_dir is_dir:", directory.is_dir(), "listing:",
        list(directory.iterdir()) if directory.is_dir() else None)
    sys.stdout.write("Q2 dir read_text? ")
    try:
        say(directory.read_text(encoding="utf-8")[:40])
    except Exception as exc:
        say(type(exc).__name__, exc)

    # --- Q2b: file -> existing directory WITHOUT replace-if-exists flags --
    src2b = TMP / "src2b.json"
    src2b.write_text('{"v": 22}', encoding="utf-8")
    directory2 = TMP / "some_dir2"
    directory2.mkdir()
    h = create_handle(src2b, DELETE | FILE_READ_ATTRIBUTES,
                      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
    try:
        raw = str(directory2).encode("utf-16-le")
        buf = bytearray()
        buf += struct.pack("<I", 0)  # no flags
        buf += b"\x00" * 4
        buf += struct.pack("<Q", 0)
        buf += struct.pack("<I", len(raw))
        buf += raw
        holder = (ctypes.c_char * len(buf)).from_buffer(buf)
        ok = k32.SetFileInformationByHandle(h, FileRenameInfoEx, holder, len(buf))
        say("Q2b no-flags file->dir ok=%r err=%r" % (ok, ctypes.get_last_error()))
    finally:
        k32.CloseHandle(h)
    say("Q2b src2b exists:", src2b.exists(), "dir2 is_dir:",
        directory2.is_dir(), list(directory2.iterdir()) if directory2.is_dir() else None)

    # --- Q3 revisited: relative destination --------------------------------
    src3 = TMP / "src3.json"
    src3.write_text('{"v": 3}', encoding="utf-8")
    os.chdir(TMP)
    try:
        posix_replace(src3, Path("relative_dest.json"))
        say("Q3 rename reported OK")
    except OSError as exc:
        say("Q3 rename FAIL", exc)
    say("Q3 src3 exists:", src3.exists())
    say("Q3 TMP/relative_dest exists:", (TMP / "relative_dest.json").exists())
    for cand in (Path.cwd() / "relative_dest.json",
                 Path(os.environ.get("SystemDrive", "C:")) / "relative_dest.json"):
        say("Q3 candidate", cand, cand.exists())

    # --- Q4: absolute path with different case than existing dest --------
    src4 = TMP / "src4.json"
    src4.write_text('{"v": 4}', encoding="utf-8")
    dest4 = TMP / "CASE_dest.json"
    dest4.write_text('{"v": 0}', encoding="utf-8")
    try:
        posix_replace(src4, TMP / "case_dest.json")  # wrong case
        say("Q4 wrong-case rename reported OK; exists:", dest4.exists())
    except OSError as exc:
        say("Q4 wrong-case rename FAIL", exc)
    say("Q4 src4 exists:", src4.exists())

    say("tmp:", TMP)
