# ruff: noqa: E501
"""Local Ming Agent Workbench.

This stdlib HTTP app is the current Web UI stage described in the Ming design:
local Python server + JSON state + SSE events. It intentionally avoids a
frontend build chain while the product interaction is still changing quickly.
"""

from __future__ import annotations

import errno
import json
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ming.config import load_config
from ming.core.live_events import LiveEventStore
from ming.ui.chat_runtime import ChatRuntime

WORKBENCH_SCHEMA_VERSION = "ming-workbench-v1"

CLIENT_DISCONNECT_ERRNOS = {
    errno.ECONNABORTED,
    errno.ECONNRESET,
    errno.EPIPE,
    getattr(errno, "WSAECONNABORTED", 10053),
    getattr(errno, "WSAECONNRESET", 10054),
}


def _is_client_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
        return True
    return isinstance(exc, OSError) and exc.errno in CLIENT_DISCONNECT_ERRNOS


DEMO_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ming Agent Workbench</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f3ef;
      --surface: #fbfaf7;
      --surface-2: #f0eee8;
      --surface-3: #e7e4dc;
      --ink: #1d2522;
      --ink-2: #48534f;
      --muted: #74807b;
      --line: #d7d3c8;
      --accent: #167866;
      --accent-2: #dfeee9;
      --warn: #a4571e;
      --warn-bg: #f5e6d8;
      --bad: #a83d3d;
      --bad-bg: #f3dddd;
      --good: #267252;
      --shadow: 0 24px 70px rgba(43, 54, 49, .10);
      --radius-lg: 18px;
      --radius-md: 12px;
      --radius-sm: 8px;
      --mono: "SFMono-Regular", "Cascadia Mono", "JetBrains Mono", Consolas, monospace;
      --sans: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      --sidebar-w: 286px;
      --process-w: 342px;
    }

    * { box-sizing: border-box; }
    html, body { height: 100%; overflow: hidden; margin: 0; }

    body {
      height: 100dvh;
      overflow: hidden;
      font-family: var(--sans);
      background:
        radial-gradient(circle at 14% 16%, rgba(22, 120, 102, .11), transparent 28%),
        linear-gradient(135deg, #f8f7f3 0%, var(--bg) 52%, #ece9e1 100%);
      color: var(--ink);
    }

    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: .18;
      background-image:
        linear-gradient(rgba(29, 37, 34, .04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(29, 37, 34, .04) 1px, transparent 1px);
      background-size: 28px 28px;
      mask-image: linear-gradient(to bottom, #000, transparent 70%);
    }

    button, textarea, input { font: inherit; }
    button {
      border: 0; cursor: pointer;
      transition: transform .18s ease, background .18s ease, border-color .18s ease, color .18s ease;
    }
    button:active { transform: translateY(1px) scale(.99); }
    button:focus-visible, textarea:focus-visible, input:focus-visible {
      outline: 3px solid rgba(22, 120, 102, .25);
      outline-offset: 2px;
    }

    /* ===== App Shell ===== */
    .app-shell {
      width: min(1720px, calc(100vw - 32px));
      height: calc(100dvh - 32px);
      margin: 16px auto;
      display: grid;
      grid-template-columns: 0px minmax(400px, 1fr) var(--process-w);
      gap: 14px;
      transition: grid-template-columns .3s ease;
    }

    .app-shell.sidebar-open {
      grid-template-columns: var(--sidebar-w) minmax(400px, 1fr) var(--process-w);
    }
    .app-shell.process-closed {
      grid-template-columns: 0px minmax(400px, 1fr) 0px;
    }
    .app-shell.sidebar-open.process-closed {
      grid-template-columns: var(--sidebar-w) minmax(400px, 1fr) 0px;
    }

    .panel {
      background: rgba(251, 250, 247, .92);
      border: 1px solid rgba(215, 211, 200, .86);
      border-radius: var(--radius-lg);
      box-shadow: var(--shadow);
      min-width: 0;
      overflow: hidden;
      transition: opacity .3s ease, transform .3s ease;
    }

    /* ===== Left Sidebar ===== */
    .session-rail {
      display: flex;
      flex-direction: column;
      height: calc(100dvh - 32px);
      opacity: 0;
      transform: translateX(-12px);
      pointer-events: none;
    }

    .sidebar-open .session-rail {
      opacity: 1;
      transform: translateX(0);
      pointer-events: auto;
    }

    .rail-head {
      padding: 18px 18px 14px;
      border-bottom: 1px solid var(--line);
    }

    .brand-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 760;
      letter-spacing: -.02em;
    }

    .brand-mark {
      width: 34px; height: 34px;
      display: grid; place-items: center;
      border-radius: 10px;
      background: var(--ink);
      color: var(--surface);
      font-weight: 760;
    }

    .subtle { color: var(--muted); font-size: 12px; line-height: 1.45; }

    .new-chat {
      height: 34px; padding: 0 12px;
      border-radius: var(--radius-sm);
      background: var(--accent); color: #fff;
      font-weight: 650; white-space: nowrap;
    }

    .search {
      width: 100%; height: 38px;
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      background: #fffefa; color: var(--ink);
      padding: 0 12px;
    }

    .session-list {
      padding: 10px; overflow: auto;
      display: grid; gap: 8px; min-height: 0;
    }

    .session-item {
      text-align: left; padding: 12px;
      border-radius: var(--radius-md);
      background: transparent; color: var(--ink);
      border: 1px solid transparent;
    }
    .session-item:hover { background: var(--surface-2); }
    .session-item.active {
      background: #ffffff;
      border-color: rgba(22, 120, 102, .28);
      box-shadow: 0 8px 28px rgba(43, 54, 49, .06);
    }

    .session-title {
      font-weight: 700; font-size: 13px;
      line-height: 1.35; margin-bottom: 6px;
    }

    .session-meta {
      display: flex; justify-content: space-between;
      gap: 10px; color: var(--muted); font-size: 11px;
    }
    .session-meta strong { color: var(--ink-2); font-weight: 650; }

    .rail-footer {
      margin-top: auto; padding: 12px;
      border-top: 1px solid var(--line);
      display: grid; gap: 8px;
    }

    /* ===== Main Workspace ===== */
    .main-workspace {
      height: calc(100dvh - 32px);
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 0;
    }

    .topbar {
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .topbar-left {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .topbar-brand {
      display: flex; align-items: center; gap: 8px;
      font-weight: 760; font-size: 16px; letter-spacing: -.02em;
    }

    .topbar-brand .brand-mark {
      width: 28px; height: 28px; font-size: 14px;
      border-radius: 8px;
    }

    .topbar-session {
      color: var(--ink-2); font-size: 13px;
      font-weight: 600;
      padding-left: 10px;
      border-left: 1px solid var(--line);
    }

    .topbar-right {
      margin-left: auto;
      display: flex; align-items: center; gap: 6px;
    }

    .icon-button {
      width: 34px; height: 34px;
      border-radius: var(--radius-sm);
      background: transparent; color: var(--ink-2);
      display: grid; place-items: center;
      font-size: 18px;
    }
    .icon-button:hover { background: var(--surface-2); }
    .icon-button.active {
      background: var(--accent-2);
      color: var(--accent);
    }

    .chip {
      display: inline-flex; align-items: center;
      gap: 6px; min-height: 24px; padding: 0 8px;
      border: 1px solid var(--line); border-radius: 999px;
      background: #fffefa; color: var(--ink-2);
      font-size: 11px; white-space: nowrap;
    }
    .chip.accent {
      background: var(--accent-2);
      border-color: rgba(22, 120, 102, .28);
      color: var(--accent); font-weight: 650;
    }

    /* ===== Conversation ===== */
    .conversation {
      overflow: auto;
      padding: 22px 22px 16px;
      display: grid;
      align-content: start;
      gap: 16px;
      min-height: 0;
    }

    .message {
      display: grid; gap: 8px;
      max-width: 82%;
    }
    .message.user { justify-self: end; }
    .message.ming { justify-self: start; }

    .message-label {
      color: var(--muted); font-size: 11px;
      font-family: var(--mono);
    }

    .bubble {
      border-radius: 16px;
      padding: 13px 15px;
      line-height: 1.55; font-size: 14px;
    }
    .message.user .bubble {
      background: var(--ink); color: #fff;
      border-bottom-right-radius: 6px;
    }
    .message.ming .bubble {
      background: #fffefa;
      border: 1px solid var(--line);
      border-bottom-left-radius: 6px;
    }
    .bubble-content { display: grid; gap: 9px; }
    .bubble-content p,
    .bubble-content ul,
    .bubble-content ol { margin: 0; }
    .bubble-content ul,
    .bubble-content ol {
      padding-left: 20px;
      display: grid;
      gap: 5px;
    }
    .bubble-content h3 {
      margin: 2px 0 0;
      font-size: 13px;
      line-height: 1.35;
    }
    .bubble-content code {
      font-family: var(--mono);
      font-size: 12px;
      background: var(--surface-2);
      border: 1px solid var(--line);
      border-radius: 5px;
      padding: 1px 5px;
    }
    .reply-status {
      display: inline-flex;
      align-items: center;
      justify-self: start;
      height: 24px;
      padding: 0 9px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--surface-2);
      color: var(--ink-2);
      font-size: 12px;
      font-weight: 680;
    }
    .reply-status.done {
      color: var(--good);
      background: rgba(38, 114, 82, .10);
      border-color: rgba(38, 114, 82, .22);
    }
    .reply-status.pending {
      color: var(--warn);
      background: var(--warn-bg);
      border-color: rgba(164, 87, 30, .25);
    }
    .reply-status.blocked {
      color: var(--bad);
      background: var(--bad-bg);
      border-color: rgba(168, 61, 61, .25);
    }

    /* ===== Tool Call Card ===== */
    .tool-card {
      max-width: 82%;
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: rgba(255, 254, 250, .76);
      overflow: hidden;
    }

    .tool-card-head {
      display: flex; align-items: center;
      gap: 8px; padding: 10px 13px;
      font-size: 12px; color: var(--ink-2);
      cursor: pointer;
    }
    .tool-card-head:hover { background: var(--surface-2); }

    .tool-icon { font-size: 14px; }
    .tool-name { font-weight: 700; color: var(--ink); font-family: var(--mono); font-size: 12px; }
    .tool-status { margin-left: auto; font-size: 11px; }
    .tool-status.ok { color: var(--good); }
    .tool-status.running { color: var(--accent); }
    .tool-status.error { color: var(--bad); }
    .tool-expand { color: var(--muted); font-size: 12px; margin-left: 4px; }

    .tool-card-body {
      display: none;
      padding: 0 13px 12px;
      font-size: 12px; color: var(--ink-2);
      border-top: 1px solid var(--line);
    }
    .tool-card.open .tool-card-body { display: block; }
    .tool-card.open .tool-expand { transform: rotate(180deg); }

    /* ===== Agent Process Cards ===== */
    .thinking-card,
    .process-card {
      justify-self: start;
      max-width: 82%;
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: rgba(255, 254, 250, .72);
    }
    .thinking-card {
      min-width: 260px;
      padding: 12px 14px;
      display: grid;
      gap: 7px;
    }
    .thinking-head,
    .process-head {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .thinking-title,
    .process-title {
      font-size: 12px;
      font-weight: 760;
      color: var(--ink);
    }
    .thinking-caption,
    .process-summary {
      margin: 0;
      color: var(--ink-2);
      font-size: 12px;
      line-height: 1.45;
    }
    .thinking-dots {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-left: auto;
    }
    .thinking-dots span {
      width: 5px;
      height: 5px;
      border-radius: 999px;
      background: var(--accent);
      opacity: .28;
      animation: mingPulse 1.05s infinite ease-in-out;
    }
    .thinking-dots span:nth-child(2) { animation-delay: .14s; }
    .thinking-dots span:nth-child(3) { animation-delay: .28s; }
    @keyframes mingPulse {
      0%, 80%, 100% { opacity: .28; transform: translateY(0); }
      40% { opacity: 1; transform: translateY(-2px); }
    }
    .process-card {
      overflow: hidden;
    }
    .process-head {
      padding: 10px 13px;
      border-bottom: 1px solid transparent;
    }
    .process-card.has-detail .process-head {
      cursor: pointer;
    }
    .process-card.has-detail.open .process-head {
      border-bottom-color: var(--line);
    }
    .process-dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 0 4px rgba(22, 120, 102, .10);
      flex: 0 0 auto;
    }
    .process-card.done .process-dot { background: var(--good); }
    .process-card.error .process-dot { background: var(--bad); }
    .process-meta {
      margin-left: auto;
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
    }
    .process-body {
      display: none;
      padding: 10px 13px 12px;
      color: var(--ink-2);
      font-size: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
    }
    .process-card.open .process-body { display: block; }

    /* ===== Pause Notice ===== */
    .notice {
      max-width: 82%;
      padding: 14px 15px;
      border-radius: var(--radius-md);
      background: var(--warn-bg);
      border: 1px solid rgba(164, 87, 30, .24);
      display: grid; gap: 8px;
    }
    .notice-title { font-weight: 760; color: #6f3a13; font-size: 13px; }
    .notice p { margin: 0; color: #6f4a2a; line-height: 1.5; font-size: 13px; }

    .open-detail {
      background: transparent; color: var(--accent);
      font-weight: 700; padding: 0; white-space: nowrap;
      font-size: 12px;
    }

    /* ===== Verdict Card ===== */
    .verdict-card {
      max-width: 92%;
      border: 2px solid rgba(164, 87, 30, .35);
      border-radius: var(--radius-lg);
      background: #fffefa;
      display: grid;
      grid-template-rows: auto auto auto auto;
      align-self: start;
      min-height: max-content;
      overflow: hidden;
    }

    .verdict-head {
      padding: 14px 16px;
      background: var(--warn-bg);
      border-bottom: 1px solid rgba(164, 87, 30, .2);
      display: grid;
      gap: 5px;
    }
    .verdict-head-title {
      font-weight: 780; font-size: 14px; color: #6f3a13;
      line-height: 1.35;
    }
    .verdict-head-topic {
      font-size: 13px; color: #6f4a2a;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }

    .verdict-columns {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0;
    }

    .verdict-col {
      padding: 14px 16px;
    }
    .verdict-col:first-child {
      border-right: 1px solid var(--line);
    }
    .verdict-col-title {
      font-weight: 720; font-size: 13px;
      color: var(--accent); margin-bottom: 8px;
    }
    .verdict-col ul {
      margin: 0; padding-left: 18px;
      font-size: 12px; color: var(--ink-2);
      line-height: 1.6;
    }

    .verdict-analysis {
      padding: 12px 16px;
      border-top: 1px solid var(--line);
      font-size: 12px; color: var(--ink-2);
      line-height: 1.5;
      background: var(--surface-2);
    }
    .verdict-analysis strong {
      color: var(--ink); font-weight: 700;
    }

    .verdict-actions {
      padding: 12px 16px;
      border-top: 1px solid var(--line);
      display: flex; gap: 8px; flex-wrap: wrap;
    }

    .verdict-btn {
      height: 36px; padding: 0 16px;
      border-radius: var(--radius-sm);
      font-weight: 680; font-size: 13px;
      white-space: nowrap;
    }
    .verdict-btn.primary {
      background: var(--accent); color: #fff;
    }
    .verdict-btn.secondary {
      background: #fffefa; color: var(--ink-2);
      border: 1px solid var(--line);
    }
    .verdict-btn.tertiary {
      background: transparent; color: var(--accent);
      border: 1px dashed rgba(22, 120, 102, .3);
    }

    /* ===== Composer ===== */
    .composer {
      padding: 14px 16px;
      border-top: 1px solid var(--line);
      background: rgba(251, 250, 247, .88);
    }
    .composer-box {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px; align-items: end;
      border: 1px solid var(--line);
      border-radius: var(--radius-lg);
      background: #fffefa;
      padding: 10px;
    }
    textarea {
      resize: none; min-height: 44px; max-height: 160px;
      border: 0; outline: 0; background: transparent;
      color: var(--ink); padding: 4px 6px; line-height: 1.5;
    }
    .send-button {
      height: 40px; padding: 0 16px;
      border-radius: var(--radius-md);
      background: var(--accent); color: #fff;
      font-weight: 760; white-space: nowrap;
    }

    .composer-hint {
      margin-top: 6px;
      text-align: right;
      font-size: 11px;
      color: var(--muted);
    }

    /* ===== Right Process Rail ===== */
    .process-rail {
      display: flex;
      flex-direction: column;
      height: calc(100dvh - 32px);
      transition: opacity .3s ease, transform .3s ease;
    }

    .process-closed .process-rail {
      opacity: 0;
      transform: translateX(12px);
      pointer-events: none;
    }

    .process-rail .rail-head {
      display: grid; gap: 10px;
    }

    .section-title {
      font-size: 13px; font-weight: 760;
      letter-spacing: -.01em;
    }

    .right-scroll {
      overflow-y: auto;
      overflow-x: hidden;
      padding: 12px;
      display: grid; gap: 12px; min-height: 0;
    }

    .side-block {
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: rgba(255, 254, 250, .76);
      padding: 13px;
      display: grid; gap: 10px;
    }
    .side-block header {
      display: flex; align-items: center;
      justify-content: space-between; gap: 10px;
    }

    .small-button {
      height: 28px; padding: 0 9px;
      border-radius: 7px;
      background: var(--surface-2); color: var(--ink-2);
      border: 1px solid var(--line);
      font-weight: 650; font-size: 12px;
    }

    .todo-list, .artifact-list, .metric-list, .context-list {
      display: grid; gap: 8px;
    }

    .todo-row, .artifact-row, .metric-row, .context-row {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 9px; align-items: start;
      font-size: 12px; color: var(--ink-2); line-height: 1.45;
    }
    .todo-row span:last-child,
    .artifact-row span:last-child,
    .context-row span:last-child {
      min-width: 0;
      overflow-wrap: anywhere;
    }

    .todo-check {
      width: 15px; height: 15px; margin-top: 2px;
      border-radius: 4px;
      border: 1px solid var(--line); background: #fff;
    }
    .todo-check.checked {
      background: var(--accent);
      border-color: var(--accent);
      box-shadow: inset 0 0 0 3px #fff;
    }

    .file-chip {
      font-family: var(--mono);
      color: var(--ink);
      overflow-wrap: anywhere;
    }

    .meter {
      height: 8px; border-radius: 999px;
      background: var(--surface-3); overflow: hidden;
      margin-top: 6px;
    }
    .meter-fill {
      height: 100%; width: 63%;
      border-radius: inherit;
      background: var(--accent);
    }

    .metric-row {
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
    }
    .metric-value {
      font-family: var(--mono); color: var(--ink);
      font-variant-numeric: tabular-nums;
    }

    /* ===== Modal ===== */
    .modal-backdrop {
      position: fixed; inset: 0; z-index: 40;
      display: none; place-items: center; padding: 24px;
      background: rgba(29, 37, 34, .36);
    }
    .modal-backdrop.open { display: grid; }

    .modal {
      width: min(980px, 100%);
      max-height: min(820px, calc(100dvh - 48px));
      display: grid; grid-template-rows: auto auto 1fr;
      background: var(--surface);
      border: 1px solid rgba(215, 211, 200, .92);
      border-radius: 24px;
      box-shadow: 0 34px 120px rgba(29, 37, 34, .24);
      overflow: hidden;
    }

    .modal-head {
      padding: 18px 20px;
      display: flex; justify-content: space-between; align-items: center;
      border-bottom: 1px solid var(--line);
    }
    .modal-title {
      font-size: 18px; font-weight: 780; letter-spacing: -.02em;
    }
    .close-button {
      width: 34px; height: 34px;
      border-radius: 10px;
      background: var(--surface-2); color: var(--ink);
      border: 1px solid var(--line);
      font-size: 20px; line-height: 1;
    }

    .modal-tabs {
      display: flex; gap: 6px; padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      background: rgba(240, 238, 232, .7);
    }
    .tab-button {
      height: 34px; padding: 0 12px;
      border-radius: var(--radius-sm);
      color: var(--ink-2); background: transparent;
      font-weight: 680;
    }
    .tab-button.active {
      color: var(--accent); background: #fffefa;
      box-shadow: inset 0 0 0 1px rgba(22, 120, 102, .22);
    }

    .modal-body { overflow: auto; padding: 18px; }

    .timeline { display: grid; gap: 12px; }
    .timeline-row {
      display: grid;
      grid-template-columns: 106px minmax(0, 1fr);
      gap: 16px; align-items: start;
    }
    .timeline-time {
      color: var(--muted); font-family: var(--mono);
      font-size: 11px; padding-top: 3px;
    }
    .timeline-card {
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      background: #fffefa; padding: 13px;
      display: grid; gap: 8px;
    }
    .timeline-card.warning {
      background: var(--warn-bg);
      border-color: rgba(164, 87, 30, .25);
    }
    .timeline-card.error {
      background: var(--bad-bg);
      border-color: rgba(168, 61, 61, .25);
    }
    .timeline-card strong { font-size: 13px; }
    .timeline-card p {
      margin: 0; color: var(--ink-2);
      font-size: 12px; line-height: 1.5;
    }

    pre {
      margin: 0; white-space: pre-wrap; overflow-wrap: anywhere;
      background: #202824; color: #f0f3ee;
      border-radius: var(--radius-md); padding: 14px;
      font-family: var(--mono); font-size: 12px; line-height: 1.55;
    }

    .settings-grid { display: grid; gap: 16px; }
    .settings-card {
      border: 1px solid var(--line);
      border-radius: var(--radius-lg);
      background: rgba(255, 254, 250, .78);
      padding: 16px;
      display: grid;
      gap: 12px;
    }
    .settings-fields {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .settings-field { display: grid; gap: 6px; }
    .settings-field label {
      color: var(--ink-2);
      font-size: 12px;
      font-weight: 650;
    }
    .settings-field input {
      height: 38px;
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      background: #fff;
      color: var(--ink);
      padding: 0 10px;
    }
    .settings-field input[readonly] { cursor: default; }
    .settings-save {
      height: 36px;
      padding: 0 16px;
      border-radius: var(--radius-sm);
      font-weight: 680;
      font-size: 13px;
      background: var(--accent-2);
      color: var(--accent);
      border: 1px solid rgba(22, 120, 102, .28);
      justify-self: start;
    }

    .hidden { display: none; }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after {
        animation-duration: .01ms !important;
        transition-duration: .01ms !important;
      }
    }

    @media (max-width: 900px) {
      .app-shell {
        grid-template-columns: 0px minmax(0, 1fr) 0px !important;
      }
      .process-rail, .session-rail {
        display: none;
      }
      .verdict-columns { grid-template-columns: 1fr; }
      .settings-fields { grid-template-columns: 1fr; }
      .verdict-col:first-child { border-right: 0; border-bottom: 1px solid var(--line); }
    }

    /* ===== Runtime additions on top of the demo visual system ===== */
    .stop-button {
      height: 40px; padding: 0 12px;
      border-radius: var(--radius-md);
      background: var(--surface-2); color: var(--ink-2);
      border: 1px solid var(--line);
      font-weight: 700; white-space: nowrap;
    }
    .stop-button:hover { background: var(--surface-3); }
    .stop-button:disabled { opacity: .48; cursor: not-allowed; }
    .composer-box.runtime {
      grid-template-columns: minmax(0, 1fr) auto auto;
    }
    .live-events { display: grid; gap: 8px; max-height: 180px; overflow: auto; }
    .live-event {
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      padding: 8px;
      background: rgba(255, 254, 250, .8);
      font-size: 12px;
      line-height: 1.45;
    }
    .tab-panel.hidden { display: none; }
    .message, .tool-card, .notice, .verdict-card { justify-self: start; }
    .message.user { justify-self: end; }
  </style>
</head>
<body>
  <div class="app-shell" id="appShell">
    <aside class="panel session-rail" id="sessionRail" aria-label="会话记录">
      <div class="rail-head">
        <div class="brand-row">
          <div class="brand"><span class="brand-mark">明</span><span>Ming</span></div>
          <button class="new-chat" type="button" id="newChatBtn">新会话</button>
        </div>
        <input class="search" value="" placeholder="搜索会话" aria-label="搜索会话">
      </div>
      <div class="session-list" id="sessionList"></div>
      <div class="rail-footer">
        <div class="subtle">Ming 任务工作台 · 本地运行</div>
      </div>
    </aside>

    <main class="panel main-workspace" id="mainWorkspace">
      <header class="topbar">
        <div class="topbar-left">
          <button class="icon-button" type="button" id="toggleSidebar" title="切换会话列表">☰</button>
          <div class="topbar-brand"><span class="brand-mark">明</span><span>Ming</span></div>
          <div class="topbar-session" id="taskText">暂无任务</div>
        </div>
        <div class="topbar-right">
          <span class="chip">DeepSeek</span>
          <span class="hidden" id="stateText">loading</span>
          <button class="icon-button" type="button" data-modal="timeline" title="查看 Ming 的思考过程">&#129504;</button>
          <button class="icon-button" type="button" id="toggleProcess" title="切换过程面板 (可锁定)">&#128202;</button>
          <button class="icon-button" type="button" data-modal="settings" title="设置与模型配置">&#9881;</button>
        </div>
      </header>

      <section class="conversation" id="conversation" aria-label="当前会话"></section>

      <footer class="composer">
        <form class="composer-box runtime" id="chatForm">
          <textarea id="messageInput" name="message" aria-label="输入" placeholder="说点什么..." rows="1"></textarea>
          <button class="stop-button" id="stopTurnBtn" type="button" disabled>停止思考</button>
          <button class="send-button" id="sendBtn" type="submit">发送</button>
        </form>
        <div class="composer-hint"><span id="chatStatus">ready</span> · Enter 发送 · Shift+Enter 换行</div>
      </footer>
    </main>

    <aside class="panel process-rail" id="processRail" aria-label="过程状态">
      <div class="rail-head">
        <div class="section-title">过程状态</div>
        <div class="subtle">Ming 正在操作的文件、计划、资源用量。</div>
      </div>
      <div class="right-scroll">
        <section class="side-block">
          <header><div class="section-title">产物</div><button class="small-button" type="button" data-modal="session_trace">打开</button></header>
          <div class="artifact-list" id="artifactList"></div>
        </section>
        <section class="side-block">
          <header><div class="section-title">TODO</div></header>
          <div class="todo-list" id="todoList"></div>
        </section>
        <section class="side-block">
          <header><div class="section-title">Token</div></header>
          <div class="metric-list" id="metricList"></div>
        </section>
        <section class="side-block">
          <header><div class="section-title">Context</div></header>
          <div class="context-list" id="contextList"></div>
        </section>
      </div>
    </aside>
  </div>

  <div class="modal-backdrop" id="detailModal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
    <div class="modal">
      <header class="modal-head">
        <div>
          <div class="modal-title" id="modalTitle">诊断详情</div>
          <div class="subtle">Ming 的动作记录、异常分析、SessionTrace。</div>
        </div>
        <button class="close-button" type="button" id="closeModal" aria-label="关闭">&times;</button>
      </header>
      <nav class="modal-tabs" aria-label="详情类型">
        <button class="tab-button active" type="button" data-tab="timeline">做了什么</button>
        <button class="tab-button" type="button" data-tab="exception">异常原因</button>
        <button class="tab-button" type="button" data-tab="session_trace">SessionTrace</button>
        <button class="tab-button" type="button" data-tab="settings">设置与模型</button>
      </nav>
      <div class="modal-body">
        <section class="tab-panel" data-panel="timeline"><div class="timeline" id="timelinePanel"></div></section>
        <section class="tab-panel hidden" data-panel="exception"><div class="timeline" id="exceptionPanel"></div></section>
        <section class="tab-panel hidden" data-panel="session_trace"><pre id="tracePanel">{}</pre></section>
        <section class="tab-panel hidden" data-panel="settings">
          <div class="settings-grid" id="settingsPanel">
            <div class="settings-card">
              <div>
                <div class="section-title">模型连接</div>
                <div class="subtle">接入 DeepSeek、GLM、MiniMax 或本地兼容 OpenAI 格式的服务。密钥只保存在本地。</div>
              </div>
              <div class="settings-fields">
                <div class="settings-field">
                  <label for="settingsApiBase">LLM API 地址</label>
                  <input id="settingsApiBase" value="">
                </div>
                <div class="settings-field">
                  <label for="settingsModel">模型名称</label>
                  <input id="settingsModel" value="">
                </div>
                <div class="settings-field">
                  <label for="settingsApiKey">API Key</label>
                  <input id="settingsApiKey" type="password" value="">
                </div>
                <div class="settings-field">
                  <label for="settingsTimeout">单次请求超时</label>
                  <input id="settingsTimeout" value="">
                </div>
              </div>
              <button class="settings-save" id="settingsSaveBtn" type="button">保存到本地设置</button>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>

  <script>
    const appShell = document.getElementById("appShell");
    const modal = document.getElementById("detailModal");
    const closeModal = document.getElementById("closeModal");
    const tabButtons = Array.from(document.querySelectorAll(".tab-button"));
    const panels = Array.from(document.querySelectorAll(".tab-panel"));
    const conversation = [];
    const liveEvents = [];
    const liveRunEvents = [];
    let allSessions = [];
    let currentConversationMode = "live";
    let selectedSessionId = "";
    let activeThinkingId = "";
    let stateTimeline = [];
    let traceTabs = {};
    let currentState = {};

    async function loadState() {
      try {
        const response = await fetch("/api/state", { cache: "no-store" });
        currentState = await response.json();
        render(currentState);
      } catch (error) {
        document.getElementById("stateText").textContent = "reconnecting";
      }
    }

    function render(state) {
      document.getElementById("taskText").textContent = state.current_task.text || "暂无任务";
      document.getElementById("stateText").textContent = `${state.agent.state} · ${state.agent.mode}`;
      stateTimeline = state.timeline || [];
      traceTabs = state.trace_tabs || {};
      allSessions = state.sessions || [];
      renderSessions(allSessions);
      renderTodo((state.process_panel && state.process_panel.todo.items) || []);
      renderArtifacts((state.process_panel && state.process_panel.artifacts) || {});
      renderMetrics((state.process_panel && state.process_panel.context) || {});
      renderContext((state.process_panel && state.process_panel.context) || {}, state.workspace);
      renderRunTimeline();
      renderModalTabs();
    }

    function renderSessions(sessions) {
      const root = document.getElementById("sessionList");
      root.innerHTML = "";
      if (!sessions.length) {
        root.innerHTML = `<div class="subtle">暂无历史会话</div>`;
        return;
      }
      for (const [index, session] of sessions.entries()) {
        const node = document.createElement("button");
        const isActive = selectedSessionId
          ? session.turn_id === selectedSessionId
          : index === 0 && currentConversationMode === "live";
        node.className = `session-item ${isActive ? "active" : ""}`;
        node.type = "button";
        node.dataset.turnId = session.turn_id || "";
        node.innerHTML =
          `<div class="session-title">${escapeHtml(session.title)}</div>` +
          `<div class="session-meta"><span>${escapeHtml(session.started_at || "")}</span></div>`;
        node.addEventListener("click", () => selectSession(session.turn_id || ""));
        root.appendChild(node);
      }
    }

    function renderTodo(items) {
      const root = document.getElementById("todoList");
      root.innerHTML = "";
      if (!items.length) {
        root.innerHTML = `<div class="subtle">暂无 TODO</div>`;
        return;
      }
      for (const item of items) {
        const row = document.createElement("div");
        row.className = "todo-row";
        row.innerHTML =
          `<span class="todo-check ${item.status === "completed" ? "checked" : ""}"></span>` +
          `<span>${escapeHtml(item.text || "")}<br><span class="subtle">${escapeHtml(item.status || "")}</span></span>`;
        root.appendChild(row);
      }
    }

    function renderArtifacts(artifacts) {
      const root = document.getElementById("artifactList");
      const files = artifacts.changed_files && artifacts.changed_files.length
        ? artifacts.changed_files
        : [];
      root.innerHTML = "";
      if (!files.length) {
        root.innerHTML = `<div class="subtle">暂无产物</div>`;
        return;
      }
      for (const file of files) {
        const row = document.createElement("div");
        row.className = "artifact-row";
        row.innerHTML =
          `<span class="todo-check checked"></span>` +
          `<span><span class="file-chip">${escapeHtml(file)}</span></span>`;
        root.appendChild(row);
      }
    }

    function renderMetrics(context) {
      const root = document.getElementById("metricList");
      const prompt = Number(context.total_prompt_tokens || context.turn_prompt_tokens || 0);
      const completion = Number(context.total_completion_tokens || context.turn_completion_tokens || 0);
      const total = prompt + completion;
      const percent = Math.max(0, Math.min(100, Math.round(total / 1000)));
      root.innerHTML =
        `<div class="metric-row"><span>上下文占用</span><span class="metric-value">${percent}%</span></div>` +
        `<div class="meter" aria-hidden="true"><div class="meter-fill" style="width:${percent}%"></div></div>` +
        `<div class="metric-row"><span>本轮模型调用</span><span class="metric-value">${escapeHtml(context.turn_llm_calls || 0)}</span></div>` +
        `<div class="metric-row"><span>总模型调用</span><span class="metric-value">${escapeHtml(context.total_llm_calls || 0)}</span></div>` +
        `<div class="metric-row"><span>估算成本</span><span class="metric-value">$${escapeHtml(context.estimated_cost_usd || 0)}</span></div>`;
    }

    function renderContext(context, workspace) {
      const root = document.getElementById("contextList");
      root.innerHTML =
        `<div class="context-row"><span class="todo-check checked"></span><span>工作区：${escapeHtml(workspace || "")}</span></div>` +
        `<div class="context-row"><span class="todo-check ${context.schema_version ? "checked" : ""}"></span><span>Trace：${escapeHtml(context.schema_version || "暂无")}</span></div>` +
        `<div class="context-row"><span class="todo-check ${context.session_trace_path ? "checked" : ""}"></span><span>SessionTrace：${escapeHtml(context.session_trace_path || "暂无")}</span></div>`;
    }

    function renderRunTimeline(cards) {
      const root = document.getElementById("runTimeline");
      const data = cards || (liveRunEvents.length ? liveRunEvents : stateTimeline);
      if (!root) {
        renderTimelinePanel(data);
        return;
      }
      root.innerHTML = "";
      if (!data.length) {
        root.innerHTML = `<div class="subtle">暂无执行过程</div>`;
        return;
      }
      for (const card of data) {
        root.appendChild(renderProgressCard(card));
      }
    }

    function renderProgressCard(card) {
      const row = document.createElement("div");
      row.className = "timeline-row";
      row.innerHTML =
        `<div class="timeline-time">${escapeHtml(card.kind || "event")}</div>` +
        `<div class="timeline-card ${card.status === "error" ? "error" : card.kind === "notice" ? "warning" : ""}">` +
        `<strong>${escapeHtml(card.title || "")}</strong>` +
        `<p>${escapeHtml(card.summary || "")}</p></div>`;
      row.addEventListener("click", () => {
        document.getElementById("tracePanel").textContent = JSON.stringify(card.details || {}, null, 2);
        openModal("session_trace");
      });
      return row;
    }

    function renderModalTabs() {
      renderTimelinePanel();
      renderExceptionPanel();
      document.getElementById("tracePanel").textContent = JSON.stringify(traceTabs.session_trace || {}, null, 2);
      if (!document.activeElement.closest("#settingsPanel")) renderSettingsPanel();
    }

    function renderSettingsPanel() {
      const settings = traceTabs.settings || {};
      const apiBase = document.getElementById("settingsApiBase");
      const apiKey = document.getElementById("settingsApiKey");
      apiBase.value = settings.api_base || "";
      apiBase.placeholder = "LiteLLM provider 默认地址";
      document.getElementById("settingsModel").value = settings.model || "未配置";
      apiKey.value = "";
      apiKey.placeholder = settings.api_key_configured ? "已在本地配置，输入新 key 可覆盖" : "未配置";
      document.getElementById("settingsTimeout").value = `${settings.request_timeout_seconds || 90} 秒`;
    }

    async function saveSettings() {
      const button = document.getElementById("settingsSaveBtn");
      button.disabled = true;
      button.textContent = "保存中";
      const response = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          api_base: document.getElementById("settingsApiBase").value.trim(),
          model: document.getElementById("settingsModel").value.trim(),
          api_key: document.getElementById("settingsApiKey").value.trim(),
          request_timeout_seconds: document.getElementById("settingsTimeout").value.trim(),
        }),
      });
      const payload = await response.json();
      button.disabled = false;
      button.textContent = response.ok ? "已保存" : "保存失败";
      document.getElementById("chatStatus").textContent = payload.status || "settings";
      if (response.ok) {
        document.getElementById("settingsApiKey").value = "";
        await loadState();
      }
      setTimeout(() => { button.textContent = "保存到本地设置"; }, 1400);
    }

    function renderTimelinePanel(cards) {
      const root = document.getElementById("timelinePanel");
      root.innerHTML = "";
      const data = cards || (liveRunEvents.length ? liveRunEvents : traceTabs.timeline || stateTimeline || []);
      for (const card of data) {
        root.appendChild(renderProgressCard(card));
      }
      if (!data.length) root.innerHTML = `<div class="subtle">暂无记录</div>`;
    }

    function renderExceptionPanel() {
      const root = document.getElementById("exceptionPanel");
      const exception = traceTabs.exception || {};
      root.innerHTML =
        `<div class="timeline-row"><div class="timeline-time">类别</div><div class="timeline-card ${exception.error ? "error" : ""}"><strong>${exception.error ? "本轮遇到问题" : "暂无异常"}</strong><p>${escapeHtml(exception.notice || "暂无异常。")}</p></div></div>`;
    }

    function renderConversationItem(item) {
      if (item.role === "thinking") {
        const node = document.createElement("section");
        node.className = "thinking-card";
        node.dataset.id = item.id || "";
        node.innerHTML =
          `<div class="thinking-head"><span class="thinking-title">Ming 正在思考</span><span class="thinking-dots" aria-hidden="true"><span></span><span></span><span></span></span></div>` +
          `<p class="thinking-caption">${escapeHtml(item.content || "正在整理上下文和下一步动作。")}</p>`;
        return node;
      }
      if (item.role === "process") {
        return renderProcessConversationCard(item.content || {});
      }
      if (item.role === "tool") {
        const event = item.content || {};
        const toolName = event.tool || toolNameFromEvent(event) || event.stage || "tool";
        const status = event.status || "running";
        const statusLabel = status === "error" ? "异常" : status === "done" ? "完成" : "已发起";
        const node = document.createElement("div");
        node.className = "tool-card";
        node.innerHTML =
          `<div class="tool-card-head"><span class="tool-icon">tool</span><span class="tool-name">${escapeHtml(toolName)}</span><span>${escapeHtml(event.message || "")}</span><span class="tool-status ${escapeHtml(status)}">${escapeHtml(statusLabel)}</span><span class="tool-expand">▼</span></div>` +
          `<div class="tool-card-body"><p>${escapeHtml(event.detail || "暂无详情")}</p></div>`;
        node.querySelector(".tool-card-head").addEventListener("click", () => toggleToolCard(node));
        return node;
      }
      if (item.role === "notice" || item.role === "system") {
        const node = document.createElement("section");
        node.className = "notice";
        node.innerHTML =
          `<div class="notice-title">Ming 暂停了</div><p>${escapeHtml(item.content || "")}</p>` +
          `<button class="open-detail" type="button" data-modal="exception">为什么会这样 →</button>`;
        node.querySelector("[data-modal]").addEventListener("click", () => openModal("exception"));
        return node;
      }
      if (item.role === "verdict") {
        return renderVerdictCard(item.content || {});
      }
      const node = document.createElement("article");
      const role = item.role === "user" ? "user" : "ming";
      node.className = `message ${role}`;
      const content = item.content || "";
      const status = role === "ming" ? classifyReplyStatus(content) : null;
      const rendered = renderMarkdown(content);
      if (!rendered.trim() && !status) return null;
      node.innerHTML =
        `<div class="message-label">${role === "user" ? "You" : "Ming"}</div>` +
        `<div class="bubble">` +
        `${status ? `<div class="reply-status ${status.kind}">${escapeHtml(status.label)}</div>` : ""}` +
        `<div class="bubble-content">${rendered}</div>` +
        `</div>`;
      return node;
    }

    function renderProcessConversationCard(event) {
      const card = formatRunEvent(event);
      const hasDetail = Boolean(event.detail && event.detail !== card.summary);
      const node = document.createElement("section");
      node.className = `process-card ${card.status || "running"} ${hasDetail ? "has-detail" : ""}`;
      node.innerHTML =
        `<div class="process-head"><span class="process-dot"></span><span class="process-title">${escapeHtml(card.title)}</span><p class="process-summary">${escapeHtml(card.summary || "")}</p><span class="process-meta">${escapeHtml(card.meta || "")}</span></div>` +
        `${hasDetail ? `<div class="process-body">${escapeHtml(event.detail || "")}</div>` : ""}`;
      if (hasDetail) {
        node.querySelector(".process-head").addEventListener("click", () => node.classList.toggle("open"));
      }
      return node;
    }

    function classifyReplyStatus(content) {
      const value = text(content).trim();
      if (!value) return null;
      if (value.startsWith("已完成")) return { kind: "done", label: "已完成" };
      if (value.startsWith("未完成") || value.includes("暂停了本轮执行")) return { kind: "blocked", label: "未完成" };
      if (value.startsWith("待你确认") || value.includes("需要你的判断")) return { kind: "pending", label: "待你确认" };
      if (/(方案|实施步骤|^\\d+\\.\\s|先.*然后)/.test(value) && !/(已创建|已写入|已启动|已验证|可访问)/.test(value)) {
        return { kind: "pending", label: "计划说明" };
      }
      return null;
    }

    function renderMarkdown(content) {
      const lines = text(content).split(/\\r?\\n/);
      const html = [];
      let paragraph = [];
      let listType = "";
      const closeParagraph = () => {
        if (!paragraph.length) return;
        html.push(`<p>${formatInline(paragraph.join(" "))}</p>`);
        paragraph = [];
      };
      const closeList = () => {
        if (!listType) return;
        html.push(`</${listType}>`);
        listType = "";
      };
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line) {
          closeParagraph();
          closeList();
          continue;
        }
        if (isMarkdownSeparator(line)) {
          closeParagraph();
          closeList();
          continue;
        }
        const heading = line.match(/^#{1,3}\\s+(.+)$/);
        if (heading) {
          closeParagraph();
          closeList();
          html.push(`<h3>${formatInline(heading[1])}</h3>`);
          continue;
        }
        const ordered = line.match(/^\\d+\\.\\s+(.+)$/);
        if (ordered) {
          closeParagraph();
          if (listType !== "ol") {
            closeList();
            html.push("<ol>");
            listType = "ol";
          }
          html.push(`<li>${formatInline(ordered[1])}</li>`);
          continue;
        }
        const unordered = line.match(/^[-*]\\s+(.+)$/);
        if (unordered) {
          closeParagraph();
          if (listType !== "ul") {
            closeList();
            html.push("<ul>");
            listType = "ul";
          }
          html.push(`<li>${formatInline(unordered[1])}</li>`);
          continue;
        }
        closeList();
        paragraph.push(line);
      }
      closeParagraph();
      closeList();
      return html.join("");
    }

    function isMarkdownSeparator(line) {
      return /^(-{3,}|\\*{3,}|_{3,})$/.test(line);
    }

    function formatInline(value) {
      return text(value).split(/(`[^`]+`)/g).map(part => {
        if (part.startsWith("`") && part.endsWith("`")) {
          return `<code>${escapeHtml(part.slice(1, -1))}</code>`;
        }
        return escapeHtml(part);
      }).join("");
    }

    function renderVerdictCard(payload) {
      const node = document.createElement("div");
      node.className = "verdict-card";
      node.innerHTML =
        `<div class="verdict-head"><div class="verdict-head-title">需要你的判断</div><div class="verdict-head-topic">${escapeHtml(payload.topic || "Ming 检测到两个方向都成立，需要你选择。")}</div></div>` +
        `<div class="verdict-columns"><div class="verdict-col"><div class="verdict-col-title">观点 A</div><ul><li>${escapeHtml(payload.a || "继续当前策略")}</li></ul></div><div class="verdict-col"><div class="verdict-col-title">观点 B</div><ul><li>${escapeHtml(payload.b || "切换策略")}</li></ul></div></div>` +
        `<div class="verdict-analysis"><strong>分歧根源：</strong>${escapeHtml(payload.analysis || "任务目标存在多种合理解释。")}</div>` +
        `<div class="verdict-actions"><button class="verdict-btn primary" type="button">采纳观点 A</button><button class="verdict-btn secondary" type="button">采纳观点 B</button><button class="verdict-btn tertiary" type="button">我有不同看法...</button></div>`;
      return node;
    }

    function toggleToolCard(node) {
      node.classList.toggle("open");
    }

    function renderConversation() {
      const root = document.getElementById("conversation");
      root.innerHTML = "";
      if (currentConversationMode === "history") {
        renderHistoryConversation(root);
        return;
      }
      if (!conversation.length) {
        conversation.push({ role: "ming", content: "有什么需要思考或解决的问题吗？我们可以一起梳理。" });
      }
      for (const item of conversation) {
        const node = renderConversationItem(item);
        if (node) root.appendChild(node);
      }
      root.scrollTop = root.scrollHeight;
    }

    function renderHistoryConversation(root) {
      const session = allSessions.find(item => item.turn_id === selectedSessionId);
      if (!session) {
        root.innerHTML = `<section class="notice"><div class="notice-title">历史会话不存在</div><p>这条记录可能已经被清理。</p></section>`;
        return;
      }
      const header = document.createElement("section");
      header.className = "notice";
      header.innerHTML =
        `<div class="notice-title">正在查看历史会话</div>` +
        `<p>${escapeHtml(session.title || "未命名会话")}<br><span class="subtle">${escapeHtml(session.started_at || "")}</span></p>`;
      root.appendChild(header);
      const items = session.conversation || [];
      if (!items.length) {
        const empty = document.createElement("section");
        empty.className = "notice";
        empty.innerHTML = `<div class="notice-title">暂无可展示消息</div><p>该 checkpoint 没有保存对话正文。</p>`;
        root.appendChild(empty);
      }
      for (const item of items) {
        const node = renderConversationItem(item);
        if (node) root.appendChild(node);
      }
      root.scrollTop = root.scrollHeight;
    }

    function selectSession(turnId) {
      const session = allSessions.find(item => item.turn_id === turnId);
      if (!session) return;
      selectedSessionId = turnId;
      currentConversationMode = "history";
      renderSessions(allSessions);
      renderConversation();
    }

    function switchToLiveConversation() {
      if (currentConversationMode === "live") return;
      currentConversationMode = "live";
      selectedSessionId = "";
      renderSessions(allSessions);
      renderConversation();
    }

    function appendConversation(role, content) {
      const item = { role, content };
      conversation.push(item);
      if (conversation.length > 80) conversation.shift();
      renderConversation();
      return item;
    }

    function appendConversationEvent(role, event) {
      const key = event.seq ? `${role}-${event.seq}` : `${role}-${event.stage}-${event.turn_id}-${event.message}`;
      if (conversation.some(item => item.key === key)) return;
      conversation.push({ role, content: event, key });
      if (conversation.length > 80) conversation.shift();
      renderConversation();
    }

    function beginThinking(message) {
      activeThinkingId = `thinking-${Date.now()}`;
      appendConversation("thinking", message || "Ming 正在思考下一步。").id = activeThinkingId;
    }

    function finishThinking() {
      if (!activeThinkingId) return;
      const index = conversation.findIndex(item => item.role === "thinking" && item.id === activeThinkingId);
      if (index >= 0) {
        conversation.splice(index, 1);
        renderConversation();
      }
      activeThinkingId = "";
    }

    async function submitChat(event) {
      event.preventDefault();
      const input = document.getElementById("messageInput");
      const message = input.value.trim();
      if (!message) return;
      switchToLiveConversation();
      appendConversation("user", message);
      beginThinking("已收到消息，正在整理上下文、选择工具和执行路径。");
      setChatRunning(true, "submitting");
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message }),
        });
        const payload = await response.json();
        if (response.status === 202) {
          input.value = "";
          setChatRunning(true, `running ${payload.turn_id}`);
        } else {
          finishThinking();
          setChatRunning(false, payload.status || "error");
          appendConversation("notice", payload.error || payload.status || "submit failed");
        }
      } catch (error) {
        finishThinking();
        setChatRunning(false, "error");
        appendConversation("notice", `提交失败：${error}`);
      }
    }

    async function stopTurn() {
      setChatRunning(true, "stopping");
      const response = await fetch("/api/turns/current/stop", { method: "POST" });
      const payload = await response.json();
      if (response.status === 200) {
        appendConversation("notice", "已停止本轮思考。");
      }
      finishThinking();
      setChatRunning(false, payload.status || "idle");
    }

    function setChatRunning(running, label) {
      document.getElementById("chatStatus").textContent = label;
      document.getElementById("sendBtn").disabled = running;
      document.getElementById("stopTurnBtn").disabled = !running;
    }

    function formatRunEvent(event) {
      const labels = {
        submitted: "收到任务",
        context: "整理上下文",
        route: "选择策略",
        llm: "模型思考",
        thought: "模型结果",
        tool: "工具执行",
        verify: "校验结果",
        done: "保存进度",
        final: "最终回复",
        error: "遇到问题",
        cancelled: "已停止",
      };
      const stage = event.stage || event.type || "event";
      return {
        id: `live-${event.seq || Date.now()}-${stage}`,
        kind: stage,
        title: labels[stage] || stage,
        status: stage === "error" ? "error" : ["done", "final"].includes(stage) ? "done" : "running",
        summary: stage === "thought" ? (event.detail || event.message || "") : (event.message || event.detail || ""),
        meta: event.turn_id ? event.turn_id.slice(-6) : "",
        collapsed: stage === "tool",
        details: event,
      };
    }

    function toolNameFromEvent(event) {
      const source = `${event.message || ""} ${event.detail || ""}`;
      const match = source.match(/(?:执行工具|工具)\\s+([A-Za-z0-9_\\-]+)/);
      return match ? match[1] : "";
    }

    function appendRunEvent(event) {
      const card = formatRunEvent(event);
      if (!liveRunEvents.some(item => item.id === card.id)) liveRunEvents.push(card);
      if (liveRunEvents.length > 80) liveRunEvents.shift();
      renderRunTimeline();
    }

    function handleConversationEvent(event) {
      appendRunEvent(event);
      const processStages = ["submitted", "context", "route", "llm", "thought", "verify", "done"];
      if (processStages.includes(event.stage)) {
        appendConversationEvent("process", event);
      }
      if (event.stage === "tool") {
        appendConversationEvent("tool", event);
        return;
      }
      if (event.stage === "final") {
        finishThinking();
        appendConversation("ming", event.detail || event.message);
        setChatRunning(false, "ready");
        loadState();
        return;
      }
      if (event.stage === "error") {
        finishThinking();
        appendConversation("notice", event.detail || event.message);
        setChatRunning(false, "error");
        loadState();
        return;
      }
      if (event.stage === "cancelled") {
        finishThinking();
        appendConversation("notice", event.message);
        setChatRunning(false, "cancelled");
        loadState();
      }
    }

    function connectLiveEvents() {
      const status = document.getElementById("liveStatus");
      const source = new EventSource("/api/events");
      const stages = ["submitted", "context", "route", "llm", "thought", "tool", "verify", "done", "final", "error", "cancelled", "heartbeat"];
      source.onopen = () => { if (status) status.textContent = "connected"; };
      source.onerror = () => { if (status) status.textContent = "reconnecting"; };
      for (const stage of stages) {
        source.addEventListener(stage, event => {
          if (!event.data) return;
          let payload;
          try {
            payload = JSON.parse(event.data);
          } catch {
            return;
          }
          if (payload.stage !== "heartbeat") {
            appendLiveEvent(payload);
            handleConversationEvent(payload);
          }
        });
      }
    }

    function appendLiveEvent(event) {
      liveEvents.unshift(event);
      if (liveEvents.length > 20) liveEvents.pop();
      renderLiveEvents();
    }

    function renderLiveEvents() {
      const root = document.getElementById("liveEvents");
      if (!root) return;
      root.innerHTML = "";
      if (!liveEvents.length) {
        root.innerHTML = `<div class="subtle">等待下一条 live event。</div>`;
        return;
      }
      for (const event of liveEvents) {
        const node = document.createElement("div");
        node.className = "live-event";
        node.innerHTML =
          `<strong>${escapeHtml(event.stage)} · ${escapeHtml(event.message)}</strong>` +
          `<div class="subtle">${escapeHtml(event.turn_id || "no turn")} · #${escapeHtml(event.seq)}</div>`;
        root.appendChild(node);
      }
    }

    function openModal(tabName) {
      modal.classList.add("open");
      setTab(tabName || "timeline");
    }

    function setTab(tabName) {
      tabButtons.forEach(b => b.classList.toggle("active", b.dataset.tab === tabName));
      panels.forEach(p => p.classList.toggle("hidden", p.dataset.panel !== tabName));
    }

    function escapeHtml(value) {
      return text(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    function text(value) {
      return value === undefined || value === null ? "" : String(value);
    }

    document.getElementById("toggleSidebar").addEventListener("click", () => {
      appShell.classList.toggle("sidebar-open");
      document.getElementById("toggleSidebar").classList.toggle("active", appShell.classList.contains("sidebar-open"));
    });
    document.getElementById("toggleProcess").addEventListener("click", event => {
      event.stopPropagation();
      appShell.classList.toggle("process-closed");
      document.getElementById("toggleProcess").classList.toggle("active", !appShell.classList.contains("process-closed"));
    });
    document.querySelectorAll("[data-modal]").forEach(btn => {
      btn.addEventListener("click", () => openModal(btn.dataset.modal));
    });
    tabButtons.forEach(b => b.addEventListener("click", () => setTab(b.dataset.tab)));
    closeModal.addEventListener("click", () => modal.classList.remove("open"));
    modal.addEventListener("click", event => { if (event.target === modal) modal.classList.remove("open"); });
    window.addEventListener("keydown", event => { if (event.key === "Escape") modal.classList.remove("open"); });
    document.getElementById("messageInput").addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        document.getElementById("chatForm").requestSubmit();
      }
    });
    document.getElementById("chatForm").addEventListener("submit", submitChat);
    document.getElementById("stopTurnBtn").addEventListener("click", stopTurn);
    document.getElementById("settingsSaveBtn").addEventListener("click", saveSettings);
    renderConversation();
    renderLiveEvents();
    connectLiveEvents();
    loadState();
    setInterval(loadState, 1800);
  </script>
</body>
</html>
"""


class TraceConsoleState:
    """Build a UI-friendly snapshot from local Ming trace/checkpoint files."""

    def __init__(self, workspace_root: str | Path | None = None):
        self.workspace_root = Path(workspace_root or Path.cwd())
        self.ming_root = self.workspace_root / ".ming"

    def load(self) -> dict[str, Any]:
        checkpoint_path = self._latest_file(self.ming_root / "checkpoints", "*/checkpoint.json")
        checkpoint = self._read_json(checkpoint_path)
        checkpoint_turn_id = checkpoint.get("turn_id", "")
        session_trace_path = (
            self._session_trace_for_turn(checkpoint_turn_id)
            if checkpoint_turn_id
            else self._latest_session_trace()
        )
        session = self._read_json(session_trace_path)

        turn = self._latest_turn(session)
        turn_id = turn.get("turn_id") or checkpoint.get("turn_id") or ""
        task_text = turn.get("user_input") or checkpoint.get("name") or "暂无任务"
        state = self._agent_state(turn)
        timeline = self._build_timeline(turn)
        sessions = self._build_sessions(session, checkpoint, checkpoint_path)
        artifacts = self._build_artifacts(session_trace_path, checkpoint_path, checkpoint)
        context = self._build_context(session, turn, artifacts, checkpoint)
        config_snapshot = self._build_config_snapshot()
        trace_tabs = self._build_trace_tabs(session, turn, timeline, artifacts, config_snapshot)

        process_panel = {
            "todo": checkpoint.get("todo") or {"items": []},
            "artifacts": artifacts,
            "context": context,
            "locked": False,
        }

        return {
            "schema_version": WORKBENCH_SCHEMA_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "workspace": str(self.workspace_root),
            "sessions": sessions,
            "current_task": {
                "turn_id": turn_id,
                "text": task_text,
                "started_at": turn.get("timestamp") or checkpoint.get("created_at") or "",
                "status": state,
            },
            "agent": {
                "state": state,
                "mode": turn.get("execution", "single"),
                "summary": self._agent_summary(turn, timeline),
                "thought_summary": self._thought_summary(turn),
                "last_event": timeline[-1]["title"] if timeline else "暂无事件",
            },
            "process_panel": process_panel,
            "trace_tabs": trace_tabs,
            "settings": config_snapshot,
            "todo": process_panel["todo"],
            "timeline": timeline,
            "subagents": self._subagents(turn, state),
            "artifacts": artifacts,
        }

    def _latest_turn(self, session: dict[str, Any]) -> dict[str, Any]:
        turns = session.get("turns") or []
        return turns[-1] if turns else {}

    def _build_sessions(
        self,
        session: dict[str, Any],
        checkpoint: dict[str, Any],
        checkpoint_path: Path | None,
    ) -> list[dict[str, Any]]:
        rows_by_turn: dict[str, dict[str, Any]] = {}
        checkpoint_paths = sorted(
            self.ming_root.glob("checkpoints/*/checkpoint.json"),
            key=lambda path: path.stat().st_mtime,
        )
        for path in checkpoint_paths[-24:]:
            payload = self._read_json(path)
            turn_id = payload.get("turn_id", "")
            if not turn_id:
                continue
            title = self._public_title_from_checkpoint(payload)
            rows_by_turn[turn_id] = {
                "turn_id": turn_id,
                "title": self._shorten(title or turn_id or "未命名会话", 48),
                "started_at": payload.get("created_at", ""),
                "checkpoint_path": self._path_text(path),
                "conversation": self._conversation_from_checkpoint(payload),
            }
        for turn in session.get("turns") or []:
            turn_id = turn.get("turn_id", "")
            if not turn_id or turn_id in rows_by_turn:
                continue
            rows_by_turn[turn_id] = {
                "turn_id": turn_id,
                "title": self._shorten(turn.get("user_input", "") or "未命名会话", 48),
                "started_at": turn.get("timestamp", ""),
                "checkpoint_path": "",
                "conversation": self._conversation_from_turn(turn),
            }
        if not rows_by_turn and checkpoint:
            turn_id = checkpoint.get("turn_id", "")
            title = self._public_title_from_checkpoint(checkpoint)
            rows_by_turn[turn_id] = {
                "turn_id": turn_id,
                "title": title or turn_id or "未命名会话",
                "started_at": checkpoint.get("created_at", ""),
                "checkpoint_path": self._path_text(checkpoint_path),
                "conversation": self._conversation_from_checkpoint(checkpoint),
            }
        rows = sorted(
            rows_by_turn.values(),
            key=lambda item: item.get("started_at", ""),
        )
        return rows[-24:][::-1]

    def _public_title_from_checkpoint(self, checkpoint: dict[str, Any]) -> str:
        messages = checkpoint.get("messages") or []
        for message in messages:
            if message.get("role") != "user":
                continue
            content = str(message.get("content") or "")
            if content and not self._is_internal_user_message(content):
                return content
        name = str(checkpoint.get("name") or "")
        if name and not self._is_internal_user_message(name):
            return name
        return str(checkpoint.get("messages_summary") or "")

    def _conversation_from_checkpoint(self, checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self._conversation_from_messages(checkpoint.get("messages") or [])
        title = self._public_title_from_checkpoint(checkpoint)
        if rows:
            if title and not self._conversation_starts_with_title(rows, title):
                rows.insert(0, {"role": "user", "content": self._clip_history_content(title)})
            return rows
        if not title:
            return []
        return [{"role": "user", "content": self._clip_history_content(title)}]

    def _conversation_starts_with_title(self, rows: list[dict[str, Any]], title: str) -> bool:
        if not rows:
            return False
        first = rows[0]
        if first.get("role") != "user":
            return False
        content = str(first.get("content") or "")
        normalized_title = " ".join(title.split())
        normalized_content = " ".join(content.split())
        return normalized_title in normalized_content or normalized_content in normalized_title

    def _conversation_from_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen_public_user = False
        for message in messages:
            role = message.get("role", "")
            content = str(message.get("content") or "")
            if role == "user" and content:
                if self._is_internal_user_message(content):
                    continue
                seen_public_user = True
                rows.append({"role": "user", "content": self._clip_history_content(content)})
            elif role == "assistant":
                if not seen_public_user:
                    continue
                tool_calls = message.get("tool_calls") or []
                for call in tool_calls:
                    function = call.get("function") or {}
                    rows.append(
                        {
                            "role": "tool",
                            "content": {
                                "tool": function.get("name") or "tool",
                                "message": "历史工具调用",
                                "detail": self._clip_history_content(function.get("arguments") or ""),
                                "status": "done",
                            },
                        }
                    )
                if content:
                    rows.append({"role": "ming", "content": self._clip_history_content(content)})
            elif role == "tool" and content:
                if not seen_public_user:
                    continue
                rows.append(
                    {
                        "role": "tool",
                        "content": {
                            "tool": message.get("name") or message.get("tool_call_id") or "tool",
                            "message": "历史工具结果",
                            "detail": self._clip_history_content(content),
                            "status": "done",
                        },
                    }
                )
        return rows[-40:]

    def _conversation_from_turn(self, turn: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        user_input = turn.get("user_input", "")
        final_output = turn.get("final_output", "")
        if user_input:
            rows.append({"role": "user", "content": self._clip_history_content(user_input)})
        if final_output:
            rows.append({"role": "ming", "content": self._clip_history_content(final_output)})
        return rows

    def _is_internal_user_message(self, content: str) -> bool:
        markers = [
            "工具调用策略失败，需要换一种执行方式继续",
            "你刚才只给了计划，没有实际使用工具",
            "T3 核验失败：最终答复与工具证据不一致",
            "T1 CoVe 自检",
        ]
        return any(marker in content for marker in markers)

    def _clip_history_content(self, value: Any, max_chars: int = 6000) -> str:
        text = str(value)
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "…"

    def _build_timeline(self, turn: dict[str, Any]) -> list[dict[str, Any]]:
        if not turn:
            return [
                {
                    "id": "empty",
                    "kind": "empty",
                    "title": "等待首个 trace",
                    "status": "idle",
                    "summary": "运行一次 Ming 任务后，这里会展示 agent-loop 的步骤。",
                    "collapsed": False,
                    "details": {},
                }
            ]

        cards: list[dict[str, Any]] = [
            {
                "id": "task",
                "kind": "task",
                "title": "收到用户任务",
                "status": "done",
                "summary": turn.get("user_input", ""),
                "collapsed": False,
                "details": {
                    "turn_id": turn.get("turn_id"),
                    "started_at": turn.get("timestamp"),
                },
            }
        ]

        gate = turn.get("gate") or {}
        if gate.get("mode"):
            cards.append(
                {
                    "id": "gate",
                    "kind": "route",
                    "title": f"选择策略：{gate['mode']}",
                    "status": "done",
                    "summary": ", ".join(gate.get("triggered_rules", [])) or "无触发规则，走默认执行路径",
                    "collapsed": False,
                    "details": gate,
                }
            )

        single = turn.get("single_agent") or {}
        for step in single.get("steps", []):
            if step.get("response_content_length"):
                cards.append(
                    {
                        "id": f"thinking-{step.get('step_id')}",
                        "kind": "thinking",
                        "title": f"模型思考，第 {step.get('iteration')} 轮",
                        "status": "done" if step.get("is_final") else "running",
                        "summary": f"输出 {step.get('response_content_length', 0)} chars",
                        "collapsed": True,
                        "details": step,
                    }
                )
            for tc in step.get("tool_calls", []):
                cards.append(
                    {
                        "id": tc.get("id") or f"tool-{step.get('step_id', 0)}",
                        "kind": "tool",
                        "title": f"工具执行：{tc.get('name', 'unknown')}",
                        "status": "error" if tc.get("result_is_error") else "done",
                        "summary": (
                            f"loop={tc.get('loop_status', 'ok')}，"
                            f"输出 {tc.get('result_output_length', 0)} chars"
                        ),
                        "collapsed": True,
                        "details": {"tool_call": tc, "step": step},
                    }
                )

        adversarial = turn.get("adversarial") or {}
        if adversarial:
            cards.append(
                {
                    "id": "adversarial",
                    "kind": "verdict",
                    "title": "Ming 的判断分歧",
                    "status": "done",
                    "summary": (
                        f"观点 A {adversarial.get('alpha_output_length', 0)} chars，"
                        f"观点 B {adversarial.get('beta_output_length', 0)} chars，"
                        f"裁决 {adversarial.get('gamma_phase1_consistency', 'unknown')}"
                    ),
                    "collapsed": False,
                    "details": adversarial,
                }
            )

        if turn.get("error"):
            cards.append(
                {
                    "id": "exception",
                    "kind": "notice",
                    "title": "本轮遇到问题",
                    "status": "needs_attention",
                    "summary": self._shorten(turn.get("error", ""), 220),
                    "collapsed": False,
                    "details": {"error": turn.get("error")},
                }
            )

        if turn.get("final_output"):
            cards.append(
                {
                    "id": "final",
                    "kind": "final",
                    "title": "最终回复",
                    "status": "done",
                    "summary": self._shorten(turn.get("final_output", ""), 220),
                    "collapsed": False,
                    "details": {"final_output": turn.get("final_output", "")},
                }
            )

        return cards

    def _subagents(self, turn: dict[str, Any], state: str) -> list[dict[str, Any]]:
        adversarial = turn.get("adversarial") or {}
        is_adv = turn.get("execution") == "adversarial"
        main_status = "idle" if state == "idle" else state
        inactive = "本轮未触发对抗分支"
        return [
            {
                "name": "Ming Main",
                "role": "主循环",
                "status": main_status,
                "summary": self._main_lane_summary(turn),
            },
            {
                "name": "Alpha",
                "role": "观点 A",
                "status": "observed" if is_adv else "idle",
                "summary": f"{adversarial.get('alpha_output_length', 0)} chars" if is_adv else inactive,
            },
            {
                "name": "Beta",
                "role": "观点 B",
                "status": "observed" if is_adv else "idle",
                "summary": f"{adversarial.get('beta_output_length', 0)} chars" if is_adv else inactive,
            },
            {
                "name": "Gamma",
                "role": "裁决",
                "status": "observed" if is_adv else "idle",
                "summary": adversarial.get("gamma_phase1_consistency", inactive) if is_adv else inactive,
            },
        ]

    def _build_artifacts(
        self,
        session_trace_path: Path | None,
        checkpoint_path: Path | None,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        session_trace_text = self._path_text(session_trace_path)
        return {
            "trace_path": session_trace_text,
            "session_trace_path": session_trace_text,
            "checkpoint_path": self._path_text(checkpoint_path),
            "notepad_path": checkpoint.get("notepad_path", ""),
            "changed_files": checkpoint.get("changed_files", []),
            "messages_summary": checkpoint.get("messages_summary", ""),
        }

    def _build_context(
        self,
        session: dict[str, Any],
        turn: dict[str, Any],
        artifacts: dict[str, Any],
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        metrics = session.get("session_metrics") or {}
        turn_metrics = turn.get("turn_metrics") or {}
        fallback_tokens = self._estimate_checkpoint_tokens(checkpoint)
        return {
            "session_trace_path": artifacts["session_trace_path"],
            "schema_version": session.get("schema_version", ""),
            "total_turns": metrics.get("total_turns", 0),
            "total_llm_calls": metrics.get("total_llm_calls", 0),
            "total_prompt_tokens": metrics.get("total_prompt_tokens", 0) or fallback_tokens,
            "total_completion_tokens": metrics.get("total_completion_tokens", 0),
            "turn_llm_calls": turn_metrics.get("total_llm_calls", 0),
            "turn_prompt_tokens": turn_metrics.get("total_prompt_tokens", 0) or fallback_tokens,
            "turn_completion_tokens": turn_metrics.get("total_completion_tokens", 0),
            "turn_latency_ms": turn_metrics.get("total_latency_ms", 0),
            "estimated_cost_usd": turn_metrics.get("estimated_cost_usd", 0.0),
        }

    def _build_trace_tabs(
        self,
        session: dict[str, Any],
        turn: dict[str, Any],
        timeline: list[dict[str, Any]],
        artifacts: dict[str, Any],
        config_snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "timeline": timeline,
            "exception": {
                "error": turn.get("error", ""),
                "notice": self._exception_notice(turn),
            },
            "session_trace": {
                "path": artifacts["session_trace_path"],
                "schema_version": session.get("schema_version", ""),
                "session_id": session.get("session_id", ""),
            },
            "settings": config_snapshot
            | {
                "trace_model": (session.get("agent") or {}).get("model", ""),
                "agent_version": (session.get("agent") or {}).get("version", ""),
            },
        }

    def _build_config_snapshot(self) -> dict[str, Any]:
        config = load_config()
        return {
            "model": config.llm.model,
            "fallback_models": config.llm.fallback_models,
            "api_base": config.llm.api_base,
            "api_key_configured": bool(config.llm.api_key),
            "temperature": config.llm.temperature,
            "max_tokens": config.llm.max_tokens,
            "request_timeout_seconds": config.llm.request_timeout_seconds,
            "max_seconds": config.agent.max_seconds,
        }

    def _exception_notice(self, turn: dict[str, Any]) -> str:
        if turn.get("error"):
            return "本轮已经保存 trace/checkpoint。可查看详情后调整任务、换工具或继续。"
        single = turn.get("single_agent") or {}
        if single.get("l5_ceiling_hit"):
            return f"触发执行上限：{single['l5_ceiling_hit']}。Ming 已暂停，避免空转。"
        return "暂无异常。"

    def _agent_state(self, turn: dict[str, Any]) -> str:
        if not turn:
            return "idle"
        if turn.get("error"):
            return "blocked"
        if turn.get("final_output"):
            return "completed"
        return "running"

    def _agent_summary(self, turn: dict[str, Any], timeline: list[dict[str, Any]]) -> str:
        if not turn:
            return "还没有可展示的 Ming 运行记录。"
        if turn.get("final_output"):
            return self._shorten(turn.get("final_output", ""), 160)
        return timeline[-1]["summary"] if timeline else "正在等待下一步事件。"

    def _thought_summary(self, turn: dict[str, Any]) -> str:
        if not turn:
            return "暂无可公开思路摘要。"
        feedback = turn.get("feedback") or {}
        if feedback.get("tier_signal"):
            return f"tier={feedback['tier_signal']} automaticity={feedback.get('automaticity_after', '?')}"
        return "本轮没有额外观察记录；打开详情可查看结构化事件。"

    def _main_lane_summary(self, turn: dict[str, Any]) -> str:
        if not turn:
            return "等待任务。"
        single = turn.get("single_agent") or {}
        tool_count = sum(len(step.get("tool_calls", [])) for step in single.get("steps", []))
        step_count = len(single.get("steps", []))
        return f"已记录 {step_count} 步，{tool_count} 个工具调用。"

    def _latest_file(self, root: Path, pattern: str) -> Path | None:
        if not root.exists():
            return None
        files = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime)
        return files[-1] if files else None

    def _latest_session_trace(self) -> Path | None:
        candidates = [
            self._latest_file(self.ming_root / "session_traces", "*.json"),
            self._latest_file(self.ming_root / "traces", "*.json"),
        ]
        existing = [path for path in candidates if path is not None and path.exists()]
        if not existing:
            return None
        return sorted(existing, key=lambda path: path.stat().st_mtime)[-1]

    def _session_trace_for_turn(self, turn_id: str) -> Path | None:
        if not turn_id:
            return None
        for root in [self.ming_root / "session_traces", self.ming_root / "traces"]:
            if not root.exists():
                continue
            direct = root / f"{turn_id}.json"
            if direct.exists():
                return direct
            for path in sorted(root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
                payload = self._read_json(path)
                if any(turn.get("turn_id") == turn_id for turn in payload.get("turns") or []):
                    return path
        return None

    def _estimate_checkpoint_tokens(self, checkpoint: dict[str, Any]) -> int:
        messages = checkpoint.get("messages") or []
        total_chars = sum(len(str(message.get("content", ""))) for message in messages)
        return int(total_chars / 2.5) if total_chars else 0

    def _read_json(self, path: Path | None) -> dict[str, Any]:
        if not path or not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _path_text(self, path: Path | None) -> str:
        return str(path) if path else ""

    def _shorten(self, text: str, max_chars: int) -> str:
        clean = " ".join(str(text).split())
        if len(clean) <= max_chars:
            return clean
        return clean[: max_chars - 1] + "…"


class TraceConsoleApp:
    """Tiny stdlib HTTP app for the Ming Agent Workbench."""

    def __init__(self, workspace_root: str | Path | None = None, chat_runtime: Any | None = None):
        self.workspace_root = Path(workspace_root or Path.cwd())
        self.state_builder = TraceConsoleState(self.workspace_root)
        self.live_events = LiveEventStore(self.workspace_root / ".ming" / "live")
        self._chat_runtime = chat_runtime

    def state(self) -> dict[str, Any]:
        return self.state_builder.load()

    def state_json(self) -> str:
        return json.dumps(self.state(), ensure_ascii=False, indent=2)

    def render_index(self) -> str:
        return DEMO_INDEX_HTML

    def chat_runtime(self):
        if self._chat_runtime is None:
            self._chat_runtime = ChatRuntime(self.workspace_root, live_events=self.live_events)
        return self._chat_runtime

    def submit_chat(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        message = str(payload.get("message") or "").strip()
        if not message:
            return 400, {"status": "invalid", "error": "message is required"}
        result = self.chat_runtime().submit(message)
        if result.get("status") == "busy":
            return 409, result
        if result.get("status") == "invalid":
            return 400, result
        return 202, result

    def stop_current_turn(self) -> tuple[int, dict[str, Any]]:
        result = self.chat_runtime().stop()
        if result.get("status") == "idle":
            return 409, result
        return 200, result

    def save_settings(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        model = str(payload.get("model") or "").strip()
        if not model:
            return 400, {"status": "invalid", "error": "model is required"}

        timeout_seconds = self._parse_seconds(payload.get("request_timeout_seconds"), default=90)
        local_path = self.workspace_root / "config" / "local.yaml"
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_data: dict[str, Any] = {}
        if local_path.exists():
            try:
                local_data = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                return 500, {"status": "error", "error": "无法读取 config/local.yaml"}

        llm = dict(local_data.get("llm") or {})
        llm["model"] = model
        llm["api_base"] = str(payload.get("api_base") or "").strip()
        llm["request_timeout_seconds"] = timeout_seconds
        api_key = str(payload.get("api_key") or "").strip()
        if api_key:
            llm["api_key"] = api_key
        local_data["llm"] = llm

        try:
            local_path.write_text(
                yaml.safe_dump(local_data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
        except OSError:
            return 500, {"status": "error", "error": "无法写入 config/local.yaml"}

        return 200, {
            "status": "settings_saved",
            "path": str(local_path),
            "api_key_configured": bool(llm.get("api_key")),
        }

    def _parse_seconds(self, value: Any, default: int) -> int:
        text_value = str(value or "").strip()
        digits = "".join(ch for ch in text_value if ch.isdigit())
        if not digits:
            return default
        return max(1, min(600, int(digits)))

    def format_sse(self, event: dict[str, Any]) -> str:
        event_name = str(event.get("stage") or event.get("type") or "message")
        data = json.dumps(event, ensure_ascii=False)
        return f"id: {event.get('seq', 0)}\nevent: {event_name}\ndata: {data}\n\n"

    def default_event_start_seq(self) -> int:
        events = self.live_events.since(0)
        if not events:
            return 0
        return max(int(event.get("seq", 0)) for event in events)

    def event_stream(
        self,
        last_seq: int = 0,
        poll_seconds: float = 1.0,
        heartbeat_seconds: float = 10.0,
    ):
        last_heartbeat = time.monotonic()
        while True:
            events = self.live_events.since(last_seq)
            for event in events:
                last_seq = max(last_seq, int(event.get("seq", 0)))
                yield self.format_sse(event)
            if poll_seconds <= 0:
                return
            now = time.monotonic()
            if now - last_heartbeat >= heartbeat_seconds:
                last_heartbeat = now
                yield self.format_sse(
                    {
                        "seq": last_seq,
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "turn_id": "",
                        "stage": "heartbeat",
                        "message": "keep-alive",
                        "detail": "",
                        "type": "heartbeat",
                    }
                )
            time.sleep(poll_seconds)

    def serve(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        app = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                path = urlparse(self.path).path
                if path in {"/", "/index.html"}:
                    self._send(200, app.render_index(), "text/html; charset=utf-8")
                    return
                if path == "/api/state":
                    self._send(200, app.state_json(), "application/json; charset=utf-8")
                    return
                if path == "/api/events":
                    self._send_sse()
                    return
                self._send(404, "Not found", "text/plain; charset=utf-8")

            def do_POST(self) -> None:
                path = urlparse(self.path).path
                if path == "/api/chat":
                    status, payload = app.submit_chat(self._read_json_body())
                    self._send_json(status, payload)
                    return
                if path == "/api/turns/current/stop":
                    status, payload = app.stop_current_turn()
                    self._send_json(status, payload)
                    return
                if path == "/api/settings":
                    status, payload = app.save_settings(self._read_json_body())
                    self._send_json(status, payload)
                    return
                self._send(404, "Not found", "text/plain; charset=utf-8")

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send(self, status: int, body: str, content_type: str) -> None:
                payload = body.encode("utf-8")
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError as exc:
                    if _is_client_disconnect(exc):
                        return
                    raise

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                self._send(
                    status,
                    json.dumps(payload, ensure_ascii=False),
                    "application/json; charset=utf-8",
                )

            def _read_json_body(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    length = 0
                if length <= 0:
                    return {}
                try:
                    raw = self.rfile.read(length).decode("utf-8")
                    payload = json.loads(raw)
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    return {}
                return payload if isinstance(payload, dict) else {}

            def _send_sse(self) -> None:
                try:
                    last_event_id = self.headers.get("Last-Event-ID")
                    if last_event_id is None:
                        last_seq = app.default_event_start_seq()
                    else:
                        try:
                            last_seq = int(last_event_id or "0")
                        except ValueError:
                            last_seq = 0
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    for chunk in app.event_stream(last_seq=last_seq):
                        self.wfile.write(chunk.encode("utf-8"))
                        self.wfile.flush()
                except OSError as exc:
                    if _is_client_disconnect(exc):
                        return
                    raise

        server = ThreadingHTTPServer((host, port), Handler)
        try:
            print(f"Ming Agent Workbench: http://{host}:{port}")
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nMing Agent Workbench stopped.")
        finally:
            server.server_close()
