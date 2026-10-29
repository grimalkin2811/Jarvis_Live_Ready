"""Probe v14: does FileRenameInfoEx(POSIX) work when the destination does NOT
exist yet (the very first save_dict on a fresh path)?

Also records the error raised when replacing a file with a directory, so the
retry classifier can treat it as permanent.
"""
import ctypes
import os
import struct
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="probe14_"))

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
    # Q1: destination missing (first save on a fresh path)
    src = TMP / "src1.json"
    dest = TMP / "fresh_dest.json"
    src.write_text('{"v": 1}', encoding="utf-8")
    try:
        posix_replace(src, dest)
        say("Q1 dest missing -> POSIX rename OK, content =", dest.read_text(encoding="utf-8").strip())
    except OSError as exc:
        say("Q1 dest missing -> FAIL", exc)

    # Q2: replace a file with a directory (unwritable-target test case)
    src2 = TMP / "src2.json"
    src2.write_text('{"v": 2}', encoding="utf-8")
    directory = TMP / "some_dir"
    directory.mkdir()
    try:
        posix_replace(src2, directory)
        say("Q2 file->dir -> UNEXPECTED OK")
    except OSError as exc:
        say("Q2 file->dir -> errno=%r winerror=%r str=%s"
            % (getattr(exc, "errno", None), getattr(exc, "winerror", None), exc))
    say("Q2 dir still dir:", directory.is_dir())

    # Q3: relative path destination
    src3 = TMP / "src3.json"
    src3.write_text('{"v": 3}', encoding="utf-8")
    os.chdir(TMP)
    try:
        posix_replace(src3, Path("relative_dest.json"))
        say("Q3 relative dest -> OK, exists =", (TMP / "relative_dest.json").exists())
    except OSError as exc:
        say("Q3 relative dest -> FAIL", exc)

    say("tmp:", TMP)
