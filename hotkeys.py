"""DOKSURI 全局快捷键 - 跨平台实现（应用在后台也能响应）。

- Windows：RegisterHotKey(None, id, ...) 把 WM_HOTKEY 消息投递到当前线程的
  消息队列，Qt 事件循环取出消息时先经过 QAbstractNativeEventFilter，
  因此无需子类化原生窗口即可实现系统级快捷键。
- macOS：Carbon RegisterEventHotKey 注册系统级热键（无需辅助功能权限），
  热键事件经 EventHandler C 回调分发；Qt 事件循环运行在主线程，
  回调同样在主线程执行，可直接操作界面控件。

键位采用各平台标准按键：Windows 为 Ctrl+T / Ctrl+Shift+O，
macOS 为 Command+T / Command+Shift+O（ACCEL_MOD 自动切换修饰键）。
"""
import ctypes
import sys

from PyQt6.QtCore import QAbstractNativeEventFilter

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# 平台无关的修饰键/键值 API（数值沿用 Win32 约定）
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_CMD = 0x0008        # macOS Command 键（Windows 下无对应语义，勿在 win 使用）
MOD_NOREPEAT = 0x4000   # 仅 Windows 生效（按住不重复触发）

VK_T = 0x54
VK_O = 0x4F

# 平台标准加速键修饰符：Windows 用 Ctrl，macOS 用 Command（main.py 引用）
ACCEL_MOD = MOD_CMD if IS_MAC else MOD_CONTROL

HK_QUICK_TRANSLATE = 1     # 快速翻译：Cmd+T（macOS）/ Ctrl+T（Windows）
HK_QUICK_SCREENSHOT = 2    # 截图翻译：Cmd+Shift+O（macOS）/ Ctrl+Shift+O（Windows）

if IS_WIN:
    from ctypes import wintypes
    WM_HOTKEY = 0x0312

# ---------------------------------------------------------------- macOS

# Carbon 虚拟键码（kVK_ANSI_*）：Windows VK 码 → macOS 键码
_MAC_KEYCODES = {
    0x41: 0x00,  # A
    0x53: 0x01,  # S
    0x44: 0x02,  # D
    0x46: 0x03,  # F
    0x48: 0x04,  # H
    0x47: 0x05,  # G
    0x5A: 0x06,  # Z
    0x58: 0x07,  # X
    0x43: 0x08,  # C
    0x56: 0x09,  # V
    0x42: 0x0B,  # B
    0x51: 0x0C,  # Q
    0x57: 0x0D,  # W
    0x45: 0x0E,  # E
    0x52: 0x0F,  # R
    0x59: 0x10,  # Y
    0x54: 0x11,  # T
    0x4F: 0x1F,  # O
    0x55: 0x20,  # U
    0x49: 0x22,  # I
    0x50: 0x23,  # P
    0x4C: 0x25,  # L
    0x4A: 0x26,  # J
    0x4B: 0x28,  # K
    0x4E: 0x2D,  # N
    0x4D: 0x2E,  # M
}

# Carbon 修饰键掩码（Events.h：cmd/shift/option/control）
_MAC_MOD_SHIFT = 0x0200
_MAC_MOD_OPTION = 0x0800
_MAC_MOD_CONTROL = 0x1000

_carbon = None           # Carbon 上下文（macOS 惰性初始化，进程内一次）
_active_instance = None  # 当前 GlobalHotkeys 实例（Carbon C 回调据此分发）


def _mac_init():
    """加载 Carbon 框架并安装热键事件处理器（仅 macOS）。

    返回上下文 dict：carbon 句柄、UPP（防 GC）、事件处理器引用、
    EventHotKeyID 结构体类。任一步失败抛异常由调用方兜底。
    """
    class EventHotKeyID(ctypes.Structure):
        _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]

    class EventTypeSpec(ctypes.Structure):
        _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]

    carbon = ctypes.CDLL(
        "/System/Library/Frameworks/Carbon.framework/Versions/Current/Carbon")
    EventHandlerUPP = ctypes.CFUNCTYPE(
        ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p)

    carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
    carbon.GetApplicationEventTarget.argtypes = []
    carbon.RegisterEventHotKey.restype = ctypes.c_int32
    carbon.RegisterEventHotKey.argtypes = [
        ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
    ]
    carbon.UnregisterEventHotKey.restype = ctypes.c_int32
    carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
    carbon.InstallEventHandler.restype = ctypes.c_int32
    carbon.InstallEventHandler.argtypes = [
        ctypes.c_void_p, EventHandlerUPP, ctypes.c_uint32,
        ctypes.POINTER(EventTypeSpec), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    carbon.GetEventParameter.restype = ctypes.c_int32
    carbon.GetEventParameter.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
    ]

    kEventClassKeyboard = 0x6B657962          # 'keyb'
    kEventHotKeyPressed = 6
    kEventParamDirectObject = 0x2D2D2D2D      # '----'
    typeEventHotKeyID = 0x686B6964            # 'hkid'

    def handler(_call_ref, event):
        try:
            hkid = EventHotKeyID()
            carbon.GetEventParameter(
                event, kEventParamDirectObject, typeEventHotKeyID, None,
                ctypes.sizeof(hkid), None, ctypes.byref(hkid))
            inst = _active_instance
            cb = inst._bindings.get(int(hkid.id)) if inst else None
            if cb is not None:
                cb()
        except Exception:
            pass
        return 0

    upp = EventHandlerUPP(handler)
    spec = EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)
    handler_ref = ctypes.c_void_p()
    carbon.InstallEventHandler(
        carbon.GetApplicationEventTarget(), upp, 1,
        ctypes.byref(spec), None, ctypes.byref(handler_ref))

    return {
        "carbon": carbon,
        "upp": upp,             # 引用必须保活，否则回调被回收会崩溃
        "handler_ref": handler_ref,
        "HotKeyID": EventHotKeyID,
    }


class GlobalHotkeys(QAbstractNativeEventFilter):
    """注册/分发系统级快捷键。

    用法：app.installNativeEventFilter(hotkeys) 后再逐个 add()。
    Windows 经 nativeEventFilter 分发；macOS 由 Carbon 事件处理器分发
    （native filter 在非 Windows 平台空转，仅为保持调用方式一致）。
    """

    def __init__(self):
        super().__init__()
        self._bindings = {}     # id -> callback
        self._registered = {}   # id -> bool
        self._hotkey_refs = {}  # id -> EventHotKeyRef（macOS）

    def add(self, hotkey_id, modifiers, vk, callback):
        """注册快捷键，返回是否成功（失败通常是被其他程序占用）。"""
        self._bindings[hotkey_id] = callback
        if IS_WIN:
            ok = bool(
                ctypes.windll.user32.RegisterHotKey(
                    None, hotkey_id, modifiers | MOD_NOREPEAT, vk
                )
            )
        elif IS_MAC:
            ok = self._mac_register(hotkey_id, modifiers, vk)
        else:
            ok = False
        self._registered[hotkey_id] = ok
        return ok

    def _mac_register(self, hotkey_id, modifiers, vk):
        global _carbon, _active_instance
        try:
            if _carbon is None:
                _carbon = _mac_init()
            keycode = _MAC_KEYCODES.get(vk)
            if keycode is None:
                return False
            mods = 0
            if modifiers & MOD_CMD:
                mods |= _MAC_MOD_CMD
            if modifiers & MOD_CONTROL:
                mods |= _MAC_MOD_CONTROL
            if modifiers & MOD_SHIFT:
                mods |= _MAC_MOD_SHIFT
            if modifiers & MOD_ALT:
                mods |= _MAC_MOD_OPTION
            _active_instance = self
            ref = ctypes.c_void_p()
            hkid = _carbon["HotKeyID"](signature=0x444F4B53, id=hotkey_id)  # 'DOKS'
            status = _carbon["carbon"].RegisterEventHotKey(
                keycode, mods, hkid,
                _carbon["carbon"].GetApplicationEventTarget(), 0,
                ctypes.byref(ref),
            )
            if status != 0:
                return False
            self._hotkey_refs[hotkey_id] = ref
            return True
        except Exception:
            return False

    def is_registered(self, hotkey_id):
        return self._registered.get(hotkey_id, False)

    def unregister_all(self):
        if IS_WIN:
            for hid in list(self._bindings):
                ctypes.windll.user32.UnregisterHotKey(None, hid)
        elif IS_MAC and _carbon is not None:
            for ref in self._hotkey_refs.values():
                try:
                    _carbon["carbon"].UnregisterEventHotKey(ref)
                except Exception:
                    pass
            self._hotkey_refs.clear()
        self._bindings.clear()
        self._registered.clear()

    def nativeEventFilter(self, eventType, message):
        # PyQt6 要求返回 (是否拦截, 结果指针) 元组；第二元素为 None 会崩溃，必须用 0
        if IS_WIN and eventType == b"windows_generic_MSG":
            if not message:
                return False, 0
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                callback = self._bindings.get(int(msg.wParam))
                if callback is not None:
                    callback()
                    return True, 0
        return False, 0
