(function () {
  const intro = document.getElementById('findit-intro');
  const canvas = document.getElementById('globeCanvas');
  const exploreBtn = document.getElementById('exploreFindItBtn');
  const skipBtn = document.getElementById('skipIntroBtn');

  if (!intro || !canvas) {
    return;
  }

  const ctx = canvas.getContext('2d');
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const hideDelay = prefersReducedMotion ? 0 : 3600;
  let animationFrame = null;

  if (sessionStorage.getItem('findit_intro_dismissed') === 'true') {
    intro.classList.remove('visible');
    return;
  }

  const landShapes = [
    [[-0.98, -0.14], [-0.9, -0.28], [-0.82, -0.38], [-0.71, -0.4], [-0.64, -0.28], [-0.58, -0.16], [-0.56, -0.02], [-0.6, 0.12], [-0.68, 0.2], [-0.76, 0.32], [-0.82, 0.44], [-0.9, 0.48], [-0.96, 0.3], [-0.98, 0.12]],
    [[-0.46, -0.72], [-0.34, -0.82], [-0.2, -0.7], [-0.12, -0.46], [-0.1, -0.2], [-0.18, 0.06], [-0.26, 0.18], [-0.38, 0.38], [-0.5, 0.52], [-0.56, 0.38], [-0.54, 0.16], [-0.5, -0.04], [-0.48, -0.3], [-0.5, -0.56]],
    [[-0.08, -0.24], [0.12, -0.34], [0.26, -0.24], [0.36, -0.14], [0.54, -0.08], [0.7, -0.06], [0.84, 0.02], [0.92, 0.12], [0.9, 0.26], [0.8, 0.36], [0.7, 0.48], [0.54, 0.54], [0.38, 0.64], [0.18, 0.6], [0.08, 0.38], [0.04, 0.2], [-0.02, 0.06], [-0.08, -0.1]],
    [[0.26, -0.08], [0.32, -0.18], [0.42, -0.28], [0.5, -0.48], [0.54, -0.62], [0.5, -0.76], [0.42, -0.82], [0.32, -0.72], [0.24, -0.52], [0.18, -0.32], [0.18, -0.12], [0.22, 0.04], [0.3, 0.12], [0.34, 0.24], [0.3, 0.38], [0.2, 0.46], [0.12, 0.36], [0.12, 0.2]],
    [[-0.72, 0.52], [-0.64, 0.44], [-0.58, 0.5], [-0.6, 0.62], [-0.68, 0.7], [-0.76, 0.66], [-0.78, 0.58]],
    [[0.66, 0.52], [0.78, 0.46], [0.9, 0.52], [0.94, 0.66], [0.82, 0.78], [0.7, 0.72], [0.62, 0.6]]
  ];

  const markers = [
    { x: 0.35, y: 0.28, glow: '#7dd3fc' },
    { x: 0.62, y: 0.38, glow: '#8ae5ff' },
    { x: 0.18, y: 0.58, glow: '#5eead4' },
    { x: 0.74, y: 0.18, glow: '#8ae5ff' },
    { x: 0.48, y: 0.72, glow: '#93c5fd' }
  ];

  const connections = [
    { from: markers[0], to: markers[1] },
    { from: markers[1], to: markers[2] },
    { from: markers[2], to: markers[4] },
    { from: markers[3], to: markers[4] }
  ];

  const stars = Array.from({ length: 120 }, () => ({
    x: Math.random(),
    y: Math.random(),
    r: Math.random() * 2.2 + 0.6,
    alpha: Math.random() * 0.7 + 0.22
  }));

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const size = Math.min(rect.width || 400, rect.height || 400);
    const ratio = window.devicePixelRatio || 1;
    canvas.width = size * ratio;
    canvas.height = size * ratio;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  function projectPoint(x, y, radius) {
    const angle = (x * Math.PI) - Math.PI / 2;
    const lat = (y * Math.PI) / 2;
    const cosLat = Math.cos(lat);
    return {
      x: radius * Math.cos(angle) * cosLat,
      y: radius * Math.sin(lat),
      z: radius * Math.sin(angle) * cosLat
    };
  }

  function drawBackground(globeRadius) {
    const width = canvas.width / (window.devicePixelRatio || 1);
    const height = canvas.height / (window.devicePixelRatio || 1);
    const cx = width / 2;
    const cy = height / 2;

    ctx.clearRect(0, 0, width, height);

    const bg = ctx.createRadialGradient(cx, cy, 0, cx, cy, globeRadius * 1.8);
    bg.addColorStop(0, 'rgba(18, 82, 168, 0.38)');
    bg.addColorStop(0.35, 'rgba(9, 20, 38, 0.28)');
    bg.addColorStop(1, 'rgba(2, 6, 23, 0)');
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, width, height);

    for (const star of stars) {
      const sx = star.x * width;
      const sy = star.y * height;
      ctx.beginPath();
      ctx.fillStyle = `rgba(255,255,255,${star.alpha})`;
      ctx.arc(sx, sy, star.r, 0, Math.PI * 2);
      ctx.fill();
    }

    const halo = ctx.createRadialGradient(cx, cy, globeRadius * 0.28, cx, cy, globeRadius * 1.5);
    halo.addColorStop(0, 'rgba(125,211,252,0.16)');
    halo.addColorStop(0.6, 'rgba(96,165,250,0.08)');
    halo.addColorStop(1, 'rgba(15, 23, 42, 0)');
    ctx.fillStyle = halo;
    ctx.fillRect(0, 0, width, height);
  }

  function drawLatitudeBands(rotation, radius) {
    ctx.save();
    ctx.strokeStyle = 'rgba(191, 219, 254, 0.18)';
    ctx.lineWidth = 1;

    const latitudes = [-0.72, -0.42, 0, 0.42, 0.72];
    for (const lat of latitudes) {
      ctx.beginPath();
      for (let angle = -Math.PI; angle <= Math.PI + 0.05; angle += 0.08) {
        const x = Math.cos(angle + rotation * 0.55) * Math.cos(lat) * radius;
        const y = Math.sin(lat) * radius;
        if (angle === -Math.PI) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.stroke();
    }

    const longitudes = [-1.4, -0.8, -0.2, 0.4, 1.0];
    for (const lon of longitudes) {
      ctx.beginPath();
      for (let t = -Math.PI / 2; t <= Math.PI / 2 + 0.05; t += 0.08) {
        const x = Math.cos(lon + rotation * 0.8) * Math.cos(t) * radius;
        const y = Math.sin(t) * radius;
        if (t === -Math.PI / 2) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.stroke();
    }

    ctx.restore();
  }

  function drawGlobe(rotation) {
    const width = canvas.width / (window.devicePixelRatio || 1);
    const height = canvas.height / (window.devicePixelRatio || 1);
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.min(width, height) * 0.42;

    ctx.save();
    ctx.translate(cx, cy);

    const ocean = ctx.createRadialGradient(-radius * 0.4, -radius * 0.38, radius * 0.18, 0, 0, radius * 1.22);
    ocean.addColorStop(0, '#d9f4ff');
    ocean.addColorStop(0.18, '#7ed0ff');
    ocean.addColorStop(0.42, '#1d5bd8');
    ocean.addColorStop(0.78, '#0c2b6b');
    ocean.addColorStop(1, '#020b1a');
    ctx.beginPath();
    ctx.fillStyle = ocean;
    ctx.arc(0, 0, radius, 0, Math.PI * 2);
    ctx.fill();

    const shadedBand = ctx.createRadialGradient(radius * 0.72, -radius * 0.55, radius * 0.08, 0, 0, radius * 1.1);
    shadedBand.addColorStop(0, 'rgba(255,255,255,0.12)');
    shadedBand.addColorStop(0.3, 'rgba(255,255,255,0.04)');
    shadedBand.addColorStop(1, 'rgba(2, 11, 26, 0.38)');
    ctx.beginPath();
    ctx.fillStyle = shadedBand;
    ctx.arc(0, 0, radius, 0, Math.PI * 2);
    ctx.fill();

    const atmosphericRim = ctx.createRadialGradient(0, 0, radius * 0.78, 0, 0, radius * 1.18);
    atmosphericRim.addColorStop(0, 'rgba(125, 211, 252, 0.12)');
    atmosphericRim.addColorStop(0.7, 'rgba(96, 165, 250, 0.06)');
    atmosphericRim.addColorStop(1, 'rgba(96, 165, 250, 0.18)');
    ctx.beginPath();
    ctx.fillStyle = atmosphericRim;
    ctx.arc(0, 0, radius * 1.06, 0, Math.PI * 2);
    ctx.fill();

    drawLatitudeBands(rotation, radius);

    ctx.beginPath();
    ctx.arc(0, 0, radius * 1.03, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(125, 211, 252, 0.65)';
    ctx.lineWidth = 1.2;
    ctx.stroke();

    const cloudGroups = [
      { cx: -radius * 0.24, cy: -radius * 0.18, rx: radius * 0.19, ry: radius * 0.12, alpha: 0.08 },
      { cx: radius * 0.12, cy: radius * 0.22, rx: radius * 0.17, ry: radius * 0.1, alpha: 0.1 },
      { cx: radius * 0.42, cy: -radius * 0.1, rx: radius * 0.2, ry: radius * 0.11, alpha: 0.08 }
    ];

    for (const cloud of cloudGroups) {
      ctx.beginPath();
      ctx.ellipse(cloud.cx, cloud.cy, cloud.rx, cloud.ry, Math.PI / 6, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255,255,255,${cloud.alpha})`;
      ctx.fill();
    }

    for (const polygon of landShapes) {
      ctx.beginPath();
      const points = polygon.map(([x, y]) => {
        const p = projectPoint(x + rotation * 0.7, y, radius);
        return { x: p.x, y: p.y };
      });
      ctx.moveTo(points[0].x, points[0].y);
      for (let i = 1; i < points.length; i++) {
        ctx.lineTo(points[i].x, points[i].y);
      }
      ctx.closePath();

      const landGrad = ctx.createRadialGradient(-radius * 0.1, -radius * 0.2, radius * 0.1, 0, 0, radius * 1.2);
      landGrad.addColorStop(0, 'rgba(156, 232, 177, 0.95)');
      landGrad.addColorStop(0.5, 'rgba(104, 180, 140, 0.9)');
      landGrad.addColorStop(1, 'rgba(59, 118, 94, 0.94)');
      ctx.fillStyle = landGrad;
      ctx.fill();

      ctx.strokeStyle = 'rgba(214, 255, 233, 0.18)';
      ctx.lineWidth = 0.8;
      ctx.stroke();
    }

    for (const connection of connections) {
      const from = projectPoint(connection.from.x + rotation * 0.9, connection.from.y, radius);
      const to = projectPoint(connection.to.x + rotation * 1.08, connection.to.y, radius);
      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.quadraticCurveTo(0, 0, to.x, to.y);
      ctx.strokeStyle = 'rgba(125, 211, 252, 0.5)';
      ctx.lineWidth = 1;
      ctx.shadowBlur = 8;
      ctx.shadowColor = 'rgba(125, 211, 252, 0.7)';
      ctx.stroke();
      ctx.shadowBlur = 0;
    }

    for (const marker of markers) {
      const p = projectPoint(marker.x + rotation * 0.9, marker.y, radius);
      const pulse = 4 + (Math.sin(performance.now() / 450 + marker.x) + 1) * 5;
      ctx.beginPath();
      ctx.fillStyle = marker.glow;
      ctx.shadowBlur = 14;
      ctx.shadowColor = marker.glow;
      ctx.arc(p.x, p.y, 3.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;

      ctx.beginPath();
      ctx.strokeStyle = 'rgba(186,230,253,0.7)';
      ctx.lineWidth = 1;
      ctx.arc(p.x, p.y, pulse, 0, Math.PI * 2);
      ctx.stroke();
    }

    ctx.restore();
  }

  function renderFrame(time) {
    drawBackground(Math.min(canvas.width, canvas.height) * 0.5);
    const phase = time / 1200;
    drawGlobe(phase % (Math.PI * 2));
    if (!prefersReducedMotion) {
      animationFrame = window.requestAnimationFrame(renderFrame);
    }
  }

  function dismissIntro() {
    intro.classList.remove('visible');
    sessionStorage.setItem('findit_intro_dismissed', 'true');
    if (animationFrame) {
      window.cancelAnimationFrame(animationFrame);
    }
  }

  function showIntro() {
    intro.classList.add('visible');
  }

  function bindEvents() {
    if (exploreBtn) {
      exploreBtn.addEventListener('click', dismissIntro);
    }
    if (skipBtn) {
      skipBtn.addEventListener('click', dismissIntro);
    }
  }

  resizeCanvas();
  bindEvents();
  showIntro();

  if (prefersReducedMotion) {
    drawBackground(Math.min(canvas.width, canvas.height) * 0.5);
    drawGlobe(0.4);
  } else {
    animationFrame = window.requestAnimationFrame(renderFrame);
    window.setTimeout(dismissIntro, hideDelay);
  }

  window.addEventListener('resize', resizeCanvas);
})();
