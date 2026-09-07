/*
 * Apostate capture collector.
 *
 * Records what a real device emits. Runs in a normal headed browser with no
 * dependencies and no build step, so it works on any device we can open a URL on.
 *
 * The one rule that matters: a probe either measures something or reports that
 * it could not. There are no fallback values anywhere in this file. If a probe
 * throws, `ok` is false, `value` is null, and the error text is kept. A capture
 * with visible failures is useful; a capture that quietly substitutes a
 * plausible number is worse than no capture at all, because nothing downstream
 * can tell it apart from ground truth.
 */
(function (global) {
  "use strict";

  var CAPTURE_VERSION = 1;

  // ---------------------------------------------------------------- helpers

  function b64(bytes) {
    var u8 = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes.buffer || bytes);
    var s = "", CHUNK = 0x8000;
    for (var i = 0; i < u8.length; i += CHUNK) {
      s += String.fromCharCode.apply(null, u8.subarray(i, i + CHUNK));
    }
    return global.btoa(s);
  }

  async function sha256Hex(input) {
    var data = typeof input === "string" ? new TextEncoder().encode(input)
             : input instanceof Uint8Array ? input
             : new Uint8Array(input.buffer || input);
    var digest = await crypto.subtle.digest("SHA-256", data);
    return Array.prototype.map
      .call(new Uint8Array(digest), function (b) { return b.toString(16).padStart(2, "0"); })
      .join("");
  }

  var registry = [];

  /**
   * Register a probe.
   * @param id        stable probe name
   * @param opts      {deterministic: bool} - deterministic probes are read twice
   * @param fn        async () => value, or throws
   */
  function probe(id, opts, fn) {
    registry.push({ id: id, deterministic: !!(opts && opts.deterministic), fn: fn });
  }

  async function run(entry) {
    var t0 = performance.now();
    try {
      var value = await entry.fn();
      if (value === undefined) {
        // A probe returning nothing is a bug in the probe, not a device fact.
        throw new Error("probe returned undefined");
      }
      return { ok: true, value: value, error: null, duration_ms: +(performance.now() - t0).toFixed(3) };
    } catch (e) {
      return {
        ok: false,
        value: null,
        error: (e && (e.stack || e.message)) ? String(e.message || e) : String(e),
        duration_ms: +(performance.now() - t0).toFixed(3)
      };
    }
  }

  function must(cond, msg) { if (!cond) throw new Error(msg); }

  // ------------------------------------------------------------ environment

  probe("navigator.scalars", { deterministic: true }, function () {
    var n = navigator, out = {};
    [
      "userAgent", "appCodeName", "appName", "appVersion", "platform", "product",
      "productSub", "vendor", "vendorSub", "language", "oscpu", "buildID",
      "hardwareConcurrency", "deviceMemory", "maxTouchPoints", "pdfViewerEnabled",
      "cookieEnabled", "onLine", "doNotTrack", "webdriver", "userActivation"
    ].forEach(function (k) {
      try { var v = n[k]; out[k] = (typeof v === "object" && v !== null) ? undefined : v; }
      catch (e) { out[k] = { __error: String(e.message || e) }; }
    });
    out.languages = Array.prototype.slice.call(n.languages || []);
    return out;
  });

  probe("navigator.userAgentData", { deterministic: true }, async function () {
    must(navigator.userAgentData, "userAgentData unsupported");
    var low = navigator.userAgentData.toJSON ? navigator.userAgentData.toJSON() : null;
    var high = await navigator.userAgentData.getHighEntropyValues([
      "architecture", "bitness", "model", "platformVersion",
      "uaFullVersion", "fullVersionList", "wow64", "formFactors"
    ]);
    return { low: low, high: high };
  });

  probe("navigator.plugins", { deterministic: true }, function () {
    return {
      plugins: Array.prototype.map.call(navigator.plugins, function (p) {
        return {
          name: p.name, filename: p.filename, description: p.description,
          mimeTypes: Array.prototype.map.call(p, function (m) {
            return { type: m.type, suffixes: m.suffixes, description: m.description };
          })
        };
      }),
      mimeTypes: Array.prototype.map.call(navigator.mimeTypes, function (m) {
        return { type: m.type, suffixes: m.suffixes, description: m.description };
      })
    };
  });

  probe("screen.geometry", { deterministic: true }, function () {
    var s = screen;
    return {
      width: s.width, height: s.height,
      availWidth: s.availWidth, availHeight: s.availHeight,
      availLeft: s.availLeft, availTop: s.availTop,
      colorDepth: s.colorDepth, pixelDepth: s.pixelDepth,
      orientation: s.orientation ? { angle: s.orientation.angle, type: s.orientation.type } : null,
      isExtended: typeof s.isExtended === "boolean" ? s.isExtended : null,
      devicePixelRatio: global.devicePixelRatio,
      // Window metrics vary with the window; recorded for the taskbar/chrome deltas they imply.
      outerWidth: global.outerWidth, outerHeight: global.outerHeight,
      innerWidth: global.innerWidth, innerHeight: global.innerHeight,
      screenX: global.screenX, screenY: global.screenY
    };
  });

  probe("intl.locale", { deterministic: true }, function () {
    var ro = Intl.DateTimeFormat().resolvedOptions();
    return {
      resolvedOptions: ro,
      timezoneOffsetMinutes: new Date().getTimezoneOffset(),
      // January and July expose the DST rule, which pins the zone far more tightly than the name.
      offsetJan: new Date(Date.UTC(new Date().getUTCFullYear(), 0, 1)).getTimezoneOffset(),
      offsetJul: new Date(Date.UTC(new Date().getUTCFullYear(), 6, 1)).getTimezoneOffset(),
      dateToString: new Date(0).toString(),
      numberFormat: new Intl.NumberFormat().resolvedOptions(),
      collator: new Intl.Collator().resolvedOptions(),
      supportedCalendars: typeof Intl.supportedValuesOf === "function"
        ? Intl.supportedValuesOf("calendar") : null,
      supportedTimeZonesCount: typeof Intl.supportedValuesOf === "function"
        ? Intl.supportedValuesOf("timeZone").length : null
    };
  });

  // ------------------------------------------------------------------ canvas

  function drawCanvasScene(ctx, w, h) {
    // Fixed scene. Any change to it invalidates comparison with earlier captures,
    // which is why collector.js is hashed into every capture's context.
    ctx.textBaseline = "top";
    ctx.font = "14px 'Arial'";
    ctx.textBaseline = "alphabetic";
    ctx.fillStyle = "#f60";
    ctx.fillRect(125, 1, 62, 20);
    ctx.fillStyle = "#069";
    ctx.fillText("Apostate 🔒 mLj", 2, 15);
    ctx.fillStyle = "rgba(102, 204, 0, 0.7)";
    ctx.fillText("Apostate 🔒 mLj", 4, 17);
    ctx.globalCompositeOperation = "multiply";
    ["#f2f", "#2ff", "#ff2"].forEach(function (c, i) {
      ctx.fillStyle = c;
      ctx.beginPath();
      ctx.arc(50 + i * 25, 50, 40, 0, Math.PI * 2, true);
      ctx.closePath();
      ctx.fill();
    });
    ctx.globalCompositeOperation = "source-over";
    ctx.strokeStyle = "#0a0";
    ctx.beginPath();
    ctx.arc(w / 2, h / 2, 30, 0, Math.PI * 2, true);
    ctx.stroke();
  }

  probe("canvas.2d", { deterministic: true }, async function () {
    var w = 280, h = 120;
    var cvs = document.createElement("canvas");
    cvs.width = w; cvs.height = h;
    var ctx = cvs.getContext("2d");
    must(ctx, "2d context unavailable");
    drawCanvasScene(ctx, w, h);

    var pngUrl = cvs.toDataURL("image/png");
    var pixels = ctx.getImageData(0, 0, w, h).data;

    return {
      width: w, height: h,
      // PNG is the reconstructible artifact: replay needs pixels, and a digest
      // can never give them back.
      png_base64: pngUrl.split(",")[1],
      pixels_sha256: await sha256Hex(new Uint8Array(pixels.buffer)),
      // Full RGBA kept so a diff can name the pixel that moved, not just report
      // that a hash changed.
      pixels_base64: b64(new Uint8Array(pixels.buffer)),
      contextAttributes: ctx.getContextAttributes ? ctx.getContextAttributes() : null,
      isPointInPath: ctx.isPointInPath(50, 50),
      measureText: (function () {
        var m = ctx.measureText("Apostate 🔒 mLj");
        var o = {};
        for (var k in m) { if (typeof m[k] === "number") o[k] = m[k]; }
        return o;
      })()
    };
  });

  probe("canvas.toDataURL_variants", { deterministic: true }, function () {
    var cvs = document.createElement("canvas");
    cvs.width = 60; cvs.height = 30;
    var ctx = cvs.getContext("2d");
    ctx.fillStyle = "#123456";
    ctx.fillRect(0, 0, 60, 30);
    // Encoder identity leaks through the compressed bytes of each format.
    return {
      png: cvs.toDataURL("image/png").length,
      jpeg: cvs.toDataURL("image/jpeg", 0.5).length,
      webp: cvs.toDataURL("image/webp").length,
      png_head: cvs.toDataURL("image/png").slice(0, 120)
    };
  });

  // ------------------------------------------------------------------ WebGL

  function glParams(gl, ctxName) {
    var out = {}, names = [];
    for (var k in gl) {
      // Enumerable GL constants are upper-case numbers; query each one.
      if (/^[A-Z0-9_]+$/.test(k) && typeof gl[k] === "number") names.push(k);
    }
    names.sort().forEach(function (name) {
      try {
        var v = gl.getParameter(gl[name]);
        if (v === null || v === undefined) return;
        if (typeof v === "object" && v.length !== undefined) v = Array.prototype.slice.call(v);
        if (typeof v === "object" && !Array.isArray(v)) return;
        out[name] = v;
      } catch (e) { /* not a valid getParameter target; not a device fact */ }
    });
    out.__context = ctxName;
    return out;
  }

  function glPrecision(gl) {
    var out = {};
    ["VERTEX_SHADER", "FRAGMENT_SHADER"].forEach(function (st) {
      ["HIGH_FLOAT", "MEDIUM_FLOAT", "LOW_FLOAT", "HIGH_INT", "MEDIUM_INT", "LOW_INT"].forEach(function (pt) {
        try {
          var p = gl.getShaderPrecisionFormat(gl[st], gl[pt]);
          if (p) out[st + "." + pt] = { rangeMin: p.rangeMin, rangeMax: p.rangeMax, precision: p.precision };
        } catch (e) { /* unsupported combination */ }
      });
    });
    return out;
  }

  async function webglCapture(ctxName) {
    var cvs = document.createElement("canvas");
    cvs.width = 256; cvs.height = 128;
    var gl = cvs.getContext(ctxName, { preserveDrawingBuffer: true });
    must(gl, ctxName + " unavailable");

    var dbg = gl.getExtension("WEBGL_debug_renderer_info");
    var exts = gl.getSupportedExtensions() || [];

    // Render a scene that exercises the rasteriser, not just a clear colour.
    var vs = gl.createShader(gl.VERTEX_SHADER);
    gl.shaderSource(vs, "attribute vec2 p;varying vec2 v;void main(){v=p;gl_Position=vec4(p,0.0,1.0);}");
    gl.compileShader(vs);
    var fs = gl.createShader(gl.FRAGMENT_SHADER);
    gl.shaderSource(fs, "precision mediump float;varying vec2 v;void main(){gl_FragColor=vec4(abs(sin(v.x*8.0)),abs(cos(v.y*6.0)),v.x*v.y+0.5,1.0);}");
    gl.compileShader(fs);
    var prog = gl.createProgram();
    gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
    must(gl.getProgramParameter(prog, gl.LINK_STATUS), "program link failed: " + gl.getProgramInfoLog(prog));
    gl.useProgram(prog);

    var buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(prog, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.viewport(0, 0, 256, 128);
    gl.clearColor(0, 0, 0, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    var px = new Uint8Array(256 * 128 * 4);
    gl.readPixels(0, 0, 256, 128, gl.RGBA, gl.UNSIGNED_BYTE, px);

    return {
      unmaskedVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
      unmaskedRenderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
      vendor: gl.getParameter(gl.VENDOR),
      renderer: gl.getParameter(gl.RENDERER),
      version: gl.getParameter(gl.VERSION),
      shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
      extensions: exts,
      parameters: glParams(gl, ctxName),
      precision: glPrecision(gl),
      contextAttributes: gl.getContextAttributes(),
      pixels_sha256: await sha256Hex(px),
      pixels_base64: b64(px),
      antialiasSamples: gl.getParameter(gl.SAMPLES)
    };
  }

  probe("webgl1", { deterministic: true }, function () { return webglCapture("webgl"); });
  probe("webgl2", { deterministic: true }, function () { return webglCapture("webgl2"); });

  probe("webgpu", { deterministic: true }, async function () {
    must(global.navigator.gpu, "WebGPU unsupported");
    var out = { preferredCanvasFormat: navigator.gpu.getPreferredCanvasFormat(), adapters: {} };
    for (var pref of ["high-performance", "low-power"]) {
      try {
        var a = await navigator.gpu.requestAdapter({ powerPreference: pref });
        if (!a) { out.adapters[pref] = null; continue; }
        var info = a.info || (a.requestAdapterInfo ? await a.requestAdapterInfo() : null);
        var limits = {};
        if (a.limits) for (var k in a.limits) limits[k] = a.limits[k];
        out.adapters[pref] = {
          // GPUSupportedFeatures is a set and Chromium's iteration order varies
          // between reads on the same device, so order carries no device
          // information here and sorting is what makes the field comparable.
          features: Array.from(a.features || []).sort(),
          limits: limits,
          info: info ? {
            vendor: info.vendor, architecture: info.architecture,
            device: info.device, description: info.description,
            subgroupMinSize: info.subgroupMinSize, subgroupMaxSize: info.subgroupMaxSize
          } : null
        };
      } catch (e) { out.adapters[pref] = { __error: String(e.message || e) }; }
    }
    return out;
  });

  // ------------------------------------------------------------------ audio

  probe("audio.offline_render", { deterministic: true }, async function () {
    var OAC = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    must(OAC, "OfflineAudioContext unsupported");
    var ctx = new OAC(1, 44100, 44100);
    var osc = ctx.createOscillator();
    var comp = ctx.createDynamicsCompressor();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(10000, ctx.currentTime);
    comp.threshold.setValueAtTime(-50, ctx.currentTime);
    comp.knee.setValueAtTime(40, ctx.currentTime);
    comp.ratio.setValueAtTime(12, ctx.currentTime);
    comp.attack.setValueAtTime(0, ctx.currentTime);
    comp.release.setValueAtTime(0.25, ctx.currentTime);
    osc.connect(comp); comp.connect(ctx.destination);
    osc.start(0);

    var rendered = await ctx.startRendering();
    var all = rendered.getChannelData(0);
    // The 4500-5000 window is the region the field conventionally reads; a wider
    // slice is kept so a diff can localise a difference rather than only detect one.
    var slice = all.subarray(4000, 6000);

    return {
      sampleRate: rendered.sampleRate,
      length: rendered.length,
      numberOfChannels: rendered.numberOfChannels,
      full_sha256: await sha256Hex(new Uint8Array(all.buffer, all.byteOffset, all.byteLength)),
      slice_start: 4000,
      slice_base64: b64(new Uint8Array(slice.buffer, slice.byteOffset, slice.byteLength)),
      // Kept in the clear as a fast sanity check: a degenerate render is
      // immediately visible instead of being hidden behind a hash.
      slice_sum: Array.prototype.reduce.call(slice, function (a, b) { return a + Math.abs(b); }, 0)
    };
  });

  probe("audio.properties", { deterministic: false }, function () {
    var AC = global.AudioContext || global.webkitAudioContext;
    must(AC, "AudioContext unsupported");
    var ctx = new AC();
    var out = {
      sampleRate: ctx.sampleRate,
      // Latencies track the output device's current buffer state and move
      // between reads on the same machine; recorded, but not identity.
      baseLatency: ctx.baseLatency,
      outputLatency: ctx.outputLatency,
      destinationMaxChannelCount: ctx.destination.maxChannelCount,
      destinationChannelCount: ctx.destination.channelCount,
      state: ctx.state
    };
    var an = ctx.createAnalyser();
    ["fftSize", "frequencyBinCount", "minDecibels", "maxDecibels", "smoothingTimeConstant"]
      .forEach(function (k) { out["analyser." + k] = an[k]; });
    var dc = ctx.createDynamicsCompressor();
    ["threshold", "knee", "ratio", "attack", "release"].forEach(function (k) {
      out["compressor." + k] = { value: dc[k].value, min: dc[k].minValue, max: dc[k].maxValue };
    });
    ctx.close();
    return out;
  });

  // -------------------------------------------------------- layout and text

  probe("clientrects", { deterministic: true }, function () {
    var host = document.createElement("div");
    host.style.cssText = "position:absolute;left:-10000px;top:0;visibility:hidden";
    host.innerHTML =
      '<span style="font:24px monospace">Apostate mLj</span>' +
      '<span style="font:18px serif">W_i_d_t_h</span>' +
      '<span style="font:italic 700 15px \'Times New Roman\',serif">gQÅ字</span>' +
      '<div style="display:inline-block;width:3.7em;font:12px sans-serif">x</div>';
    document.body.appendChild(host);
    var rects = [];
    // Full coordinates at full precision. Sub-pixel layout differs by font stack,
    // rasteriser and DPR, so rounding here would destroy the signal.
    Array.prototype.forEach.call(host.querySelectorAll("span,div"), function (el) {
      var r = el.getBoundingClientRect();
      rects.push({ x: r.x, y: r.y, width: r.width, height: r.height, top: r.top, left: r.left });
      Array.prototype.forEach.call(el.getClientRects(), function (cr) {
        rects.push({ __client: true, x: cr.x, y: cr.y, width: cr.width, height: cr.height });
      });
    });
    host.remove();
    return rects;
  });

  probe("fonts.detected", { deterministic: true }, function () {
    var BASE = ["monospace", "sans-serif", "serif"];
    var TEST = ("Andale Mono,Arial,Arial Black,Arial Hebrew,Arial Narrow,Arial Rounded MT Bold,Arial Unicode MS," +
      "Bitstream Vera Sans Mono,Book Antiqua,Bookman Old Style,Calibri,Cambria,Cambria Math,Century,Century Gothic," +
      "Century Schoolbook,Comic Sans MS,Consolas,Courier,Courier New,Geneva,Georgia,Helvetica,Helvetica Neue," +
      "Impact,Lucida Bright,Lucida Calligraphy,Lucida Console,Lucida Fax,LUCIDA GRANDE,Lucida Handwriting," +
      "Lucida Sans,Lucida Sans Typewriter,Lucida Sans Unicode,Microsoft Sans Serif,Monaco,Monotype Corsiva," +
      "MS Gothic,MS Outlook,MS PGothic,MS Reference Sans Serif,MS Sans Serif,MS Serif,MYRIAD PRO,Palatino," +
      "Palatino Linotype,Segoe Print,Segoe Script,Segoe UI,Segoe UI Light,Segoe UI Semibold,Segoe UI Symbol," +
      "Tahoma,Times,Times New Roman,Trebuchet MS,Verdana,Wingdings,Wingdings 2,Wingdings 3,Roboto,Noto Sans," +
      "Noto Color Emoji,Droid Sans,Ubuntu,Cantarell,DejaVu Sans,Liberation Sans,PingFang SC,Hiragino Sans," +
      "Apple Color Emoji,SF Pro Text,Menlo,Optima,Papyrus,Zapfino").split(",");

    var span = document.createElement("span");
    span.style.cssText = "position:absolute;left:-10000px;font-size:72px";
    span.textContent = "mmmmmmmmmmlli字";
    document.body.appendChild(span);

    var base = {};
    BASE.forEach(function (b) {
      span.style.fontFamily = b;
      base[b] = { w: span.offsetWidth, h: span.offsetHeight };
    });

    var found = [], metrics = {};
    TEST.forEach(function (f) {
      var hit = false, m = {};
      BASE.forEach(function (b) {
        span.style.fontFamily = "'" + f + "'," + b;
        m[b] = { w: span.offsetWidth, h: span.offsetHeight };
        if (span.offsetWidth !== base[b].w || span.offsetHeight !== base[b].h) hit = true;
      });
      // Metrics for every candidate, present or not: the widths of the misses
      // describe the fallback font, which is itself identifying.
      metrics[f] = m;
      if (hit) found.push(f);
    });
    span.remove();
    return { baseline: base, detected: found, metrics: metrics };
  });

  probe("fonts.query_api", { deterministic: true }, function () {
    must(document.fonts && document.fonts.check, "FontFaceSet unsupported");
    var out = {};
    ["12px Arial", "12px 'Segoe UI'", "12px Menlo", "12px Roboto", "12px 'Noto Color Emoji'"]
      .forEach(function (f) { out[f] = document.fonts.check(f); });
    out.size = document.fonts.size;
    out.status = document.fonts.status;
    return out;
  });

  probe("css.system", { deterministic: true }, function () {
    var COLORS = ["ActiveBorder", "ActiveCaption", "ActiveText", "AppWorkspace", "Background",
      "ButtonBorder", "ButtonFace", "ButtonHighlight", "ButtonShadow", "ButtonText", "Canvas",
      "CanvasText", "CaptionText", "Field", "FieldText", "GrayText", "Highlight", "HighlightText",
      "InactiveBorder", "InactiveCaption", "InactiveCaptionText", "InfoBackground", "InfoText",
      "LinkText", "Mark", "MarkText", "Menu", "MenuText", "Scrollbar", "ThreeDDarkShadow",
      "ThreeDFace", "ThreeDHighlight", "ThreeDLightShadow", "ThreeDShadow", "VisitedText", "Window",
      "WindowFrame", "WindowText"];
    var FONTS = ["caption", "icon", "menu", "message-box", "small-caption", "status-bar"];
    var el = document.createElement("div");
    el.style.cssText = "position:absolute;left:-10000px";
    document.body.appendChild(el);
    var colors = {}, fonts = {};
    COLORS.forEach(function (c) {
      el.style.color = ""; el.style.color = c;
      colors[c] = getComputedStyle(el).color;
    });
    el.style.color = "";
    FONTS.forEach(function (f) {
      el.style.font = ""; el.style.font = f;
      var cs = getComputedStyle(el);
      fonts[f] = { fontFamily: cs.fontFamily, fontSize: cs.fontSize, fontWeight: cs.fontWeight, fontStyle: cs.fontStyle };
    });
    el.remove();
    return { colors: colors, fonts: fonts };
  });

  probe("css.media", { deterministic: true }, function () {
    var Q = {
      "any-hover": ["none", "hover"], "any-pointer": ["none", "coarse", "fine"],
      "hover": ["none", "hover"], "pointer": ["none", "coarse", "fine"],
      "color-gamut": ["srgb", "p3", "rec2020"],
      "prefers-color-scheme": ["light", "dark"],
      "prefers-reduced-motion": ["no-preference", "reduce"],
      "prefers-contrast": ["no-preference", "more", "less", "custom"],
      "forced-colors": ["none", "active"], "inverted-colors": ["none", "inverted"],
      "display-mode": ["browser", "standalone", "fullscreen", "minimal-ui"],
      "dynamic-range": ["standard", "high"], "scripting": ["none", "initial-only", "enabled"],
      "orientation": ["portrait", "landscape"], "update": ["none", "slow", "fast"],
      "overflow-block": ["none", "scroll", "optional-paged", "paged"]
    };
    var out = {};
    Object.keys(Q).forEach(function (feat) {
      out[feat] = Q[feat].filter(function (v) { return matchMedia("(" + feat + ":" + v + ")").matches; });
    });
    // Integer equality only ever finds integer values, so every fractional
    // device-pixel-ratio reported null. These are range features: bisect on
    // min-* instead, which is also how a detector reads them without ever
    // touching window.devicePixelRatio.
    ["color", "color-index", "monochrome"].forEach(function (f) {
      var v = null;
      for (var i = 0; i <= 64; i++) { if (matchMedia("(" + f + ":" + i + ")").matches) { v = i; break; } }
      out[f] = v;
    });

    function bisect(query, lo, hi, unit, iterations) {
      // WebKit-prefixed features take the prefix before "min", not after:
      // -webkit-min-device-pixel-ratio, never min--webkit-device-pixel-ratio.
      var minName = query.indexOf("-webkit-") === 0
          ? "-webkit-min-" + query.slice(8)
          : "min-" + query;
      if (!matchMedia("(" + minName + ":" + lo + (unit || "") + ")").matches) return null;
      for (var i = 0; i < iterations; i++) {
        var mid = (lo + hi) / 2;
        if (matchMedia("(" + minName + ":" + mid + (unit || "") + ")").matches) {
          lo = mid;
        } else {
          hi = mid;
        }
      }
      return lo;
    }
    // 40 iterations resolves far below any value a display reports, which is
    // the point: the precision recoverable this way is the fingerprint.
    out["-webkit-device-pixel-ratio"] =
        bisect("-webkit-device-pixel-ratio", 0, 16, "", 40);
    out["resolution"] = bisect("resolution", 0, 2000, "dppx", 40);
    return out;
  });

  // --------------------------------------------------------------- platform

  probe("api.surface", { deterministic: true }, function () {
    var NAMES = ("SharedWorker,ServiceWorker,WebHID,Serial,USB,Bluetooth,NDEFReader,ContactsManager," +
      "PaymentRequest,CredentialsContainer,MediaSession,Gyroscope,Accelerometer,Magnetometer," +
      "AmbientLightSensor,OrientationSensor,DeviceOrientationEvent,DeviceMotionEvent,BatteryManager," +
      "IdleDetector,Notification,PushManager,SpeechSynthesis,SpeechRecognition,WebAssembly," +
      "OffscreenCanvas,ImageDecoder,VideoDecoder,AudioDecoder,MediaRecorder,RTCPeerConnection," +
      "showDirectoryPicker,showOpenFilePicker,launchQueue,documentPictureInPicture,EyeDropper," +
      "CompressionStream,ReportingObserver,Scheduler,NavigationPreloadManager,VirtualKeyboard," +
      "CookieStore,BarcodetDetector,FaceDetector,TextDetector,PressureObserver,Sanitizer," +
      "GPUAdapter,XRSystem,Keyboard,Clipboard,Lock,StorageBucket").split(",");
    var out = {};
    NAMES.forEach(function (n) {
      out[n] = (n in global) || (n in navigator) ||
        (n.charAt(0) === n.charAt(0).toLowerCase() && n in navigator);
    });
    out.chrome = typeof global.chrome === "object" ? Object.keys(global.chrome) : null;
    out.chrome_runtime = !!(global.chrome && global.chrome.runtime);
    out.chrome_app = !!(global.chrome && global.chrome.app);
    return out;
  });

  probe("native_code.toString", { deterministic: true }, function () {
    return {
      Object: Function.prototype.toString.call(Object),
      getContext: Function.prototype.toString.call(HTMLCanvasElement.prototype.getContext),
      toDataURL: Function.prototype.toString.call(HTMLCanvasElement.prototype.toDataURL),
      hardwareConcurrency: (function () {
        var d = Object.getOwnPropertyDescriptor(Navigator.prototype, "hardwareConcurrency");
        return d && d.get ? Function.prototype.toString.call(d.get) : null;
      })(),
      functionToString: Function.prototype.toString.call(Function.prototype.toString),
      errorStackShape: (function () { try { null.x(); } catch (e) { return e.stack.split("\n").length; } })()
    };
  });

  probe("codecs.media", { deterministic: true }, async function () {
    var VIDEO = [
      { contentType: 'video/mp4; codecs="avc1.42E01E"', width: 1920, height: 1080, bitrate: 2000000, framerate: 30 },
      { contentType: 'video/webm; codecs="vp09.00.10.08"', width: 1920, height: 1080, bitrate: 2000000, framerate: 30 },
      { contentType: 'video/mp4; codecs="hev1.1.6.L93.B0"', width: 1920, height: 1080, bitrate: 2000000, framerate: 30 },
      { contentType: 'video/mp4; codecs="av01.0.05M.08"', width: 1920, height: 1080, bitrate: 2000000, framerate: 30 }
    ];
    var AUDIO = [
      { contentType: 'audio/mp4; codecs="mp4a.40.2"', channels: 2, bitrate: 300000, samplerate: 44100 },
      { contentType: "audio/ogg; codecs=vorbis", channels: 2, bitrate: 300000, samplerate: 44100 },
      { contentType: "audio/ogg; codecs=opus", channels: 2, bitrate: 300000, samplerate: 48000 },
      { contentType: 'audio/mpeg; codecs="mp3"', channels: 2, bitrate: 300000, samplerate: 44100 }
    ];
    var out = { decodingInfo: [], canPlayType: {} };
    must(navigator.mediaCapabilities, "mediaCapabilities unsupported");
    for (var cfg of VIDEO) {
      out.decodingInfo.push({ config: cfg, result: await navigator.mediaCapabilities.decodingInfo({ type: "file", video: cfg }) });
    }
    for (var acfg of AUDIO) {
      out.decodingInfo.push({ config: acfg, result: await navigator.mediaCapabilities.decodingInfo({ type: "file", audio: acfg }) });
    }
    var v = document.createElement("video"), a = document.createElement("audio");
    ['video/mp4; codecs="avc1.42E01E"', "video/webm", "application/vnd.apple.mpegurl", "video/ogg"]
      .forEach(function (t) { out.canPlayType[t] = v.canPlayType(t); });
    ['audio/mpeg', 'audio/ogg; codecs="vorbis"', 'audio/wav; codecs="1"']
      .forEach(function (t) { out.canPlayType[t] = a.canPlayType(t); });
    return out;
  });

  probe("speech.voices", { deterministic: false }, async function () {
    must(global.speechSynthesis, "speechSynthesis unsupported");
    var voices = speechSynthesis.getVoices();
    if (!voices.length) {
      // The list populates asynchronously on some platforms. Wait, then report
      // an empty list honestly if it stays empty.
      voices = await new Promise(function (resolve) {
        var done = false;
        var t = setTimeout(function () { if (!done) { done = true; resolve(speechSynthesis.getVoices()); } }, 3000);
        speechSynthesis.onvoiceschanged = function () {
          if (done) return;
          done = true; clearTimeout(t); resolve(speechSynthesis.getVoices());
        };
      });
    }
    return voices.map(function (v) {
      return { name: v.name, lang: v.lang, localService: v.localService, voiceURI: v.voiceURI, default: v.default };
    });
  });

  probe("webrtc.capabilities", { deterministic: true }, function () {
    must(global.RTCRtpReceiver && RTCRtpReceiver.getCapabilities, "RTCRtpReceiver.getCapabilities unsupported");
    return {
      receiver: { audio: RTCRtpReceiver.getCapabilities("audio"), video: RTCRtpReceiver.getCapabilities("video") },
      sender: { audio: RTCRtpSender.getCapabilities("audio"), video: RTCRtpSender.getCapabilities("video") }
    };
  });

  probe("media.devices", { deterministic: true }, async function () {
    must(navigator.mediaDevices && navigator.mediaDevices.enumerateDevices, "enumerateDevices unsupported");
    var devices = await navigator.mediaDevices.enumerateDevices();
    return {
      // Labels stay empty without permission; counts by kind are the fingerprint.
      counts: devices.reduce(function (a, d) { a[d.kind] = (a[d.kind] || 0) + 1; return a; }, {}),
      hasLabels: devices.some(function (d) { return !!d.label; }),
      supportedConstraints: navigator.mediaDevices.getSupportedConstraints()
    };
  });

  probe("storage.estimate", { deterministic: false }, async function () {
    must(navigator.storage && navigator.storage.estimate, "storage.estimate unsupported");
    var est = await navigator.storage.estimate();
    return {
      quota: est.quota, usage: est.usage,
      usageDetails: est.usageDetails || null,
      persisted: navigator.storage.persisted ? await navigator.storage.persisted() : null
    };
  });

  probe("memory.heap", { deterministic: false }, function () {
    must(performance.memory, "performance.memory unsupported");
    return {
      jsHeapSizeLimit: performance.memory.jsHeapSizeLimit,
      totalJSHeapSize: performance.memory.totalJSHeapSize,
      usedJSHeapSize: performance.memory.usedJSHeapSize,
      deviceMemory: navigator.deviceMemory
    };
  });

  probe("network.connection", { deterministic: false }, function () {
    var c = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
    must(c, "NetworkInformation unsupported");
    return {
      type: c.type, effectiveType: c.effectiveType, downlink: c.downlink,
      downlinkMax: c.downlinkMax, rtt: c.rtt, saveData: c.saveData
    };
  });

  probe("battery", { deterministic: false }, async function () {
    must(navigator.getBattery, "getBattery unsupported");
    var b = await navigator.getBattery();
    return { charging: b.charging, level: b.level, chargingTime: b.chargingTime, dischargingTime: b.dischargingTime };
  });

  probe("permissions.states", { deterministic: true }, async function () {
    must(navigator.permissions, "Permissions API unsupported");
    var NAMES = ["geolocation", "notifications", "camera", "microphone", "midi",
      "background-sync", "persistent-storage", "clipboard-read", "clipboard-write",
      "accelerometer", "gyroscope", "magnetometer", "screen-wake-lock", "payment-handler"];
    var out = {};
    for (var n of NAMES) {
      try { out[n] = (await navigator.permissions.query({ name: n })).state; }
      catch (e) { out[n] = { __error: String(e.message || e) }; }
    }
    // Notification.permission disagreeing with the Permissions API is a classic
    // headless tell, so both are recorded.
    out.__NotificationPermission = global.Notification ? Notification.permission : null;
    return out;
  });

  probe("keyboard.layout", { deterministic: true }, async function () {
    must(navigator.keyboard && navigator.keyboard.getLayoutMap, "keyboard.getLayoutMap unsupported");
    var map = await navigator.keyboard.getLayoutMap();
    var out = {};
    map.forEach(function (v, k) { out[k] = v; });
    return out;
  });

  probe("touch", { deterministic: true }, function () {
    return {
      maxTouchPoints: navigator.maxTouchPoints,
      touchEventInWindow: "ontouchstart" in global,
      TouchEvent: typeof global.TouchEvent,
      pointerEvent: typeof global.PointerEvent,
      touchConstructor: (function () { try { new Touch({ identifier: 0, target: document.body }); return true; } catch (e) { return String(e.name); } })()
    };
  });

  probe("wasm", { deterministic: true }, async function () {
    must(global.WebAssembly, "WebAssembly unsupported");
    // Minimal valid module: exports a function returning 42.
    var bytes = new Uint8Array([0,97,115,109,1,0,0,0,1,5,1,96,0,1,127,3,2,1,0,7,5,1,1,102,0,0,10,6,1,4,0,65,42,11]);
    var mod = await WebAssembly.instantiate(bytes);
    var feats = {};
    // Each is a minimal module using exactly one post-MVP feature. Which of
    // these a build accepts is a property of the V8 it was compiled from.
    var TESTS = {
      bulkMemory: [0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,5,3,1,0,1,10,14,1,12,0,65,0,65,0,65,0,252,10,0,0,11],
      simd:       [0,97,115,109,1,0,0,0,1,5,1,96,0,1,123,3,2,1,0,10,8,1,6,0,65,0,253,15,11],
      threads:    [0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,5,4,1,3,1,1,10,11,1,9,0,65,0,254,16,2,0,26,11],
      exceptions: [0,97,115,109,1,0,0,0,1,4,1,96,0,0,3,2,1,0,10,8,1,6,0,6,64,25,11,11]
    };
    Object.keys(TESTS).forEach(function (k) {
      try { feats[k] = WebAssembly.validate(new Uint8Array(TESTS[k])); }
      catch (e) { feats[k] = false; }
    });
    return { instantiates: mod.instance.exports.f() === 42, features: feats };
  });

  probe("timing.resolution", { deterministic: false }, function () {
    var samples = [];
    for (var i = 0; i < 2000; i++) {
      var a = performance.now(), b = performance.now();
      while (b === a) b = performance.now();
      samples.push(b - a);
    }
    samples.sort(function (x, y) { return x - y; });
    return {
      minDelta: samples[0],
      medianDelta: samples[Math.floor(samples.length / 2)],
      timeOrigin: typeof performance.timeOrigin === "number",
      dateNowResolution: (function () {
        var t = Date.now(), n = Date.now();
        while (n === t) n = Date.now();
        return n - t;
      })()
    };
  });

  probe("math.precision", { deterministic: true }, function () {
    // Transcendental implementations differ by libm and by V8 build.
    var out = {};
    [["acos",0.123],["acosh",1.5],["asin",0.123],["asinh",1.5],["atan",0.5],["atanh",0.5],
     ["cbrt",100],["cos",1e-7],["cosh",1],["exp",1],["expm1",1],["log",10],["log1p",10],
     ["log10",1e3],["sin",1e-7],["sinh",1],["sqrt",2],["tan",1e-7],["tanh",1],["pow",null]]
      .forEach(function (t) {
        try { out[t[0]] = t[0] === "pow" ? Math.pow(Math.PI, -100) : Math[t[0]](t[1]); }
        catch (e) { out[t[0]] = null; }
      });
    return out;
  });

  probe("headers.echo", { deterministic: true }, async function () {
    // The receiver reflects what it actually saw on the wire, including
    // Client Hints, header order and casing that JavaScript cannot read.
    var res = await fetch("/echo", { headers: { "X-Apostate-Probe": "1" } });
    must(res.ok, "echo endpoint returned " + res.status);
    return await res.json();
  });

  probe("headers.echo_worker", { deterministic: true }, async function () {
    // Whether a worker-initiated fetch carries Client Hints cannot be answered
    // from the renderer's own view; only the server sees what arrived. A
    // difference against headers.echo is a real divergence between scopes.
    must(global.Worker, "Worker unsupported");
    var echoUrl = new URL("/echo", location.href).href;
    var src = "self.onmessage=function(e){" +
      "fetch(e.data).then(function(r){return r.json()}).then(function(j){postMessage(j)})" +
      ".catch(function(err){postMessage({__error:String(err&&err.message||err)})})};";
    var url = URL.createObjectURL(new Blob([src], { type: "text/javascript" }));
    try {
      var w = new Worker(url);
      var result = await new Promise(function (resolve, reject) {
        var t = setTimeout(function () { reject(new Error("worker fetch timed out")); }, 5000);
        w.onmessage = function (e) { clearTimeout(t); resolve(e.data); };
        w.onerror = function (e) { clearTimeout(t); reject(new Error(e.message || "worker error")); };
        w.postMessage(echoUrl);
      });
      w.terminate();
      return result;
    } finally {
      URL.revokeObjectURL(url);
    }
  });

  probe("screen.details", { deterministic: true }, async function () {
    // getScreenDetails exposes the OS display label, HDR headroom and colour
    // primaries that no other API surfaces, and its devicePixelRatio is the raw
    // scale factor rather than the zoom-multiplied one.
    must(global.getScreenDetails, "getScreenDetails unsupported");
    var state = navigator.permissions
      ? (await navigator.permissions.query({ name: "window-management" })).state
      : "unknown";
    if (state !== "granted") {
      // Requesting would prompt, and a prompt during capture changes what is
      // being measured. Report the gap instead of silently returning nothing.
      throw new Error("window-management permission is '" + state + "'; grant it and re-capture");
    }
    var details = await global.getScreenDetails();
    return {
      screenCount: details.screens.length,
      currentIsPrimary: details.currentScreen.isPrimary,
      screens: details.screens.map(function (s) {
        return {
          label: s.label, isPrimary: s.isPrimary, isInternal: s.isInternal,
          devicePixelRatio: s.devicePixelRatio,
          width: s.width, height: s.height,
          availWidth: s.availWidth, availHeight: s.availHeight,
          left: s.left, top: s.top, colorDepth: s.colorDepth,
          hdrHeadroom: typeof s.highDynamicRangeHeadroom === "number"
            ? s.highDynamicRangeHeadroom : null,
          redPrimaryX: s.redPrimaryX, redPrimaryY: s.redPrimaryY,
          greenPrimaryX: s.greenPrimaryX, greenPrimaryY: s.greenPrimaryY,
          bluePrimaryX: s.bluePrimaryX, bluePrimaryY: s.bluePrimaryY,
          whitePointX: s.whitePointX, whitePointY: s.whitePointY
        };
      })
    };
  });

  // ---------------------------------------------------------------- context

  function automationSignals() {
    var sig = [];
    if (navigator.webdriver) sig.push("navigator.webdriver");
    if (/Headless/i.test(navigator.userAgent)) sig.push("UA contains Headless");
    ["__driver_evaluate", "__webdriver_evaluate", "__selenium_evaluate", "__fxdriver_evaluate",
     "_Selenium_IDE_Recorder", "callSelenium", "_selenium", "__nightmare", "__playwright",
     "__puppeteer_utility_world__", "cdc_adoQpoasnfa76pfcZLmcfl_Array"].forEach(function (k) {
      if (k in global || k in document) sig.push(k);
    });
    if (navigator.plugins.length === 0 && /Chrome/.test(navigator.userAgent) && !/Android/.test(navigator.userAgent)) {
      sig.push("desktop Chrome with zero plugins");
    }
    return sig;
  }

  // ------------------------------------------------------------------- main

  async function collect(label, onProgress) {
    var probes = {}, repeat = {};
    var total = registry.length + registry.filter(function (e) { return e.deterministic; }).length;
    var done = 0;

    for (var entry of registry) {
      probes[entry.id] = await run(entry);
      if (onProgress) onProgress(++done, total, entry.id);
    }
    // Second pass over deterministic probes only. Divergence between the two is
    // the measurement that tells us whether a field is stable on real hardware.
    for (var d of registry) {
      if (!d.deterministic) continue;
      repeat[d.id] = await run(d);
      if (onProgress) onProgress(++done, total, d.id + " (repeat)");
    }

    var sig = automationSignals();
    return {
      capture_version: CAPTURE_VERSION,
      context: {
        taken_at: new Date().toISOString(),
        label: label || null,
        ua: navigator.userAgent,
        collector_sha256: global.__APOSTATE_COLLECTOR_SHA256 || "unknown",
        device_pixel_ratio: global.devicePixelRatio,
        headed: typeof global.outerWidth === "number" && global.outerWidth > 0 ? true : null,
        automation_suspected: sig.length > 0,
        automation_signals: sig
      },
      probes: probes,
      repeat: repeat
    };
  }

  global.ApostateCollector = { collect: collect, probeCount: function () { return registry.length; } };
})(window);
