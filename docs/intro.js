'use strict';
/*
 * intro.js — page d'accueil « papier déchiré ».
 * L'image d'accueil couvre l'écran ; on attrape un bord (haut ou bas) et on tire :
 * une déchirure suit le doigt / la souris, puis les deux moitiés s'écartent et tombent,
 * laissant apparaître le bureau.
 */
(function () {
  const PAPER = '#f3eee4';          // couleur de l'âme du papier (bord déchiré)
  const PAPER_DARK = '#d9d1c1';

  function rand(seed) {             // générateur pseudo-aléatoire reproductible
    let s = seed >>> 0 || 1;
    return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
  }

  function start(opts) {
    const root = document.getElementById('splash');
    if (!root) return Promise.resolve();
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    return new Promise((resolve) => {
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      const hint = document.createElement('div');
      hint.className = 'splash-hint';
      hint.innerHTML = '<span class="splash-arrow" aria-hidden="true"></span>';
      hint.append(document.createTextNode(opts.texte || 'Attrape un bord et tire pour déchirer'));
      const enter = document.createElement('button');
      enter.className = 'splash-enter';
      enter.textContent = 'Entrer';
      root.style.background = opts.fond || '#111';
      root.append(canvas, hint, enter);

      const img = new Image();
      img.decoding = 'async';
      img.src = opts.img;

      let W = 0, H = 0, dpr = 1;
      const base = document.createElement('canvas');     // image déjà mise à l'échelle de l'écran
      const bctx = base.getContext('2d');

      // état de la déchirure
      let path = null;          // points {x, y, f} de la ligne de déchirure (f : largeur des fibres)
      let fromTop = true;       // la déchirure part du haut (true) ou du bas (false)
      let tip = 0;              // longueur déchirée (px, depuis le bord de départ)
      let dragging = false;
      let finished = false;
      let anim = 0;

      function layout() {
        dpr = Math.min(2, window.devicePixelRatio || 1);
        W = window.innerWidth;
        H = window.innerHeight;
        for (const c of [canvas, base]) {
          c.width = Math.round(W * dpr);
          c.height = Math.round(H * dpr);
        }
        canvas.style.width = W + 'px';
        canvas.style.height = H + 'px';
        bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        bctx.fillStyle = opts.fond || '#111';
        bctx.fillRect(0, 0, W, H);
        if (img.naturalWidth) {
          const iw = img.naturalWidth, ih = img.naturalHeight;
          const k = opts.ajustement === 'contain' ? Math.min(W / iw, H / ih) : Math.max(W / iw, H / ih);
          const dw = iw * k, dh = ih * k;
          bctx.drawImage(img, (W - dw) / 2, (H - dh) / 2, dw, dh);
        }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        draw();
      }

      function makePath(x0) {
        const r = rand(Math.floor(x0 * 997) + 17);
        const pts = [];
        const step = 9;
        let drift = 0, vel = 0;
        for (let y = 0; y <= H + step; y += step) {
          vel += (r() - 0.5) * 0.9;
          vel *= 0.86;
          drift += vel;
          drift *= 0.985;                                   // reste autour de la colonne de départ
          const jag = (r() - 0.5) * 7;
          pts.push({ x: x0 + drift * 6 + jag, y: Math.min(y, H), f: 2 + r() * 6 });
        }
        return pts;
      }

      // x de la déchirure à la hauteur y (interpolation)
      function xAt(y) {
        const i = Math.max(0, Math.min(path.length - 2, Math.floor(y / 9)));
        const a = path[i], b = path[i + 1];
        const t = b.y === a.y ? 0 : (y - a.y) / (b.y - a.y);
        return a.x + (b.x - a.x) * Math.max(0, Math.min(1, t));
      }

      // écart entre les deux moitiés à la hauteur y (en coin : max au bord de départ, 0 à la pointe)
      function gapAt(y, spread) {
        const d = fromTop ? y : H - y;                    // distance depuis le bord de départ
        if (d >= tip) return 0;
        const t = 1 - d / tip;
        return spread * Math.pow(t, 1.25);
      }

      function draw() {
        ctx.clearRect(0, 0, W, H);
        if (!path || tip <= 0) {
          ctx.drawImage(base, 0, 0, W, H);
          return;
        }
        const spread = Math.min(W * 0.16, 14 + tip * 0.14);
        const y0 = fromTop ? 0 : H - tip;                 // zone déchirée : [y0, y1]
        const y1 = fromTop ? tip : H;

        // partie intacte
        if (fromTop) ctx.drawImage(base, 0, tip * dpr, W * dpr, (H - tip) * dpr, 0, tip, W, H - tip);
        else ctx.drawImage(base, 0, 0, W * dpr, (H - tip) * dpr, 0, 0, W, H - tip);

        // ombre portée dans la fente
        ctx.save();
        ctx.lineJoin = 'round';
        ctx.strokeStyle = 'rgba(0,0,0,0.35)';
        if ('filter' in ctx) ctx.filter = 'blur(6px)';
        for (const side of [-1, 1]) {
          ctx.beginPath();
          for (let y = y0; y <= y1; y += 9) {
            const x = xAt(y) + side * gapAt(y, spread) / 2;
            y === y0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
          }
          ctx.lineWidth = 10;
          ctx.stroke();
        }
        ctx.restore();

        // les deux moitiés, en bandes décalées
        const band = 6;
        for (let ya = y0; ya < y1; ya += band) {
          const yb = Math.min(y1, ya + band);
          const g = gapAt((ya + yb) / 2, spread) / 2;
          const xa = xAt(ya), xb = xAt(yb);
          // gauche
          ctx.save();
          ctx.beginPath();
          ctx.moveTo(-g, ya); ctx.lineTo(xa - g, ya); ctx.lineTo(xb - g, yb); ctx.lineTo(-g, yb); ctx.closePath();
          ctx.clip();
          ctx.drawImage(base, 0, ya * dpr, W * dpr, (yb - ya) * dpr + 1, -g, ya, W, yb - ya + 1 / dpr);
          ctx.restore();
          // droite
          ctx.save();
          ctx.beginPath();
          ctx.moveTo(xa + g, ya); ctx.lineTo(W + g, ya); ctx.lineTo(W + g, yb); ctx.lineTo(xb + g, yb); ctx.closePath();
          ctx.clip();
          ctx.drawImage(base, 0, ya * dpr, W * dpr, (yb - ya) * dpr + 1, g, ya, W, yb - ya + 1 / dpr);
          ctx.restore();
        }

        // bords déchirés : fibres blanches du papier
        for (const side of [-1, 1]) {
          ctx.beginPath();
          const pts = [];
          for (const p of path) {
            if (p.y < y0 - 9 || p.y > y1 + 9) continue;
            const y = Math.max(y0, Math.min(y1, p.y));
            pts.push({ x: xAt(y) + side * gapAt(y, spread) / 2, y, f: p.f * Math.min(1, gapAt(y, spread) / 6 + 0.35) });
          }
          if (pts.length < 2) continue;
          pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
          for (let i = pts.length - 1; i >= 0; i--) ctx.lineTo(pts[i].x - side * pts[i].f, pts[i].y);
          ctx.closePath();
          ctx.fillStyle = PAPER;
          ctx.fill();
          ctx.strokeStyle = PAPER_DARK;
          ctx.lineWidth = 0.8;
          ctx.stroke();
        }
      }

      /* ── interactions ── */
      function begin(x, y) {
        if (finished) return;
        cancelAnimationFrame(anim);
        fromTop = y < H / 2;
        path = makePath(Math.max(W * 0.12, Math.min(W * 0.88, x)));
        dragging = true;
        hint.classList.add('gone');
        move(y);
      }
      function move(y) {
        if (!dragging) return;
        const d = fromTop ? y : H - y;
        tip = Math.max(0, Math.min(H, d));
        draw();
      }
      function end() {
        if (!dragging) return;
        dragging = false;
        if (tip > H * 0.4) tearAway();
        else settle();
      }

      function settle() {                                   // relâché trop tôt : le papier se referme
        const t0 = performance.now(), from = tip;
        const stepFn = (now) => {
          const t = Math.min(1, (now - t0) / 260);
          tip = from * (1 - t) * (1 - t);
          draw();
          if (t < 1) anim = requestAnimationFrame(stepFn);
          else { path = null; hint.classList.remove('gone'); }
        };
        anim = requestAnimationFrame(stepFn);
      }

      function tearAway() {
        finished = true;
        const t0 = performance.now(), from = tip;
        // 1) la déchirure finit de traverser la page
        const run = (now) => {
          const t = Math.min(1, (now - t0) / 220);
          tip = from + (H - from) * (1 - (1 - t) * (1 - t));
          draw();
          if (t < 1) anim = requestAnimationFrame(run);
          else fall();
        };
        anim = requestAnimationFrame(run);
      }

      // 2) les deux moitiés pivotent et tombent de chaque côté
      function fall() {
        const t0 = performance.now();
        const half = (side) => {
          const c = document.createElement('canvas');
          c.width = canvas.width; c.height = canvas.height;
          const k = c.getContext('2d');
          k.setTransform(dpr, 0, 0, dpr, 0, 0);
          k.beginPath();
          if (side < 0) { k.moveTo(0, 0); path.forEach((p) => k.lineTo(p.x, p.y)); k.lineTo(0, H); }
          else { k.moveTo(W, 0); path.forEach((p) => k.lineTo(p.x, p.y)); k.lineTo(W, H); }
          k.closePath();
          k.save(); k.clip(); k.drawImage(base, 0, 0, W, H); k.restore();
          k.beginPath();
          path.forEach((p, i) => (i ? k.lineTo(p.x, p.y) : k.moveTo(p.x, p.y)));
          for (let i = path.length - 1; i >= 0; i--) k.lineTo(path[i].x - side * path[i].f, path[i].y);
          k.closePath(); k.fillStyle = PAPER; k.fill();
          return c;
        };
        const L = half(-1), R = half(1);
        const pivotY = fromTop ? H : 0;
        const run = (now) => {
          const t = Math.min(1, (now - t0) / 900);
          const e = t * t;                                   // accélère comme une chute
          ctx.clearRect(0, 0, W, H);
          for (const [c, side] of [[L, -1], [R, 1]]) {
            const px = side < 0 ? 0 : W;
            ctx.save();
            ctx.globalAlpha = 1 - Math.max(0, (t - 0.6) / 0.4);
            ctx.translate(px + side * (W * 0.08 + e * W * 0.7), pivotY + e * H * 0.25 * (fromTop ? 1 : -1));
            ctx.rotate(side * (0.04 + e * 0.5) * (fromTop ? -1 : 1));
            ctx.translate(-px, -pivotY);
            ctx.shadowColor = 'rgba(0,0,0,0.45)';
            ctx.shadowBlur = 30;
            ctx.drawImage(c, 0, 0, W, H);
            ctx.restore();
          }
          root.style.background = 'transparent';
          if (t < 1) anim = requestAnimationFrame(run);
          else done();
        };
        anim = requestAnimationFrame(run);
      }

      function done() {
        window.removeEventListener('resize', layout);
        window.removeEventListener('keydown', onKey);
        root.remove();
        resolve();
      }

      function quickEnter() {                             // bouton / clavier / mouvement réduit
        if (finished) return;
        if (reduce) {
          finished = true;
          root.classList.add('fade');
          setTimeout(done, 400);
          return;
        }
        fromTop = true;
        path = makePath(W / 2);
        hint.classList.add('gone');
        tip = 1;
        tearAway();
      }
      function onKey(e) {
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); quickEnter(); }
      }

      canvas.addEventListener('pointerdown', (e) => {
        canvas.setPointerCapture(e.pointerId);
        begin(e.clientX, e.clientY);
      });
      canvas.addEventListener('pointermove', (e) => move(e.clientY));
      canvas.addEventListener('pointerup', end);
      canvas.addEventListener('pointercancel', end);
      enter.addEventListener('click', quickEnter);
      window.addEventListener('keydown', onKey);
      window.addEventListener('resize', layout);

      img.onload = () => { layout(); root.style.background = 'transparent'; root.classList.add('ready'); };
      img.onerror = done;                                   // pas d'image : on passe directement au bureau
      layout();
    });
  }

  window.Intro = { start };
})();
