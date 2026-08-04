#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""win_snap.py - magnetisk fönster-snapping mellan SM5K-cockpitfönstren
(sunsync, SM5K WRC Rotator Control, SM5K SPE Tuner Klient). Läser de andra
körande programmens fönsterposition live via Windows-API (fönstertitel +
GetWindowRect), ingen delad kod/process mellan programmen krävs.
"""
import win32gui

TITLE_PREFIXES = (
    "SunSync by SM5K",
    "SM5K WRC Rotator Control",
    "SM5K SPE Tuner Klient",
)
SNAP_DIST = 12


def _other_rects(own_title):
    rects = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title == own_title:
            return
        if any(title.startswith(p) for p in TITLE_PREFIXES):
            rects.append(win32gui.GetWindowRect(hwnd))

    win32gui.EnumWindows(cb, None)
    return rects


def snap(own_title, x, y, w, h):
    """Snäpp föreslagen (x, y) kant i kant mot andra cockpitfönster inom SNAP_DIST px."""
    l, t, r, b = x, y, x + w, y + h
    for (ol, ot, orr, ob) in _other_rects(own_title):
        if abs(l - orr) <= SNAP_DIST:
            x += orr - l
        elif abs(r - ol) <= SNAP_DIST:
            x += ol - r
        elif abs(l - ol) <= SNAP_DIST:
            x += ol - l
        elif abs(r - orr) <= SNAP_DIST:
            x += orr - r
        if abs(t - ob) <= SNAP_DIST:
            y += ob - t
        elif abs(b - ot) <= SNAP_DIST:
            y += ot - b
        elif abs(t - ot) <= SNAP_DIST:
            y += ot - t
        elif abs(b - ob) <= SNAP_DIST:
            y += ob - b
    return x, y
