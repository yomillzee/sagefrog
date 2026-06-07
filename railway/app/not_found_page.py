"""Custom 404 page with a tiny Frogger-style minigame."""

from __future__ import annotations

import html


def render_not_found_page(*, path: str | None = None) -> str:
    safe_path = html.escape(path or "", quote=True)
    path_line = (
        f'<p class="path">Lost hop: <code>{safe_path}</code></p>' if safe_path else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>404 · Sagefrog</title>
  <style>
    :root {{
      --navy: #0a2540;
      --green: #22c55e;
      --green-dark: #16a34a;
      --muted: #64748b;
      --border: #d8dee8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 16px;
      padding: 24px 16px 32px;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: linear-gradient(180deg, #eef1f5 0%, #e2e8f0 100%);
      color: var(--navy);
    }}
    .hero {{
      text-align: center;
      max-width: 420px;
    }}
    h1 {{
      margin: 0 0 6px;
      font-size: clamp(1.6rem, 4vw, 2rem);
      letter-spacing: -0.02em;
    }}
    .sub {{
      margin: 0;
      color: var(--muted);
      font-size: 0.95rem;
      line-height: 1.5;
    }}
    .path {{
      margin: 10px 0 0;
      font-size: 0.85rem;
      color: var(--muted);
    }}
    .path code {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 2px 6px;
      font-size: 0.82rem;
      word-break: break-all;
    }}
    .game-wrap {{
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px;
      box-shadow: 0 10px 40px rgba(10, 37, 64, 0.1);
    }}
    canvas {{
      display: block;
      border-radius: 8px;
      background: #14532d;
      touch-action: none;
    }}
    .hud {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-top: 10px;
      font-size: 0.85rem;
      color: var(--muted);
    }}
    .hud strong {{ color: var(--navy); }}
    .controls {{
      margin: 0;
      font-size: 0.8rem;
      color: var(--muted);
      text-align: center;
    }}
    .links {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      justify-content: center;
    }}
    .links a {{
      color: #0b5cab;
      font-weight: 600;
      text-decoration: none;
    }}
    .links a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="hero">
    <h1>404 — page not found</h1>
    <p class="sub">Help the frog cross traffic and reach the lily pads while you figure out where you wanted to go.</p>
    {path_line}
  </div>

  <div class="game-wrap">
    <canvas id="game" width="360" height="440" aria-label="Frogger minigame"></canvas>
    <div class="hud">
      <span>Score: <strong id="score">0</strong></span>
      <span>Lives: <strong id="lives">3</strong></span>
      <span id="status">Arrow keys to hop</span>
    </div>
  </div>
  <p class="controls">← → ↑ ↓ or WASD · Reach the top row · R to restart</p>

  <div class="links">
    <a href="/login">Sign in</a>
    <a href="/">Home</a>
  </div>

  <script>
    (function () {{
      const canvas = document.getElementById('game');
      const ctx = canvas.getContext('2d');
      const scoreEl = document.getElementById('score');
      const livesEl = document.getElementById('lives');
      const statusEl = document.getElementById('status');

      const COLS = 9;
      const ROWS = 11;
      const CELL = canvas.width / COLS;
      const GOALS = [1, 4, 7];
      const START = {{ col: 4, row: ROWS - 1 }};

      let score = 0;
      let lives = 3;
      let player = {{ ...START }};
      let onLog = null;
      let reachedGoals = new Set();
      let gameOver = false;
      let wonPulse = 0;

      function makeCars() {{
        const lanes = [
          {{ row: 7, speed: 1.4, dir: 1, color: '#ef4444' }},
          {{ row: 8, speed: 2.1, dir: -1, color: '#f59e0b' }},
          {{ row: 9, speed: 1.7, dir: 1, color: '#8b5cf6' }},
        ];
        const cars = [];
        for (const lane of lanes) {{
          for (let i = 0; i < 3; i += 1) {{
            cars.push({{
              row: lane.row,
              x: (i * canvas.width) / 2 + Math.random() * 40,
              w: CELL * 1.35,
              h: CELL * 0.55,
              speed: lane.speed,
              dir: lane.dir,
              color: lane.color,
            }});
          }}
        }}
        return cars;
      }}

      function makeLogs() {{
        const lanes = [
          {{ row: 2, speed: 1.2, dir: 1 }},
          {{ row: 3, speed: 1.8, dir: -1 }},
        ];
        const logs = [];
        for (const lane of lanes) {{
          for (let i = 0; i < 2; i += 1) {{
            logs.push({{
              row: lane.row,
              x: i * (canvas.width / 2) + 10,
              w: CELL * 2.1,
              h: CELL * 0.62,
              speed: lane.speed,
              dir: lane.dir,
            }});
          }}
        }}
        return logs;
      }}

      let cars = makeCars();
      let logs = makeLogs();

      function resetPlayer() {{
        player = {{ ...START }};
        onLog = null;
      }}

      function resetRound() {{
        cars = makeCars();
        logs = makeLogs();
        reachedGoals = new Set();
        resetPlayer();
        gameOver = false;
        statusEl.textContent = 'Arrow keys to hop';
      }}

      function fullReset() {{
        score = 0;
        lives = 3;
        scoreEl.textContent = '0';
        livesEl.textContent = '3';
        resetRound();
      }}

      function rowY(row) {{
        return row * (canvas.height / ROWS);
      }}

      function playerBox() {{
        const pad = CELL * 0.18;
        const x = player.col * CELL + pad;
        const y = rowY(player.row) + pad;
        const size = CELL - pad * 2;
        return {{ x, y, w: size, h: size }};
      }}

      function rectsOverlap(a, b) {{
        return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
      }}

      function die() {{
        lives -= 1;
        livesEl.textContent = String(Math.max(lives, 0));
        if (lives <= 0) {{
          gameOver = true;
          statusEl.textContent = 'Game over — press R';
          return;
        }}
        statusEl.textContent = 'Splats! Try again';
        resetPlayer();
      }}

      function winGoal(col) {{
        if (reachedGoals.has(col)) return;
        reachedGoals.add(col);
        score += 100;
        scoreEl.textContent = String(score);
        statusEl.textContent = 'Nice hop! +100';
        wonPulse = 18;
        if (reachedGoals.size === GOALS.length) {{
          score += 250;
          scoreEl.textContent = String(score);
          statusEl.textContent = 'All pads cleared! +250';
          setTimeout(resetRound, 700);
          return;
        }}
        resetPlayer();
      }}

      function tryMove(dc, dr) {{
        if (gameOver) return;
        const next = {{
          col: Math.max(0, Math.min(COLS - 1, player.col + dc)),
          row: Math.max(0, Math.min(ROWS - 1, player.row + dr)),
        }};
        player = next;
        onLog = null;

        if (player.row === 0) {{
          const col = player.col;
          if (GOALS.includes(col)) {{
            winGoal(col);
          }} else {{
            die();
          }}
        }}
      }}

      function handleKeys(e) {{
        const key = e.key.toLowerCase();
        if (key === 'r') {{
          fullReset();
          return;
        }}
        if (gameOver) return;
        if (['arrowup', 'w'].includes(key)) tryMove(0, -1);
        else if (['arrowdown', 's'].includes(key)) tryMove(0, 1);
        else if (['arrowleft', 'a'].includes(key)) tryMove(-1, 0);
        else if (['arrowright', 'd'].includes(key)) tryMove(1, 0);
        else return;
        e.preventDefault();
      }}

      window.addEventListener('keydown', handleKeys);

      function drawBackground() {{
        for (let row = 0; row < ROWS; row += 1) {{
          const y = rowY(row);
          const h = canvas.height / ROWS;
          if (row === 0) ctx.fillStyle = '#166534';
          else if (row <= 3) ctx.fillStyle = row <= 1 ? '#14532d' : '#1d4ed8';
          else if (row <= 6) ctx.fillStyle = '#374151';
          else ctx.fillStyle = '#15803d';
          ctx.fillRect(0, y, canvas.width, h + 1);

          if (row === 0) {{
            for (const g of GOALS) {{
              const gx = g * CELL + CELL * 0.15;
              const gy = y + CELL * 0.2;
              const filled = reachedGoals.has(g);
              ctx.fillStyle = filled ? '#4ade80' : '#86efac';
              ctx.beginPath();
              ctx.ellipse(gx + CELL * 0.35, gy + CELL * 0.25, CELL * 0.32, CELL * 0.18, 0, 0, Math.PI * 2);
              ctx.fill();
            }}
          }}

          if (row >= 7 && row <= 9) {{
            ctx.strokeStyle = 'rgba(255,255,255,0.12)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(0, y + h / 2);
            ctx.lineTo(canvas.width, y + h / 2);
            ctx.stroke();
          }}
        }}
      }}

      function drawLogs() {{
        ctx.fillStyle = '#92400e';
        for (const log of logs) {{
          const y = rowY(log.row) + (canvas.height / ROWS - log.h) / 2;
          ctx.fillRect(log.x, y, log.w, log.h);
          ctx.fillStyle = '#a16207';
          ctx.fillRect(log.x + 6, y + 4, log.w - 12, log.h - 8);
          ctx.fillStyle = '#92400e';
        }}
      }}

      function drawCars() {{
        for (const car of cars) {{
          const y = rowY(car.row) + (canvas.height / ROWS - car.h) / 2;
          ctx.fillStyle = car.color;
          ctx.fillRect(car.x, y, car.w, car.h);
          ctx.fillStyle = 'rgba(255,255,255,0.75)';
          ctx.fillRect(car.x + (car.dir > 0 ? car.w - 10 : 4), y + 6, 6, 6);
        }}
      }}

      function drawFrog() {{
        const box = playerBox();
        const cx = box.x + box.w / 2;
        const cy = box.y + box.h / 2;
        const r = box.w * 0.42;
        ctx.fillStyle = wonPulse > 0 ? '#86efac' : '#22c55e';
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#14532d';
        ctx.beginPath();
        ctx.arc(cx - r * 0.35, cy - r * 0.2, r * 0.18, 0, Math.PI * 2);
        ctx.arc(cx + r * 0.35, cy - r * 0.2, r * 0.18, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(cx - r * 0.35, cy - r * 0.22, r * 0.07, 0, Math.PI * 2);
        ctx.arc(cx + r * 0.35, cy - r * 0.22, r * 0.07, 0, Math.PI * 2);
        ctx.fill();
      }}

      function updateEntities() {{
        if (gameOver) return;

        for (const car of cars) {{
          car.x += car.speed * car.dir;
          if (car.dir > 0 && car.x > canvas.width + 20) car.x = -car.w - 20;
          if (car.dir < 0 && car.x < -car.w - 20) car.x = canvas.width + 20;
        }}

        for (const log of logs) {{
          log.x += log.speed * log.dir;
          if (log.dir > 0 && log.x > canvas.width + 20) log.x = -log.w - 20;
          if (log.dir < 0 && log.x < -log.w - 20) log.x = canvas.width + 20;
        }}

        const pb = playerBox();
        onLog = null;
        if (player.row === 2 || player.row === 3) {{
          let safe = false;
          for (const log of logs) {{
            if (log.row !== player.row) continue;
            const lb = {{
              x: log.x,
              y: rowY(log.row) + (canvas.height / ROWS - log.h) / 2,
              w: log.w,
              h: log.h,
            }};
            if (rectsOverlap(pb, lb)) {{
              safe = true;
              onLog = log;
              player.col = Math.max(0, Math.min(COLS - 1, (pb.x + pb.w / 2) / CELL));
              break;
            }}
          }}
          if (!safe) die();
        }}

        if (onLog) {{
          const shift = (onLog.speed * onLog.dir) / CELL;
          player.col = Math.max(0, Math.min(COLS - 1, player.col + shift));
          if (player.col <= 0 || player.col >= COLS - 1) die();
        }}

        for (const car of cars) {{
          if (car.row !== player.row) continue;
          const cb = {{
            x: car.x,
            y: rowY(car.row) + (canvas.height / ROWS - car.h) / 2,
            w: car.w,
            h: car.h,
          }};
          if (rectsOverlap(pb, cb)) {{
            die();
            break;
          }}
        }}

        if (wonPulse > 0) wonPulse -= 1;
      }}

      function drawOverlay() {{
        if (!gameOver) return;
        ctx.fillStyle = 'rgba(10, 37, 64, 0.55)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 22px system-ui, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('Game over', canvas.width / 2, canvas.height / 2 - 8);
        ctx.font = '14px system-ui, sans-serif';
        ctx.fillText('Press R to restart', canvas.width / 2, canvas.height / 2 + 18);
        ctx.textAlign = 'start';
      }}

      function tick() {{
        updateEntities();
        drawBackground();
        drawLogs();
        drawCars();
        drawFrog();
        drawOverlay();
        requestAnimationFrame(tick);
      }}

      tick();
    }})();
  </script>
</body>
</html>"""
