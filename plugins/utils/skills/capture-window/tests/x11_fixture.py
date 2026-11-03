"""Owned X11 test overlays via ctypes and the system Xlib, no Python packages."""
import ctypes as C
import os
import signal
import sys
import time

x = C.CDLL("libX11.so.6")
Display = C.c_void_p
Window = C.c_ulong
x.XOpenDisplay.argtypes = [C.c_char_p]; x.XOpenDisplay.restype = Display
x.XDefaultRootWindow.argtypes = [Display]; x.XDefaultRootWindow.restype = Window
x.XCreateWindow.argtypes = [Display, Window, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_uint, C.c_int, C.c_uint, C.c_void_p, C.c_ulong, C.c_void_p]
x.XCreateWindow.restype = Window
x.XMapWindow.argtypes = [Display, Window]
x.XRaiseWindow.argtypes = [Display, Window]
x.XChangeWindowAttributes.argtypes = [Display, Window, C.c_ulong, C.c_void_p]
x.XFlush.argtypes = [Display]
x.XCloseDisplay.argtypes = [Display]
x.XSetTransientForHint.argtypes = [Display, Window, Window]
display = x.XOpenDisplay(None)
if not display:
    raise SystemExit("Owned display unavailable")
root = x.XDefaultRootWindow(display)
input_only = sys.argv[1] == "input"
wid = x.XCreateWindow(display, root, int(sys.argv[2]), int(sys.argv[3]), 60, 40, 0, 0, 2 if input_only else 1, None, 0, None)
class Attributes(C.Structure):
    _fields_ = [("background_pixmap", C.c_ulong), ("background_pixel", C.c_ulong), ("border_pixmap", C.c_ulong), ("border_pixel", C.c_ulong),
               ("bit_gravity", C.c_int), ("win_gravity", C.c_int), ("backing_store", C.c_int), ("backing_planes", C.c_ulong),
               ("backing_pixel", C.c_ulong), ("save_under", C.c_int), ("event_mask", C.c_long), ("do_not_propagate_mask", C.c_long),
               ("override_redirect", C.c_int), ("colormap", C.c_ulong), ("cursor", C.c_ulong)]
attrs = Attributes(); attrs.override_redirect = 1
x.XChangeWindowAttributes(display, wid, 1 << 9, C.byref(attrs))
if not input_only and len(sys.argv) > 4:
    x.XSetTransientForHint(display, wid, int(sys.argv[4], 0))
x.XMapWindow(display, wid); x.XRaiseWindow(display, wid); x.XFlush(display)
print(wid, flush=True)
try:
    while True:
        time.sleep(0.1)
finally:
    x.XCloseDisplay(display)
