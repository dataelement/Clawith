"""Shared Chrome rendering helpers for document conversion."""

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from loguru import logger


def chrome_executable() -> str | None:
    """Return a local Chrome/Chromium executable path if one is available."""
    candidates = [
        os.environ.get("CHROME_BIN"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    return next((str(path) for path in candidates if path and Path(path).exists()), None)


def is_complex_css_paint(value: str | None) -> bool:
    value = str(value or "")
    return "gradient(" in value or "url(" in value


def is_translucent_css_color(value: str | None) -> bool:
    import re

    value = str(value or "").strip().lower()
    match = re.match(r"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*([0-9.]+)\s*\)", value)
    if not match:
        return False
    try:
        return float(match.group(1)) < 0.999
    except ValueError:
        return False


async def collect_browser_layout(
    src_file: Path,
    design_w_px: int,
    design_h_px: int,
    render_mode: str,
    render_scale: float = 2.0,
) -> dict[str, Any] | None:
    import socket
    import subprocess
    import tempfile
    import time
    import urllib.request
    import websockets

    chrome = chrome_executable()
    if not chrome:
        return None

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    profile_dir = tempfile.TemporaryDirectory(prefix="clawith-html-pptx-")
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--allow-file-access-from-files",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir.name}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/json/version", timeout=0.25) as resp:
                    json.loads(resp.read().decode("utf-8"))
                break
            except Exception:
                await asyncio.sleep(0.1)
        else:
            return None

        file_url = src_file.resolve().as_uri()
        req = urllib.request.Request(f"{base}/json/new?{file_url}", method="PUT")
        with urllib.request.urlopen(req, timeout=2) as resp:
            target = json.loads(resp.read().decode("utf-8"))
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            return None

        expression = r"""
(() => {
  const transparent = new Set(['rgba(0, 0, 0, 0)', 'transparent']);
  const viewport = { width: window.innerWidth, height: window.innerHeight };
  const pageStyle = getComputedStyle(document.body || document.documentElement);
  const pageBg = cssPaint(pageStyle) || '#ffffff';

  function isTransparentColor(value) {
    return !value || transparent.has(value) || /^rgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)$/.test(value);
  }

  function cssPaint(cs) {
    if (cs.backgroundColor && !isTransparentColor(cs.backgroundColor)) return cs.backgroundColor;
    if (cs.backgroundImage && cs.backgroundImage !== 'none') return cs.backgroundImage;
    return '';
  }

  function isVisible(el) {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity || 1) > 0.01 && r.width > 0.5 && r.height > 0.5;
  }

  function childElements(el) {
    return Array.from(el.children || []).filter(isVisible);
  }

  function directText(el) {
    return Array.from(el.childNodes || [])
      .filter(n => n.nodeType === Node.TEXT_NODE)
      .map(n => n.textContent || '')
      .join(' ')
      .replace(/\s+/g, ' ')
      .trim();
  }

  function fullText(el) {
    return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
  }

  function isInlineTag(tag) {
    return ['a', 'abbr', 'b', 'br', 'code', 'em', 'i', 'mark', 'small', 'span', 'strong', 'sub', 'sup', 'u'].includes(tag);
  }

  function isBlockTextTag(tag) {
    return ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'button', 'a', 'td', 'th'].includes(tag);
  }

  function hasPaint(cs) {
    const bg = cssPaint(cs);
    const border = ['Top', 'Right', 'Bottom', 'Left'].some(side => parseFloat(cs[`border${side}Width`] || '0') > 0);
    return !!bg || border;
  }

  function isTextClipBackground(cs) {
    const clip = `${cs.backgroundClip || ''} ${cs.webkitBackgroundClip || ''}`.toLowerCase();
    const fill = `${cs.webkitTextFillColor || ''}`.toLowerCase();
    return clip.includes('text') || fill === 'transparent' || fill === 'rgba(0, 0, 0, 0)';
  }

  function itemFor(el, rootRect, kind, text) {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const itemId = `item-${Math.random().toString(36).slice(2)}-${Date.now()}`;
    el.setAttribute('data-clawith-item-id', itemId);
    return {
      itemId,
      kind,
      tag: el.tagName.toLowerCase(),
      text: text || '',
      src: el.currentSrc || el.getAttribute('src') || '',
      x: r.left - rootRect.left,
      y: r.top - rootRect.top,
      w: r.width,
      h: r.height,
      style: {
color: cs.color,
backgroundColor: cs.backgroundColor,
backgroundImage: cs.backgroundImage,
borderColor: cs.borderTopColor,
borderWidth: cs.borderTopWidth,
borderRadius: cs.borderTopLeftRadius,
fontSize: cs.fontSize,
fontFamily: cs.fontFamily,
fontWeight: cs.fontWeight,
textAlign: cs.textAlign,
display: cs.display,
alignItems: cs.alignItems,
justifyContent: cs.justifyContent,
paddingLeft: cs.paddingLeft,
paddingRight: cs.paddingRight,
paddingTop: cs.paddingTop,
paddingBottom: cs.paddingBottom,
webkitTextFillColor: cs.webkitTextFillColor,
backgroundClip: cs.backgroundClip,
webkitBackgroundClip: cs.webkitBackgroundClip,
lineHeight: cs.lineHeight,
opacity: cs.opacity,
boxShadow: cs.boxShadow,
filter: cs.filter,
backdropFilter: cs.backdropFilter || cs.webkitBackdropFilter,
      },
    };
  }

  function collectRoot(root) {
    const rootRectRaw = root === document.body
      ? { left: 0, top: 0, width: viewport.width, height: viewport.height }
      : root.getBoundingClientRect();
      const rootRect = {
left: rootRectRaw.left || 0,
top: rootRectRaw.top || 0,
width: rootRectRaw.width || viewport.width,
height: rootRectRaw.height || viewport.height,
    };
    const items = [];
    const rootStyle = getComputedStyle(root);

    function walk(el) {
      if (!isVisible(el)) return;
      const cs = getComputedStyle(el);
      const children = childElements(el);
      const tag = el.tagName.toLowerCase();
      const text = directText(el);
      const hasBlockChildren = children.some(child => !isInlineTag(child.tagName.toLowerCase()));

      if (el !== root && hasPaint(cs) && !isTextClipBackground(cs)) {
items.push(itemFor(el, rootRect, 'shape'));
      }
      if (tag === 'img') {
items.push(itemFor(el, rootRect, 'image'));
return;
      }
      if (isBlockTextTag(tag) && !hasBlockChildren) {
const content = fullText(el);
if (content) items.push(itemFor(el, rootRect, 'text', content));
return;
      }
      if (text || ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'span', 'strong', 'em', 'button', 'a'].includes(tag)) {
const content = text || (children.length ? '' : (el.innerText || '').replace(/\s+/g, ' ').trim());
if (content) items.push(itemFor(el, rootRect, 'text', content));
      }
      children.forEach(walk);
    }

    childElements(root).forEach(walk);
    return {
      x: rootRect.left,
      y: rootRect.top,
      width: rootRect.width,
      height: rootRect.height,
      backgroundColor: cssPaint(rootStyle) || pageBg,
      items,
    };
  }

  let roots = Array.from(document.querySelectorAll('.slide,[data-slide]')).filter(isVisible);
  if (!roots.length) {
    const body = document.body || document.documentElement;
    roots = Array.from(body.children || [])
      .filter(el => isVisible(el) && !['script', 'style', 'link', 'meta'].includes(el.tagName.toLowerCase()))
      .filter(el => el.getBoundingClientRect().height >= 24);
    if (roots.length === 1) {
      const only = roots[0];
      const onlyRect = only.getBoundingClientRect();
      const children = Array.from(only.children || [])
.filter(el => isVisible(el) && !['script', 'style', 'link', 'meta'].includes(el.tagName.toLowerCase()))
.filter(el => el.getBoundingClientRect().height >= 24);
      if (onlyRect.height > viewport.height * 1.2 && children.length > 1) {
roots = children;
      } else if (onlyRect.width < viewport.width * 0.92 || onlyRect.height < viewport.height * 0.92) {
roots = [body];
      }
    }
  }
  if (!roots.length) roots = [document.body || document.documentElement];
  roots.forEach((root, index) => root.setAttribute('data-clawith-slide-root', String(index)));
  return { viewport, pageBackground: pageBg, slides: roots.map(collectRoot) };
})()
"""

        msg_id = 0

        async with websockets.connect(ws_url, max_size=20_000_000) as ws_conn:
            async def send(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
                nonlocal msg_id
                msg_id += 1
                await ws_conn.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
                while True:
                    raw = await asyncio.wait_for(ws_conn.recv(), timeout=8)
                    message = json.loads(raw)
                    if message.get("id") == msg_id:
                        return message

            await send("Page.enable")
            await send("Runtime.enable")
            await send("Emulation.setDeviceMetricsOverride", {
                "width": design_w_px,
                "height": design_h_px,
                "deviceScaleFactor": render_scale,
                "mobile": False,
            })
            await send("Page.navigate", {"url": file_url})
            load_deadline = time.time() + 8
            while time.time() < load_deadline:
                raw = await asyncio.wait_for(ws_conn.recv(), timeout=8)
                message = json.loads(raw)
                if message.get("method") == "Page.loadEventFired":
                    break
            await asyncio.sleep(0.25)
            result = await send("Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            })
            layout = result.get("result", {}).get("result", {}).get("value")
            if layout and render_mode in ("visual", "screenshot", "image", "hybrid"):
                import base64
                screenshots: list[str | None] = []
                for idx, slide_data in enumerate(layout.get("slides") or []):
                    clip_w = max(1.0, float(slide_data.get("width") or design_w_px))
                    clip_h = max(1.0, float(slide_data.get("height") or design_h_px))
                    screenshot_result = await send("Page.captureScreenshot", {
                        "format": "png",
                        "captureBeyondViewport": True,
                        "fromSurface": True,
                        "clip": {
                            "x": max(0.0, float(slide_data.get("x") or 0)),
                            "y": max(0.0, float(slide_data.get("y") or 0)),
                            "width": clip_w,
                            "height": clip_h,
                            "scale": 1,
                        },
                    })
                    data = screenshot_result.get("result", {}).get("data")
                    if not data:
                        screenshots.append(None)
                        continue
                    with tempfile.NamedTemporaryFile(delete=False, suffix=f"-slide-{idx + 1}.png") as image_tmp:
                        image_file = Path(image_tmp.name)
                    image_file.write_bytes(base64.b64decode(data))
                    screenshots.append(str(image_file))
                layout["screenshots"] = screenshots
            if layout and render_mode in ("editable", "hybrid_editable"):
                import base64
                background_screenshots: list[str | None] = []
                shape_screenshots: dict[str, str] = {}
                page_bg_value = str(layout.get("pageBackground") or "")
                for idx, slide_data in enumerate(layout.get("slides") or []):
                    bg_value = str(slide_data.get("backgroundColor") or "")
                    root_w = max(1.0, float(slide_data.get("width") or design_w_px))
                    root_h = max(1.0, float(slide_data.get("height") or design_h_px))
                    root_is_full_canvas = root_w >= design_w_px * 0.98 and root_h >= design_h_px * 0.98
                    needs_bg = (
                        is_complex_css_paint(bg_value)
                        or is_translucent_css_color(bg_value)
                        or (is_complex_css_paint(page_bg_value) and not root_is_full_canvas)
                    )
                    if not needs_bg:
                        background_screenshots.append(None)
                        continue
                    clip_w = root_w
                    clip_h = root_h
                    hide_expr = (
                        "(() => {"
                        "const id='clawith-bg-capture-style';"
                        "document.getElementById(id)?.remove();"
                        "const style=document.createElement('style');"
                        "style.id=id;"
                        f"style.textContent='[data-clawith-slide-root=\"{idx}\"] > * {{ visibility: hidden !important; }}';"
                        "document.head.appendChild(style);"
                        "})()"
                    )
                    restore_expr = "document.getElementById('clawith-bg-capture-style')?.remove()"
                    await send("Runtime.evaluate", {"expression": hide_expr, "awaitPromise": True})
                    try:
                        screenshot_result = await send("Page.captureScreenshot", {
                            "format": "png",
                            "captureBeyondViewport": True,
                            "fromSurface": True,
                            "clip": {
                                "x": max(0.0, float(slide_data.get("x") or 0)),
                                "y": max(0.0, float(slide_data.get("y") or 0)),
                                "width": clip_w,
                                "height": clip_h,
                                "scale": 1,
                            },
                        })
                    finally:
                        await send("Runtime.evaluate", {"expression": restore_expr})
                    data = screenshot_result.get("result", {}).get("data")
                    if not data:
                        background_screenshots.append(None)
                        continue
                    with tempfile.NamedTemporaryFile(delete=False, suffix=f"-slide-bg-{idx + 1}.png") as image_tmp:
                        image_file = Path(image_tmp.name)
                    image_file.write_bytes(base64.b64decode(data))
                    background_screenshots.append(str(image_file))
                    # Root background capture temporarily hides direct
                    # children; after it is restored, item-level captures
                    # can preserve shadows/backdrop effects for cards.
                for slide_idx, slide_data in enumerate(layout.get("slides") or []):
                    for item in slide_data.get("items") or []:
                        if item.get("kind") != "shape":
                            continue
                        style = item.get("style") or {}
                        bg_value = str(style.get("backgroundImage") or "")
                        has_complex_paint = (
                            "gradient(" in bg_value
                            or "url(" in bg_value
                            or str(style.get("boxShadow") or "none") != "none"
                            or str(style.get("filter") or "none") != "none"
                            or str(style.get("backdropFilter") or "none") != "none"
                        )
                        if not has_complex_paint or not item.get("itemId"):
                            continue
                        item_id = str(item["itemId"])
                        clip_w = max(1.0, float(item.get("w") or 1))
                        clip_h = max(1.0, float(item.get("h") or 1))
                        hide_expr = (
                            "(() => {"
                            "const id='clawith-item-bg-capture-style';"
                            "document.getElementById(id)?.remove();"
                            "const style=document.createElement('style');"
                            "style.id=id;"
                            "style.textContent="
                            f"'[data-clawith-slide-root=\"{slide_idx}\"] * {{ visibility: hidden !important; }} "
                            f"[data-clawith-slide-root=\"{slide_idx}\"] [data-clawith-item-id=\"{item_id}\"] {{ visibility: visible !important; color: transparent !important; -webkit-text-fill-color: transparent !important; text-shadow: none !important; }} "
                            f"[data-clawith-slide-root=\"{slide_idx}\"] [data-clawith-item-id=\"{item_id}\"]::before, "
                            f"[data-clawith-slide-root=\"{slide_idx}\"] [data-clawith-item-id=\"{item_id}\"]::after {{ color: transparent !important; -webkit-text-fill-color: transparent !important; text-shadow: none !important; }} "
                            f"[data-clawith-slide-root=\"{slide_idx}\"] [data-clawith-item-id=\"{item_id}\"] * {{ visibility: hidden !important; color: transparent !important; -webkit-text-fill-color: transparent !important; text-shadow: none !important; }}';"
                            "document.head.appendChild(style);"
                            "})()"
                        )
                        restore_expr = "document.getElementById('clawith-item-bg-capture-style')?.remove()"
                        await send("Runtime.evaluate", {"expression": hide_expr, "awaitPromise": True})
                        try:
                            screenshot_result = await send("Page.captureScreenshot", {
                                "format": "png",
                                "captureBeyondViewport": True,
                                "fromSurface": True,
                                "clip": {
                                    "x": max(0.0, float(slide_data.get("x") or 0) + float(item.get("x") or 0)),
                                    "y": max(0.0, float(slide_data.get("y") or 0) + float(item.get("y") or 0)),
                                    "width": clip_w,
                                    "height": clip_h,
                                    "scale": 1,
                                },
                            })
                        finally:
                            await send("Runtime.evaluate", {"expression": restore_expr})
                        data = screenshot_result.get("result", {}).get("data")
                        if not data:
                            continue
                        with tempfile.NamedTemporaryFile(delete=False, suffix=f"-item-bg-{item_id}.png") as image_tmp:
                            image_file = Path(image_tmp.name)
                        image_file.write_bytes(base64.b64decode(data))
                        shape_screenshots[item_id] = str(image_file)
                layout["backgroundScreenshots"] = background_screenshots
                layout["shapeScreenshots"] = shape_screenshots
            return layout
    except Exception as layout_exc:
        logger.warning(f"Browser layout extraction failed, falling back to DOM flow conversion: {layout_exc}")
        return None
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        profile_dir.cleanup()
