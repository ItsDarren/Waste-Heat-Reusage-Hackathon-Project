"""Bound numerical runtimes within this process; use the active Python environment."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
os.environ.setdefault('OMP_NUM_THREADS', '2')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '2')
os.environ.setdefault('MKL_NUM_THREADS', '2')

# Bound this process and its child workloads to one permitted CPU on Windows
# for stable native numerical execution. No machine-wide settings are changed.
if sys.platform == 'win32':
    import ctypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=ctypes.c_void_p
    kernel.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    process=kernel.GetCurrentProcess()
    allowed=ctypes.c_size_t(); system=ctypes.c_size_t()
    if kernel.GetProcessAffinityMask(process,ctypes.byref(allowed),ctypes.byref(system)) and allowed.value:
        kernel.SetProcessAffinityMask(process,1 << (allowed.value.bit_length()-1))
