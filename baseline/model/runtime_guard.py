"""Bounded model-process execution settings; no machine-wide settings are changed."""
def configure():
    import gc,os
    gc.disable()  # Model inputs/results are acyclic; reference counting frees them.
    if os.name=='nt':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        handle=kernel.GetCurrentProcess()
        kernel.GetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
        kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        allowed=ctypes.c_size_t();system=ctypes.c_size_t()
        if kernel.GetProcessAffinityMask(handle,ctypes.byref(allowed),ctypes.byref(system)) and allowed.value:
            chosen=1<<(allowed.value.bit_length()-1)
            if not kernel.SetProcessAffinityMask(handle,chosen):raise ctypes.WinError(ctypes.get_last_error())
