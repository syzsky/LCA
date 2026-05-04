import logging
import os
import ctypes
from ctypes import wintypes

logger = logging.getLogger(__name__)

DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ADAPTER_FLAG_SOFTWARE = 0x00000002


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class DXGI_ADAPTER_DESC1(ctypes.Structure):
    _fields_ = [
        ("Description", wintypes.WCHAR * 128),
        ("VendorId", wintypes.UINT),
        ("DeviceId", wintypes.UINT),
        ("SubSysId", wintypes.UINT),
        ("Revision", wintypes.UINT),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", LUID),
        ("Flags", wintypes.UINT),
    ]


def _load_dxgi():
    try:
        return ctypes.windll.dxgi
    except Exception:
        return None


def _enum_dxgi_adapters():
    dxgi = _load_dxgi()
    if dxgi is None:
        return []

    try:
        import comtypes
        from comtypes import GUID, HRESULT, POINTER, COMMETHOD
        from comtypes import IUnknown
    except Exception as exc:
        logger.warning("DirectML adapter probe skipped: %s", exc)
        return []

    class IDXGIAdapter1(IUnknown):
        _iid_ = GUID("{29038f61-3839-4626-91fd-086879011a05}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetDesc1", (["out"], POINTER(DXGI_ADAPTER_DESC1), "pDesc")),
        ]

    class IDXGIFactory1(IUnknown):
        _iid_ = GUID("{770aae78-f26f-4dba-a829-253c83d1b387}")
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "EnumAdapters1",
                (["in"], wintypes.UINT, "Adapter"),
                (["out"], POINTER(POINTER(IDXGIAdapter1)), "ppAdapter"),
            ),
        ]

    create_factory = dxgi.CreateDXGIFactory1
    create_factory.restype = ctypes.c_long  # HRESULT
    create_factory.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p]

    adapters = []
    comtypes.CoInitialize()
    try:
        factory_ptr = ctypes.c_void_p()
        hr = create_factory(ctypes.byref(IDXGIFactory1._iid_), ctypes.byref(factory_ptr))
        if hr != 0 or not factory_ptr.value:
            return adapters
        factory = ctypes.cast(factory_ptr, POINTER(IDXGIFactory1))
        idx = 0
        while True:
            try:
                adapter = factory.EnumAdapters1(idx)
            except comtypes.COMError as exc:
                if exc.hresult == DXGI_ERROR_NOT_FOUND:
                    break
                logger.debug("DXGI EnumAdapters1 调用失败：%s", exc)
                break
            if not adapter:
                break
            try:
                desc = adapter.GetDesc1()
            except comtypes.COMError as exc:
                logger.debug("DXGI GetDesc1 调用失败：%s", exc)
                break
            adapters.append(
                {
                    "index": idx,
                    "description": desc.Description.strip(),
                    "vendor_id": int(desc.VendorId),
                    "device_id": int(desc.DeviceId),
                    "dedicated_video": int(desc.DedicatedVideoMemory),
                    "shared_system": int(desc.SharedSystemMemory),
                    "flags": int(desc.Flags),
                }
            )
            idx += 1
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass

    return adapters


def _select_discrete_adapter(adapters):
    if not adapters:
        return None
    discrete = [
        a
        for a in adapters
        if (a["flags"] & DXGI_ADAPTER_FLAG_SOFTWARE) == 0 and a["dedicated_video"] > 0
    ]
    if discrete:
        return max(discrete, key=lambda a: a["dedicated_video"])
    return adapters[0]


def select_dml_device_id():
    env_id = os.environ.get("LCA_DML_DEVICE_ID")
    if env_id is not None:
        try:
            device_id = int(env_id)
            return device_id, f"env:{device_id}"
        except ValueError:
            logger.warning("Invalid LCA_DML_DEVICE_ID: %s", env_id)

    adapters = _enum_dxgi_adapters()
    selected = _select_discrete_adapter(adapters)
    if not selected:
        return 0, "default"

    device_id = int(selected["index"])
    desc = selected.get("description") or f"adapter_{device_id}"
    return device_id, desc
