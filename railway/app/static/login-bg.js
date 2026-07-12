/*
 * Sagefrog login-page ambient background.
 *
 * A subtle, slow-drifting particle field rendered behind the sign-in card.
 * Purely decorative and fully self-contained:
 *   - Loads three.js only after passing the guards below, so users with
 *     reduced-motion enabled or no WebGL never download it.
 *   - Renders into its own fixed <canvas> that sits *above* the existing CSS
 *     gradient but *below* the login card (z-index:-1, pointer-events:none).
 *   - If anything at all goes wrong, it removes the canvas and the page falls
 *     back to exactly the static gradient it has always had.
 *   - Pauses when the tab is hidden to avoid burning battery.
 *
 * This script only runs on the login page, where nothing is being read.
 */
(function () {
  "use strict";

  // --- Guards: bail out before loading three.js -----------------------------

  // Respect users who asked for less motion.
  try {
    if (window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return;
    }
  } catch (e) { /* matchMedia missing -> continue */ }

  // Cheap WebGL capability probe so we never inject three.js on a browser that
  // can't use it.
  function hasWebGL() {
    try {
      var c = document.createElement("canvas");
      return !!(window.WebGLRenderingContext &&
        (c.getContext("webgl") || c.getContext("experimental-webgl")));
    } catch (e) {
      return false;
    }
  }
  if (!hasWebGL()) return;

  // --- Lazy-load three.js, then start ---------------------------------------

  var s = document.createElement("script");
  s.src = "/static/vendor/three.min.js";
  s.async = true;
  s.onload = function () {
    try {
      start();
    } catch (e) {
      // Never let a rendering error touch the page.
      if (window.console && console.warn) console.warn("login-bg disabled:", e);
    }
  };
  s.onerror = function () { /* asset missing -> stay on the gradient */ };
  document.head.appendChild(s);

  // --- The effect -----------------------------------------------------------

  function start() {
    if (!window.THREE) return;
    var THREE = window.THREE;

    var canvas = document.createElement("canvas");
    canvas.id = "bgfx";
    canvas.setAttribute("aria-hidden", "true");
    var st = canvas.style;
    st.position = "fixed";
    st.top = "0"; st.left = "0";
    st.width = "100%"; st.height = "100%";
    st.zIndex = "-1";          // above body gradient, below the card
    st.pointerEvents = "none"; // never intercept clicks/taps
    st.display = "block";
    document.body.appendChild(canvas);

    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas: canvas, antialias: true, alpha: true,
        powerPreference: "low-power",
      });
    } catch (e) {
      canvas.remove();
      return;
    }
    // Alpha 0 clear colour: the body's CSS gradient shows straight through.
    renderer.setClearColor(0x000000, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));

    var scene = new THREE.Scene();
    var camera = new THREE.PerspectiveCamera(60, 1, 1, 1000);
    camera.position.z = 60;

    // Build a soft point cloud coloured between Sagefrog's green and blue.
    var COUNT = 650;
    var positions = new Float32Array(COUNT * 3);
    var colors = new Float32Array(COUNT * 3);
    var green = new THREE.Color(0x34b27b);
    var blue = new THREE.Color(0x2563eb);
    var tmp = new THREE.Color();
    for (var i = 0; i < COUNT; i++) {
      positions[i * 3]     = (Math.random() - 0.5) * 140;
      positions[i * 3 + 1] = (Math.random() - 0.5) * 100;
      positions[i * 3 + 2] = (Math.random() - 0.5) * 120;
      tmp.copy(green).lerp(blue, Math.random());
      colors[i * 3]     = tmp.r;
      colors[i * 3 + 1] = tmp.g;
      colors[i * 3 + 2] = tmp.b;
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));

    // Generate a soft circular sprite so points render as round glows rather
    // than the default hard squares. Kept in-code so there's no extra asset.
    function makeDotTexture() {
      var c = document.createElement("canvas");
      c.width = c.height = 64;
      var ctx = c.getContext("2d");
      var g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
      g.addColorStop(0, "rgba(255,255,255,1)");
      g.addColorStop(0.35, "rgba(255,255,255,0.55)");
      g.addColorStop(1, "rgba(255,255,255,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, 64, 64);
      var tex = new THREE.CanvasTexture(c);
      tex.needsUpdate = true;
      return tex;
    }

    var mat = new THREE.PointsMaterial({
      size: 1.5,
      sizeAttenuation: true,
      vertexColors: true,
      map: makeDotTexture(),
      transparent: true,
      opacity: 0.5,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    var points = new THREE.Points(geo, mat);
    scene.add(points);

    function resize() {
      var w = window.innerWidth, h = window.innerHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    resize();
    window.addEventListener("resize", resize);

    var running = true;
    document.addEventListener("visibilitychange", function () {
      running = !document.hidden;
      if (running) loop();
    });

    var t0 = performance.now();
    function loop() {
      if (!running) return;
      requestAnimationFrame(loop);
      var t = (performance.now() - t0) * 0.00006; // very slow drift
      points.rotation.y = t;
      points.rotation.x = Math.sin(t * 0.6) * 0.12;
      renderer.render(scene, camera);
    }
    loop();
  }
})();
