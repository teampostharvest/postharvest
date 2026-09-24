"""Live login page viewer served by the session-capture backend.

Instead of pointing the capture link at Chrome's DevTools frontend, the link
now opens this small same-origin page. It renders the throwaway Chromium's
actual page (screenshot stream over the backend's CDP websocket bridge) and
forwards pointer/keyboard input back to it — i.e. the experience of a visible
browser tab, like ``cli.py login`` opens, piped through the app.

No session data ever leaves the server: the page renders a screenshot and the
clicks/keys the user produces here are proxied over the already-gated bridge.
The websocket URL is derived from ``window.location`` so it automatically uses
``wss``/``https`` when hosted behind TLS and ``ws``/``http`` on the laptop.
"""

VIEWER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live login — postharvest</title>
<style>
  :root { color-scheme: dark; }
  html, body { height: 100%; margin: 0; background: #0f1115; color: #e6e6e6;
    font: 13px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
  body { display: flex; flex-direction: column; }
  #bar { display: flex; align-items: center; gap: 10px; flex: 0 0 auto;
    padding: 8px 14px; border-bottom: 1px solid #262b36; background: #171a21; }
  #dot { width: 9px; height: 9px; border-radius: 50%; background: #7a828f; flex: none; }
  #dot.live { background: #34d399; box-shadow: 0 0 8px #34d39966; }
  #dot.err  { background: #f87171; box-shadow: 0 0 8px #f8717166; }
  #status { color: #c9ced8; }
  #hint { margin-left: auto; color: #8b93a1; }
  #stage { position: relative; flex: 1 1 auto; overflow: auto; outline: none;
    background: repeating-conic-gradient(#15181f 0 25%, #12151b 0 50%) 50% / 28px 28px; }
  #frame { display: block; width: 100%; min-height: 100%; }
  #cover { position: absolute; inset: 0; display: none; place-items: center;
    text-align: center; padding: 24px; background: rgba(15, 17, 21, 0.72); }
  #cover.show { display: grid; }
  #cover h1 { margin: 0 0 8px; font-size: 15px; }
  #cover p  { margin: 0; color: #8b93a1; max-width: 52ch; }
</style>
</head>
<body>
  <div id="bar">
    <span id="dot"></span>
    <span id="status">Connecting…</span>
    <span id="hint">Clicks and typing here are sent live to the login browser</span>
  </div>
  <div id="stage" tabindex="0">
    <img id="frame" alt="Live Facebook login page" draggable="false">
    <div id="cover"><div>
      <h1 id="coverTitle">Waiting for the login browser…</h1>
      <p id="coverText">Sign in to Facebook on the page below. When the session is saved, this tab closes itself.</p>
    </div></div>
  </div>
<script>
(function () {
  "use strict";
  var m = location.pathname.match(/\\/accounts\\/capture\\/([^\\/]+)\\/viewer/);
  var id = m ? m[1] : "";
  var wsScheme = location.protocol === "https:" ? "wss:" : "ws:";
  var wsUrl = wsScheme + "//" + location.host + "/api/accounts/capture/" + id + "/cdp";

  var bar  = document.getElementById("bar");
  var stage = document.getElementById("stage");
  var img  = document.getElementById("frame");
  var dot  = document.getElementById("dot");
  var statusEl = document.getElementById("status");
  var cover = document.getElementById("cover");

  var ws = null, msgId = 0, pending = {};
  var devW = 0, devH = 0;
  var lastFrameAt = 0, fallbackShot = false, closed = false;
  var lastMoveAt = 0;

  function setStatus(state, text) {
    dot.className = state;
    statusEl.textContent = text;
  }
  function showCover(title, text) {
    document.getElementById("coverTitle").textContent = title;
    document.getElementById("coverText").textContent = text;
    cover.classList.add("show");
  }
  function hideCover() { cover.classList.remove("show"); }

  function send(method, params, cb) {
    var frame = { id: ++msgId, method: method };
    if (params) frame.params = params;
    if (cb) pending[frame.id] = cb;
    try { ws.send(JSON.stringify(frame)); } catch (e) { /* socket closing */ }
  }

  function viewportSize() {
    return { w: Math.max(320, window.innerWidth), h: Math.max(200, window.innerHeight - bar.offsetHeight) };
  }
  function applyViewport() {
    var v = viewportSize();
    if (v.w === devW && v.h === devH) return;
    devW = v.w; devH = v.h;
    send("Emulation.setDeviceMetricsOverride", {
      width: devW, height: devH, deviceScaleFactor: 1, mobile: false,
      screenWidth: devW, screenHeight: devH
    });
    send("Page.bringToFront", {});
  }

  function render(data) {
    lastFrameAt = performance.now();
    try {
      var bin = atob(data), bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      var prev = img.src;
      img.src = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
      if (prev && prev.indexOf("blob:") === 0) URL.revokeObjectURL(prev);
      hideCover();
    } catch (e) { /* transient decode hiccup — keep streaming */ }
  }
  // Fallback: if the push stream ever stalls (no frame for a while), grab one
  // screenshot. Only fires when the stream is quiet — normal operation streams.
  function takeFallbackShot() {
    if (closed || fallbackShot || !ws || ws.readyState !== 1) return;
    fallbackShot = true;
    send("Page.captureScreenshot", { format: "jpeg", quality: 62 }, function (res) {
      fallbackShot = false;
      if (res && res.data) render(res.data);
    });
  }
  window.setInterval(function () {
    if (performance.now() - lastFrameAt > 2500) takeFallbackShot();
  }, 1000);

  function modifiers(e) {
    var mods = 0;
    if (e.altKey) mods |= 1;
    if (e.ctrlKey) mods |= 2;
    if (e.metaKey) mods |= 4;
    if (e.shiftKey) mods |= 8;
    return mods;
  }
  function mapPoint(e) {
    var r = img.getBoundingClientRect();
    if (!r.width || !r.height) return { x: 0, y: 0 };
    return {
      x: Math.max(0, ((e.clientX - r.left) * devW) / r.width | 0),
      y: Math.max(0, ((e.clientY - r.top) * devH) / r.height | 0)
    };
  }
  function mouseEvent(e, type) {
    var p = mapPoint(e);
    var mods = modifiers(e);
    if (type === "mouseMoved") {
      send("Input.dispatchMouseEvent", { type: type, x: p.x, y: p.y, button: "none", buttons: 0, modifiers: mods });
    } else {
      send("Input.dispatchMouseEvent", {
        type: type, x: p.x, y: p.y, button: "left", buttons: 1,
        clickCount: 1, modifiers: mods
      });
    }
  }

  var VK = { Enter: 13, Tab: 9, Backspace: 8, Escape: 27, Delete: 46, Home: 36, End: 35,
    PageUp: 33, PageDown: 34, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40,
    " ": 32, ".": 190, ",": 188, "/": 191, "\\\\": 220, ";": 186, "'": 222, "[": 219,
    "]": 221, "-": 189, "=": 187, "`": 192 };
  function keyEvent(e, type) {
    var text;
    if (type === "keyDown" && e.key.length === 1 && !e.ctrlKey && !e.metaKey) text = e.key;
    var vk = VK.hasOwnProperty(e.key) ? VK[e.key] : (e.keyCode || 0);
    send("Input.dispatchKeyEvent", {
      type: type, key: e.key, code: e.code || "",
      text: text, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk,
      modifiers: modifiers(e)
    });
  }

  stage.addEventListener("mousemove", function (e) {
    var now = performance.now();
    if (now - lastMoveAt < 32) return;
    lastMoveAt = now;
    mouseEvent(e, "mouseMoved");
  });
  stage.addEventListener("mousedown", function (e) { e.preventDefault(); stage.focus(); mouseEvent(e, "mousePressed"); });
  stage.addEventListener("mouseup", function (e) { mouseEvent(e, "mouseReleased"); });
  stage.addEventListener("wheel", function (e) {
    e.preventDefault();
    var p = mapPoint(e);
    send("Input.dispatchMouseEvent", { type: "mouseWheel", x: p.x, y: p.y,
      deltaX: e.deltaX, deltaY: e.deltaY, modifiers: modifiers(e) });
  }, { passive: false });
  window.addEventListener("keydown", function (e) {
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
    keyEvent(e, "keyDown");
  });
  window.addEventListener("keyup", function (e) { keyEvent(e, "keyUp"); });
  window.addEventListener("resize", function () { applyViewport(); });

  function connect() {
    try { ws = new WebSocket(wsUrl); }
    catch (e) { fail("Could not open a connection to the login browser."); return; }
    ws.onopen = function () {
      setStatus("live", "Connected — sign in to Facebook on the page below");
      send("Page.enable", {});
      send("Runtime.enable", {});
      applyViewport();
      // Push-streamed frames: the browser sends them as fast as it renders —
      // no request/response round trip per frame.
      send("Page.startScreencast", {
        format: "jpeg", quality: 62, everyNthFrame: 1,
        maxWidth: 1600, maxHeight: 1200
      });
      lastFrameAt = performance.now();
    };
    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.id && pending[msg.id]) {
        var cb = pending[msg.id];
        delete pending[msg.id];
        cb(msg.result);
      } else if (msg.method === "Page.screencastFrame" && msg.params) {
        if (msg.params.data) render(msg.params.data);
        if (msg.params.sessionId) send("Page.screencastFrameAck", { sessionId: msg.params.sessionId });
      } else if (msg.method === "Page.loadEventFired") {
        applyViewport();
      }
    };
    ws.onclose = function (ev) {
      closed = true;
      setStatus("err", "Login browser closed");
      if (ev && ev.code === 4403) {
        showCover("Already connected elsewhere", "The login browser is open in another tab. Close that tab, then reload this one.");
      } else {
        showCover("This login browser is done", "The session was saved or the capture ended — you can close this tab.");
      }
    };
    ws.onerror = function () {
      if (!closed) setStatus("err", "Connection problem — trying to reconnect…");
    };
  }
  function fail(text) {
    closed = true;
    setStatus("err", "Can't reach the login browser");
    showCover("Can't reach the login browser", text);
  }
  connect();
})();
</script>
</body>
</html>
"""