"""Probe v13: can a POSIX-semantics rename replace a destination whose reader
denies FILE_SHARE_DELETE ?

Cases:
  A: destination open with FILE_SHARE_READ only  (reader denies delete)
  B: destination open exclusively                 (no sharing at all)
  C: destination open with FILE_SHARE_DELETE      (control, expected OK)

For each case we try, in order:
  1. SetFileInformationByHandle(FileRenameInfoEx, REPLACE_IF_EXISTS|POSIX)
  2. os.replace  (MoveFileExW fallback)

and report whether the destination stayed present + readable afterwards.
"""
import ctypes
import msvcrt
import os
import struct
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="probe13_"))

GENERIC_READ = 0x80000000
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


def win_err():
    return ctypes.WinError(ctypes.get_last_error())


def create_handle(path, access, share):
    h = k32.CreateFileW(str(path), access, share, None, OPEN_EXISTING,
                        FILE_ATTRIBUTE_NORMAL, None)
    if not h or h == INVALID:
        raise win_err()
    return h


def _rename_buf(dst, flags):
    raw = str(dst).encode("utf-16-le")
    buf = bytearray()
    buf += struct.pack("<I", flags)
    buf += b"\x00" * 4
    buf += struct.pack("<Q", 0)
    buf += struct.pack("<I", len(raw))
    buf += raw
    return buf


def posix_replace(src, dst):
    h = create_handle(src, DELETE | FILE_READ_ATTRIBUTES,
                      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
    try:
        buf = _rename_buf(dst, FLAG_REPLACE_IF_EXISTS | FLAG_POSIX_SEMANTICS)
        holder = (ctypes.c_char * len(buf)).from_buffer(buf)
        if not k32.SetFileInformationByHandle(h, FileRenameInfoEx, holder, len(buf)):
            raise win_err()
    finally:
        k32.CloseHandle(h)


def case(label, share):
    dest = TMP / f"dest_{label}.json"
    src = TMP / f"src_{label}.json"
    dest.write_text('{"v": "old"}', encoding="utf-8")
    handle = create_handle(dest, GENERIC_READ, share)
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    fh = os.fdopen(fd, "r", encoding="utf-8")

    src.write_text('{"v": "new"}', encoding="utf-8")
    try:
        posix_replace(src, dest)
        say(f"{label}: POSIX rename OK")
    except OSError as exc:
        say(f"{label}: POSIX rename FAIL -> {exc}")
        try:
            os.replace(src, dest)
            say(f"{label}: os.replace OK")
        except OSError as exc2:
            say(f"{label}: os.replace FAIL -> {exc2}")

    # Snapshot lecteur + fermeture AVANT l'état destination : dans le cas B le
    # handle est exclusif (share=0) et bloquerait un nouveau open() sur dest.
    reader_view = fh.read()
    fh.close()
    say(f"{label}: dest exists={dest.exists()} content="
        f"{dest.read_text(encoding='utf-8').strip() if dest.exists() else '<gone>'}")
    say(f"{label}: reader still sees {reader_view!r}")
    for p in (dest, src):
        if p.exists():
            p.unlink()


if __name__ == "__main__":
    say("== rename over a destination with a restrictive reader ==")
    case("A_deny_delete", FILE_SHARE_READ)
    case("B_exclusive", 0)
    case("C_allow_delete", FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
    say("tmp:", TMP)
