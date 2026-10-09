# -*- coding: utf-8 -*-
"""
=============================================================================
 JIANYING / PUNCTUATION PROTOCOL
 剪映文稿识别字幕 · 句读过滤控制器
 SWISS INTERNATIONAL TYPOGRAPHIC STYLE (TOGGLE EDITION)
=============================================================================
"""

import os
import sys
import shutil
import ctypes
from ctypes import wintypes
from datetime import datetime
import threading
import time
import json
import base64
import subprocess
import tkinter as tk
from tkinter import messagebox
import re

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

VERSION = "1.0.3"
AUTH_URL = "https://raw.githubusercontent.com/Kirota233/JianYing-Period-Keeper/master/auth.json"
API_AUTH_URL = "https://api.github.com/repos/Kirota233/JianYing-Period-Keeper/contents/auth.json?ref=master"

# 启用 Windows 高 DPI 矢量缩放
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    import psutil
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "psutil"])
    import psutil

# ---------------------------------------------------------------------------
# 系统底层与内存定义
# ---------------------------------------------------------------------------
kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
psapi = ctypes.windll.psapi
PROCESS_ALL = 0x1F0FFF
PAGE_EXECUTE_READWRITE = 0x40

psapi.EnumProcessModules.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
psapi.EnumProcessModules.restype = wintypes.BOOL

psapi.GetModuleBaseNameW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
psapi.GetModuleBaseNameW.restype = wintypes.DWORD

class MODULEINFO(ctypes.Structure):
    _fields_ = [
        ('lpBaseOfDll', ctypes.c_void_p),
        ('SizeOfImage', wintypes.DWORD),
        ('EntryPoint', ctypes.c_void_p)
    ]

psapi.GetModuleInformation.argtypes = [wintypes.HANDLE, wintypes.HMODULE, ctypes.POINTER(MODULEINFO), wintypes.DWORD]
psapi.GetModuleInformation.restype = wintypes.BOOL

# 剪映特征字符集
# 1. UTF-16 标点集合 (在 head_script_revision_result 中处理字幕的断句标点集合)
# 原始：。！？!?；;，,、.\n\r (包含句号与逗号)
ORIG_U16_PUNCT = b'\x020\x01\xff\x1f\xff!\x00?\x00\x1b\xff;\x00\x0c\xff,\x00\x010.\x00\n\x00\r\x00'
# 补丁：将 。(U+3002)、.(U+002E)、，(U+FF0C)、,(U+002C) 替换为已存在的感叹号占位
# 严格保持 26 字节长度，不使用 \x00 提前截断，确保末尾换行符 \n \r 仍能被底层正常清洗规整，避免句号单独分行
PATCH_U16_PUNCT = (
    b'\x01\xff'   # 0: ！ (原为 。)
    b'\x01\xff'   # 1: ！
    b'\x1f\xff'   # 2: ？
    b'!\x00'      # 3: !
    b'?\x00'      # 4: ?
    b'\x1b\xff'   # 5: ；
    b';\x00'      # 6: ;
    b'\x01\xff'   # 7: ！ (原为 ，)
    b'!\x00'      # 8: !  (原为 ,)
    b'\x010'      # 9: 、
    b'!\x00'      # 10: ! (原为 .)
    b'\n\x00'     # 11: \n
    b'\r\x00'     # 12: \r
)

# 2. UTF-8 备用正则特征
ORIG_U8_REGEX = b'[\xe3\x80\x82\xef\xbc\x9f\xef\xbc\x81\xef\xbc\x9b\xe2\x80\x9c .!?\xef\xbd\x9e]\x00'
PATCH_U8_REGEX = b'[\xef\xbc\x9f\xef\xbc\x81\xef\xbc\x9b\xe2\x80\x9c !?\xef\xbd\x9e]\x00\x00\x00\x00\x00'


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('BaseAddress', ctypes.c_void_p),
        ('AllocationBase', ctypes.c_void_p),
        ('AllocationProtect', wintypes.DWORD),
        ('PartitionId', wintypes.WORD),
        ('RegionSize', ctypes.c_size_t),
        ('State', wintypes.DWORD),
        ('Protect', wintypes.DWORD),
        ('Type', wintypes.DWORD)
    ]


# ---------------------------------------------------------------------------
# 核心维护：启动自检与安全恢复 (彻底杜绝应用无法启动)
# ---------------------------------------------------------------------------
def ensure_vecreator_integrity():
    search_dirs = [
        r"D:\JianyingPro",
        r"C:\Program Files\JianyingPro",
        os.path.expandvars(r"%LOCALAPPDATA%\JianyingPro\Apps"),
        os.path.expandvars(r"%LOCALAPPDATA%\JianyingPro"),
    ]
    for sdir in search_dirs:
        if os.path.exists(sdir):
            for root, dirs, files in os.walk(sdir):
                if "VECreator.dll" in files and "VECreator.dll.bak" in files:
                    dll_p = os.path.join(root, "VECreator.dll")
                    bak_p = os.path.join(root, "VECreator.dll.bak")
                    try:
                        if os.path.getsize(dll_p) != os.path.getsize(bak_p):
                            shutil.copyfile(bak_p, dll_p)
                    except Exception:
                        pass


ensure_vecreator_integrity()


# ---------------------------------------------------------------------------
# 进程与内存状态嗅探
# ---------------------------------------------------------------------------
def get_jianying_runtime():
    """
    返回: (status_code, pids, has_editor)
    status_code: 'OFFLINE' | 'STANDBY' | 'READY'
    严格匹配 jianyingpro.exe，排除工具自身与后台托盘
    """
    j_pids = []
    editor_pids = []
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = (proc.info['name'] or '').lower()
            if name == 'jianyingpro.exe':
                pid = proc.info['pid']
                j_pids.append(pid)
                try:
                    for m in proc.memory_maps(grouped=False):
                        if 'vecreator.dll' in m.path.lower():
                            editor_pids.append(pid)
                            break
                except Exception:
                    pass
        except Exception:
            continue

    if not j_pids:
        return 'OFFLINE', [], False
    if editor_pids:
        return 'READY', editor_pids, True
    return 'STANDBY', j_pids, False


# ---------------------------------------------------------------------------
# 内存热补丁注入核心
# ---------------------------------------------------------------------------
def patch_process_memory(pid, apply_patch=True):
    h = kernel32.OpenProcess(PROCESS_ALL, False, pid)
    if not h:
        return False

    modified = False
    try:
        h_mods = (wintypes.HMODULE * 1024)()
        cb_needed = wintypes.DWORD()
        if psapi.EnumProcessModules(h, ctypes.cast(h_mods, ctypes.c_void_p), ctypes.sizeof(h_mods), ctypes.byref(cb_needed)):
            n_mods = cb_needed.value // ctypes.sizeof(wintypes.HMODULE)
            for i in range(n_mods):
                mod_name = ctypes.create_unicode_buffer(260)
                psapi.GetModuleBaseNameW(h, h_mods[i], mod_name, 260)
                if mod_name.value.lower() != 'vecreator.dll':
                    continue

                mod_info = MODULEINFO()
                if psapi.GetModuleInformation(h, h_mods[i], ctypes.byref(mod_info), ctypes.sizeof(mod_info)):
                    base_addr = mod_info.lpBaseOfDll
                    size_of_image = mod_info.SizeOfImage

                    curr = base_addr
                    end = base_addr + size_of_image
                    mbi = MEMORY_BASIC_INFORMATION()

                while curr < end:
                    if kernel32.VirtualQueryEx(h, ctypes.c_void_p(curr), ctypes.byref(mbi), ctypes.sizeof(mbi)) == 0:
                        break

                    if mbi.State == 0x1000 and (mbi.Protect & 0xEE) and not (mbi.Protect & 0x100):
                        buf = (ctypes.c_char * mbi.RegionSize)()
                        read_bytes = ctypes.c_size_t()
                        if kernel32.ReadProcessMemory(h, ctypes.c_void_p(curr), ctypes.byref(buf), mbi.RegionSize, ctypes.byref(read_bytes)):
                            data = bytes(buf)[:read_bytes.value]

                            # 1. 针对 UTF-16 标点集合
                            target_src = ORIG_U16_PUNCT if apply_patch else PATCH_U16_PUNCT
                            target_dst = PATCH_U16_PUNCT if apply_patch else ORIG_U16_PUNCT
                            p = data.find(target_src)
                            if p != -1:
                                target_va = curr + p
                                old_prot = wintypes.DWORD()
                                kernel32.VirtualProtectEx(h, ctypes.c_void_p(target_va), len(target_dst), PAGE_EXECUTE_READWRITE, ctypes.byref(old_prot))
                                written = ctypes.c_size_t()
                                kernel32.WriteProcessMemory(h, ctypes.c_void_p(target_va), target_dst, len(target_dst), ctypes.byref(written))
                                kernel32.VirtualProtectEx(h, ctypes.c_void_p(target_va), len(target_dst), old_prot.value, ctypes.byref(old_prot))
                                modified = True

                            # 2. 针对 UTF-8 正则表
                            r_src = ORIG_U8_REGEX if apply_patch else PATCH_U8_REGEX
                            r_dst = PATCH_U8_REGEX if apply_patch else ORIG_U8_REGEX
                            p2 = data.find(r_src)
                            if p2 != -1:
                                target_va2 = curr + p2
                                old_prot2 = wintypes.DWORD()
                                kernel32.VirtualProtectEx(h, ctypes.c_void_p(target_va2), len(r_dst), PAGE_EXECUTE_READWRITE, ctypes.byref(old_prot2))
                                written2 = ctypes.c_size_t()
                                kernel32.WriteProcessMemory(h, ctypes.c_void_p(target_va2), r_dst, len(r_dst), ctypes.byref(written2))
                                kernel32.VirtualProtectEx(h, ctypes.c_void_p(target_va2), len(r_dst), old_prot2.value, ctypes.byref(old_prot2))
                                modified = True
                    curr += mbi.RegionSize
                break
    finally:
        kernel32.CloseHandle(h)
    return modified


# ---------------------------------------------------------------------------
# Win32 原生剪贴板底层 API (支持跨线程安全读写与重试机制)
# ---------------------------------------------------------------------------
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


def win_get_clipboard_text():
    for _ in range(5):
        if user32.OpenClipboard(None):
            try:
                if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    return None
                h_data = user32.GetClipboardData(CF_UNICODETEXT)
                if not h_data:
                    return None
                p_data = kernel32.GlobalLock(h_data)
                if not p_data:
                    return None
                try:
                    return ctypes.wstring_at(p_data)
                finally:
                    kernel32.GlobalUnlock(h_data)
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return None


def win_set_clipboard_text(text):
    data = text.encode('utf-16-le') + b'\x00\x00'
    for _ in range(5):
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
                h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h_mem:
                    return False
                p_mem = kernel32.GlobalLock(h_mem)
                if not p_mem:
                    kernel32.GlobalFree(h_mem)
                    return False
                ctypes.memmove(p_mem, data, len(data))
                kernel32.GlobalUnlock(h_mem)
                user32.SetClipboardData(CF_UNICODETEXT, h_mem)
                return True
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
# 剪贴板实时句读保护器 (基于 Unicode CL 属性避头尾禁则与排版绑定)
# ---------------------------------------------------------------------------
class ClipboardGuard:
    def needs_protection(self, text: str) -> bool:
        if not text:
            return False
        return any(c in text for c in ('。', '.', '，', ','))

    def needs_restoration(self, text: str) -> bool:
        if not text:
            return False
        return any(c in text for c in ('﹒', '﹐', '․', '‚', '\u2060'))

    def protect_text(self, text: str) -> str:
        if not text:
            return ''
        # 1. 彻底清除旧版本遗留的零宽连接符与非标标点，杜绝任何历史残留
        text = text.replace('\u2060', '')
        text = text.replace('․', '.').replace('‚', ',')

        # 2. 剥离标点前异常的多余空白符，并映射为 Unicode CL (Close Punctuation) 类标点
        # ﹒(U+FE52 Small Full Stop) 与 ﹐(U+FE50 Small Comma) 具有 CL 闭合标点属性：
        # (1) 不使用任何 Cf 零宽控制符，杜绝底层 ASR 语音模型切词导致的句号前多出空格问题；
        # (2) 在 Qt/剪映文字排版引擎中天然继承 UAX #14 [^\s] × CL 禁则，
        #     绝对禁止在标点前单独换行，彻底消除末尾句号落单成行的排版缺陷。
        text = re.sub(r'[ \t\u3000]*[。.\ufe52]', '﹒', text)
        text = re.sub(r'[ \t\u3000]*[，,\ufe50]', '﹐', text)
        return text

    def restore_text(self, text: str) -> str:
        if not text:
            return ''
        # 1. 连续 2 个及以上的 ﹒ 还原为标准英文连续省略号 ...
        text = re.sub(r'﹒{2,}', lambda m: '.' * len(m.group(0)), text)

        # 2. 上下文感知自适应还原：
        # 处于西文/数字语境时还原为半角 ASCII 标点，处于中文语境时还原为全角标点
        def repl_dot(m):
            idx = m.start()
            start = max(0, idx - 15)
            end = min(len(text), idx + 15)
            window = text[start:end]
            if any('\u4e00' <= c <= '\u9fff' for c in window):
                return '。'
            return '.'

        def repl_comma(m):
            idx = m.start()
            start = max(0, idx - 15)
            end = min(len(text), idx + 15)
            window = text[start:end]
            if any('\u4e00' <= c <= '\u9fff' for c in window):
                return '，'
            return ','

        text = re.sub(r'﹒', repl_dot, text)
        text = re.sub(r'﹐', repl_comma, text)

        # 兼容清理旧版残留
        text = text.replace('․', '.').replace('‚', ',').replace('\u2060', '')
        return text


guard = ClipboardGuard()


# ---------------------------------------------------------------------------
# 瑞士极简设计视觉规范 (Swiss Style)
# ---------------------------------------------------------------------------
class SwissDesign:
    BG = "#0D0E12"             # 极深炭黑底色 (Obsidian)
    SURFACE = "#15171E"        # 模块底色
    SURFACE_HOVER = "#1E222D"
    TEXT_HERO = "#FFFFFF"      # 纯白
    TEXT_MUTED = "#7A8294"     # 中灰
    LINE = "#222733"           # 结构线
    ACCENT_RED = "#E30613"     # 瑞士红 (PANTONE 485 C)
    STATUS_GREEN = "#00D664"   # 就绪绿
    STATUS_AMBER = "#FFB800"   # 等待黄

    FONT_TITLE = ("Segoe UI", 15, "bold")
    FONT_LABEL = ("Segoe UI", 8, "bold")
    FONT_CARD_TITLE = ("Microsoft YaHei UI", 11, "bold")
    FONT_CARD_SUB = ("Microsoft YaHei UI", 9)
    FONT_BTN = ("Microsoft YaHei UI", 10, "bold")
    FONT_BODY = ("Microsoft YaHei UI", 9)
    FONT_MONO = ("Consolas", 8)


# ---------------------------------------------------------------------------
# 主应用窗口 (极简瑞士风格 + 拨动开关 + 实时持续守护)
# ---------------------------------------------------------------------------
class MinimalApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"JIANYING / PUNCTUATION v{VERSION}")
        self.geometry("480x390")
        self.resizable(False, False)
        self.configure(bg=SwissDesign.BG)

        self.is_active = False

        self.build_ui()
        self.start_monitor_thread()
        self.start_sentinel_thread()
        threading.Thread(target=self._check_auth, daemon=True).start()

    def build_ui(self):
        # 1. 顶部 Header
        top = tk.Frame(self, bg=SwissDesign.BG)
        top.pack(fill="x", padx=28, pady=(24, 14))

        title_box = tk.Frame(top, bg=SwissDesign.BG)
        title_box.pack(side="left", anchor="w")

        tk.Label(
            title_box,
            text=f"JIANYING PROTOCOL v{VERSION}",
            font=SwissDesign.FONT_LABEL,
            fg=SwissDesign.ACCENT_RED,
            bg=SwissDesign.BG
        ).pack(anchor="w")

        tk.Label(
            title_box,
            text="句读过滤控制器",
            font=SwissDesign.FONT_TITLE,
            fg=SwissDesign.TEXT_HERO,
            bg=SwissDesign.BG
        ).pack(anchor="w", pady=(1, 0))

        # 说明按钮（高亮醒目背景）
        btn_info = tk.Button(
            top,
            text="说明 / INFO",
            bg="#FFFFFF",
            fg="#0D0E12",
            activebackground="#E5E7EB",
            activeforeground="#000000",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self.show_info_modal
        )
        btn_info.pack(side="right", anchor="center")

        # 结构发丝线
        tk.Frame(self, height=1, bg=SwissDesign.LINE).pack(fill="x", padx=28, pady=(0, 14))

        # 2. 状态看板
        status_box = tk.Frame(self, bg=SwissDesign.SURFACE, highlightbackground=SwissDesign.LINE, highlightthickness=1)
        status_box.pack(fill="x", padx=28, pady=(0, 16), ipady=10)

        # 状态行 1: 剪映引擎
        row1 = tk.Frame(status_box, bg=SwissDesign.SURFACE)
        row1.pack(fill="x", padx=16, pady=(0, 6))

        tk.Label(
            row1,
            text="CORE ENGINE",
            font=SwissDesign.FONT_LABEL,
            fg=SwissDesign.TEXT_MUTED,
            bg=SwissDesign.SURFACE
        ).pack(side="left")

        self.lbl_engine_val = tk.Label(
            row1,
            text="SEARCHING / 探测中...",
            font=SwissDesign.FONT_MONO,
            fg=SwissDesign.TEXT_MUTED,
            bg=SwissDesign.SURFACE
        )
        self.lbl_engine_val.pack(side="right")

        # 状态行 2: 当前过滤状态
        row2 = tk.Frame(status_box, bg=SwissDesign.SURFACE)
        row2.pack(fill="x", padx=16)

        tk.Label(
            row2,
            text="FILTER STATE",
            font=SwissDesign.FONT_LABEL,
            fg=SwissDesign.TEXT_MUTED,
            bg=SwissDesign.SURFACE
        ).pack(side="left")

        self.lbl_mode_val = tk.Label(
            row2,
            text="DEFAULT / 官方过滤模式",
            font=SwissDesign.FONT_MONO,
            fg=SwissDesign.TEXT_MUTED,
            bg=SwissDesign.SURFACE
        )
        self.lbl_mode_val.pack(side="right")

        # 3. 拨动开关控制卡片 (Toggle Switch Card)
        self.card = tk.Frame(
            self,
            bg=SwissDesign.SURFACE,
            highlightbackground=SwissDesign.LINE,
            highlightthickness=1,
            cursor="hand2"
        )
        self.card.pack(fill="x", padx=28, pady=(0, 16), ipady=14)
        self.card.bind("<Button-1>", lambda e: self.toggle_switch())

        # 卡片左侧文字
        card_text_frame = tk.Frame(self.card, bg=SwissDesign.SURFACE)
        card_text_frame.pack(side="left", padx=(18, 0))
        card_text_frame.bind("<Button-1>", lambda e: self.toggle_switch())

        self.lbl_card_title = tk.Label(
            card_text_frame,
            text="文稿识别保留中英文句号与逗号",
            font=SwissDesign.FONT_CARD_TITLE,
            fg=SwissDesign.TEXT_HERO,
            bg=SwissDesign.SURFACE
        )
        self.lbl_card_title.pack(anchor="w")
        self.lbl_card_title.bind("<Button-1>", lambda e: self.toggle_switch())

        self.lbl_card_sub = tk.Label(
            card_text_frame,
            text="默认过滤中 · 官方省略句号逗号",
            font=SwissDesign.FONT_CARD_SUB,
            fg=SwissDesign.TEXT_MUTED,
            bg=SwissDesign.SURFACE
        )
        self.lbl_card_sub.pack(anchor="w", pady=(3, 0))
        self.lbl_card_sub.bind("<Button-1>", lambda e: self.toggle_switch())

        # 卡片右侧拨动开关
        toggle_box = tk.Frame(self.card, bg=SwissDesign.SURFACE)
        toggle_box.pack(side="right", padx=(0, 18))
        toggle_box.bind("<Button-1>", lambda e: self.toggle_switch())

        self.canvas_toggle = tk.Canvas(
            toggle_box,
            width=58,
            height=30,
            bg=SwissDesign.SURFACE,
            highlightthickness=0,
            cursor="hand2"
        )
        self.canvas_toggle.pack(side="right")
        self.canvas_toggle.bind("<Button-1>", lambda e: self.toggle_switch())

        self.lbl_toggle_state = tk.Label(
            toggle_box,
            text="OFF",
            font=("Segoe UI", 10, "bold"),
            fg=SwissDesign.TEXT_MUTED,
            bg=SwissDesign.SURFACE
        )
        self.lbl_toggle_state.pack(side="right", padx=(0, 10))
        self.lbl_toggle_state.bind("<Button-1>", lambda e: self.toggle_switch())

        self.draw_toggle()

        # 4. 底部状态行
        tk.Frame(self, height=1, bg=SwissDesign.LINE).pack(fill="x", padx=28, pady=(4, 8))

        self.lbl_bottom = tk.Label(
            self,
            text="SYSTEM: 就绪 · 点击上方开关即可切换状态",
            font=SwissDesign.FONT_MONO,
            fg=SwissDesign.TEXT_MUTED,
            bg=SwissDesign.BG
        )
        self.lbl_bottom.pack(anchor="w", padx=28)

    # -----------------------------------------------------------------------
    # 拨动开关渲染与交互
    # -----------------------------------------------------------------------
    def draw_toggle(self):
        c = self.canvas_toggle
        c.delete("all")
        if self.is_active:
            # 开启状态：高饱和瑞士红槽 + 纯白滑块 (右侧)
            c.create_oval(2, 2, 28, 28, fill=SwissDesign.ACCENT_RED, outline='')
            c.create_oval(30, 2, 56, 28, fill=SwissDesign.ACCENT_RED, outline='')
            c.create_rectangle(15, 2, 43, 28, fill=SwissDesign.ACCENT_RED, outline='')
            c.create_oval(31, 4, 53, 26, fill="#FFFFFF", outline='')
            self.lbl_toggle_state.config(text="ON", fg=SwissDesign.ACCENT_RED)
            self.lbl_card_sub.config(text="已保留句逗 · 文稿识别不吃标点", fg=SwissDesign.STATUS_GREEN)
            self.lbl_mode_val.config(text="● ACTIVE / 已保留句读", fg=SwissDesign.ACCENT_RED)
        else:
            # 关闭状态：深色槽 + 白色滑块 (左侧)
            c.create_oval(2, 2, 28, 28, fill="#2B303C", outline='')
            c.create_oval(30, 2, 56, 28, fill="#2B303C", outline='')
            c.create_rectangle(15, 2, 43, 28, fill="#2B303C", outline='')
            c.create_oval(5, 4, 27, 26, fill="#FFFFFF", outline='')
            self.lbl_toggle_state.config(text="OFF", fg=SwissDesign.TEXT_MUTED)
            self.lbl_card_sub.config(text="默认过滤中 · 官方省略句号逗号", fg=SwissDesign.TEXT_MUTED)
            self.lbl_mode_val.config(text="DEFAULT / 官方过滤模式", fg=SwissDesign.TEXT_MUTED)

    def toggle_switch(self):
        self.is_active = not self.is_active
        self.draw_toggle()

        code, pids, has_editor = get_jianying_runtime()

        if self.is_active:
            # 开启保留：立即对当前剪映引擎注入内存补丁，并保护当前剪贴板
            if has_editor:
                for pid in pids:
                    patch_process_memory(pid, apply_patch=True)
            try:
                curr_clip = win_get_clipboard_text()
                if curr_clip and guard.needs_protection(curr_clip):
                    protected = guard.protect_text(curr_clip)
                    win_set_clipboard_text(protected)
            except Exception:
                pass
            self.lbl_bottom.config(text=f"[{datetime.now().strftime('%H:%M:%S')}] 句读保护已激活 · 文稿匹配将保留句号逗号")
        else:
            # 关闭/恢复默认：恢复官方默认过滤规则，并还原当前剪贴板
            if has_editor:
                for pid in pids:
                    patch_process_memory(pid, apply_patch=False)
            try:
                curr_clip = win_get_clipboard_text()
                if curr_clip and guard.needs_restoration(curr_clip):
                    restored = guard.restore_text(curr_clip)
                    win_set_clipboard_text(restored)
            except Exception:
                pass
            self.lbl_bottom.config(text=f"[{datetime.now().strftime('%H:%M:%S')}] 已恢复官方默认过滤设置")

    # -----------------------------------------------------------------------
    # 后台实时剪贴板守护线程 (解决多项目切换、重新复制以及静置失效的 Bug)
    # -----------------------------------------------------------------------
    def start_sentinel_thread(self):
        t = threading.Thread(target=self._clipboard_sentinel_loop, daemon=True)
        t.start()

    def _clipboard_sentinel_loop(self):
        last_seq = None
        while True:
            try:
                if self.is_active:
                    seq = user32.GetClipboardSequenceNumber()
                    if seq != last_seq:
                        last_seq = seq
                        text = win_get_clipboard_text()
                        if text and guard.needs_protection(text):
                            protected = guard.protect_text(text)
                            if protected != text:
                                if win_set_clipboard_text(protected):
                                    last_seq = user32.GetClipboardSequenceNumber()
            except Exception:
                pass
            time.sleep(0.1)

    # -----------------------------------------------------------------------
    # 后台实时动态监听与引擎守护线程 (解决切换草稿工程后补丁失效的 Bug)
    # -----------------------------------------------------------------------
    def start_monitor_thread(self):
        t = threading.Thread(target=self._monitor_loop, daemon=True)
        t.start()

    def _monitor_loop(self):
        while True:
            try:
                code, pids, has_editor = get_jianying_runtime()
                # 持续持久守护：当开关开启时，自动检测并对所有新打开的工程进程打上补丁
                if self.is_active and has_editor:
                    for pid in pids:
                        patch_process_memory(pid, apply_patch=True)
                self.after(0, self._update_ui_state, code, pids, has_editor)
            except Exception:
                pass
            time.sleep(1.0)

    def _update_ui_state(self, code, pids, has_editor):
        if code == 'OFFLINE':
            self.lbl_engine_val.config(text="○ OFFLINE / 剪映未运行", fg=SwissDesign.TEXT_MUTED)
        elif code == 'READY':
            pid_str = ",".join(str(p) for p in pids)
            self.lbl_engine_val.config(text=f"● READY / 编辑引擎就绪 ({pid_str})", fg=SwissDesign.STATUS_GREEN)
        elif code == 'STANDBY':
            self.lbl_engine_val.config(text="◐ STANDBY / 剪映启动中", fg=SwissDesign.STATUS_AMBER)

    # -----------------------------------------------------------------------
    # 极简说明弹窗：分 step 写使用说明，一句话一个操作，零多余
    # -----------------------------------------------------------------------
    def show_info_modal(self):
        modal = tk.Toplevel(self)
        modal.title("INFO / 说明")
        modal.geometry("420x340")
        modal.resizable(False, False)
        modal.configure(bg=SwissDesign.BG)
        modal.transient(self)
        modal.grab_set()

        try:
            self.update_idletasks()
            x = self.winfo_x() + (self.winfo_width() - 420) // 2
            y = self.winfo_y() + (self.winfo_height() - 340) // 2
            modal.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        top = tk.Frame(modal, bg=SwissDesign.BG)
        top.pack(fill="x", padx=24, pady=(20, 10))

        tk.Label(
            top,
            text="USER MANUAL",
            font=SwissDesign.FONT_LABEL,
            fg=SwissDesign.ACCENT_RED,
            bg=SwissDesign.BG
        ).pack(anchor="w")

        tk.Label(
            top,
            text="使用步骤",
            font=SwissDesign.FONT_TITLE,
            fg=SwissDesign.TEXT_HERO,
            bg=SwissDesign.BG
        ).pack(anchor="w", pady=(1, 0))

        tk.Frame(modal, height=1, bg=SwissDesign.LINE).pack(fill="x", padx=24, pady=(0, 12))

        # 纯步骤卡片
        body = tk.Frame(modal, bg=SwissDesign.SURFACE, highlightbackground=SwissDesign.LINE, highlightthickness=1)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 14), ipady=4)

        steps = [
            ("STEP 01", "打开电脑版剪映。"),
            ("STEP 02", "打开本工具，将拨动开关拨至 [ON]。"),
            ("STEP 03", "复制文稿并在剪映文稿匹配中使用（支持多次复制与切换工程）。"),
            ("STEP 04", "如需恢复官方默认效果，将开关拨回 [OFF]。")
        ]

        for s_idx, s_text in steps:
            s_row = tk.Frame(body, bg=SwissDesign.SURFACE)
            s_row.pack(fill="x", padx=16, pady=6)

            tk.Label(
                s_row,
                text=s_idx,
                font=("Consolas", 8, "bold"),
                fg=SwissDesign.ACCENT_RED,
                bg=SwissDesign.SURFACE,
                width=8,
                anchor="w"
            ).pack(side="left")

            tk.Label(
                s_row,
                text=s_text,
                font=SwissDesign.FONT_BODY,
                fg=SwissDesign.TEXT_HERO,
                bg=SwissDesign.SURFACE,
                anchor="w"
            ).pack(side="left", fill="x", expand=True)

        btn_box = tk.Frame(modal, bg=SwissDesign.BG)
        btn_box.pack(fill="x", padx=24, pady=(0, 16))

        tk.Button(
            btn_box,
            text="我知道了 / CONFIRM",
            bg=SwissDesign.ACCENT_RED,
            fg="#FFFFFF",
            activebackground="#B8000D",
            activeforeground="#FFFFFF",
            font=SwissDesign.FONT_BTN,
            relief="flat",
            bd=0,
            pady=6,
            cursor="hand2",
            command=modal.destroy
        ).pack(fill="x")

    # -----------------------------------------------------------------------
    # 云端鉴权与在线自动更新逻辑
    # -----------------------------------------------------------------------
    def _check_auth(self):
        data = None
        # 1. 尝试直接请求 raw 链接
        try:
            r = requests.get(AUTH_URL, timeout=3)
            if r.status_code == 200:
                data = r.json()
        except Exception:
            pass

        # 2. 备用请求 GitHub API 链接
        if not data:
            try:
                r = requests.get(API_AUTH_URL, timeout=4)
                if r.status_code == 200:
                    c = r.json().get('content', '')
                    data = json.loads(base64.b64decode(c).decode('utf-8'))
            except Exception:
                pass

        if not data:
            return

        status = data.get("status", "active")
        remote_ver = data.get("version", VERSION)
        update_url = data.get("update_url", "")

        if status == "destroy":
            self.after(0, self._handle_destroy)
            return
        elif status == "blocked":
            self.after(0, self._handle_blocked)
            return

        if self._is_newer(remote_ver, VERSION) and update_url:
            self.after(0, self._handle_update, remote_ver, update_url)

    def _is_newer(self, remote, local):
        try:
            r_parts = [int(x) for x in remote.split('.')]
            l_parts = [int(x) for x in local.split('.')]
            return r_parts > l_parts
        except Exception:
            return False

    def _handle_destroy(self):
        messagebox.showerror("Error", "Security authorization expired. Application will exit.")
        self._self_destruct()

    def _handle_blocked(self):
        messagebox.showwarning("Notice", "This version has been disabled by the server.")
        self.destroy()
        sys.exit(0)

    def _handle_update(self, new_ver, url):
        msg = f"发现新版本 v{new_ver} (当前 v{VERSION})，是否立即自动更新？"
        if messagebox.askyesno("UPDATE / 更新提示", msg):
            threading.Thread(target=self._download_and_update, args=(url,), daemon=True).start()

    def _download_and_update(self, url):
        import urllib.request
        if getattr(sys, 'frozen', False):
            exe = os.path.abspath(sys.executable)
        else:
            exe = os.path.abspath(sys.argv[0])
        if not exe.lower().endswith('.exe'):
            import webbrowser
            webbrowser.open(url)
            return
        new = exe + ".new"
        try:
            urllib.request.urlretrieve(url, new)
            bat = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), "upd_period.bat")
            with open(bat, "w", encoding="gbk") as f:
                f.write(f'''@echo off
set "EXE={exe}"
set "NEW={new}"
set /a count=0
:loop
del /f /q "%EXE%" >nul 2>&1
if not exist "%EXE%" goto replace
set /a count+=1
if %count% geq 60 goto replace
ping 127.0.0.1 -n 2 >nul
goto loop
:replace
move /y "%NEW%" "%EXE%" >nul 2>&1
start "" "%EXE%"
del /f /q "%~f0" >nul 2>&1
''')
            subprocess.Popen(bat, creationflags=0x08000000)
            os._exit(0)
        except Exception:
            import webbrowser
            webbrowser.open(url)

    def _self_destruct(self):
        if getattr(sys, 'frozen', False):
            exe = os.path.abspath(sys.executable)
        else:
            exe = os.path.abspath(sys.argv[0])
        bat = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), "d.bat")
        try:
            with open(bat, "w", encoding="gbk") as f:
                f.write(f'''@echo off
set "EXE={exe}"
:loop
del /f /q "%EXE%" >nul 2>&1
if not exist "%EXE%" goto end
ping 127.0.0.1 -n 2 >nul
goto loop
:end
del /f /q "%~f0" >nul 2>&1
''')
            subprocess.Popen(bat, creationflags=0x08000000)
        except Exception:
            pass
        os._exit(0)


# ---------------------------------------------------------------------------
# 单文件启动入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = MinimalApp()
    app.mainloop()
