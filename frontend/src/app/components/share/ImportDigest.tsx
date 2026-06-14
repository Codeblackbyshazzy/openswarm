// The "digest" flash that plays where you drop a .swarm: an expanding ring of
// brand-tinted dithered pixels, evoking PixelBlast WITHOUT any WebGL. PixelBlast
// is a single shared WebGL2 context (one canvas, reparented) and reusing it here
// would fight an app's loading animation over that one canvas, plus rapid
// WebGL-context churn is the exact thing that crashed the GPU process. So this is
// plain Canvas2D on ONE pooled canvas, and play() refuses to start while a burst
// is already running, so drop-spam can never pile up work.
import React, { forwardRef, useImperativeHandle, useRef } from 'react';

export interface DigestHandle {
  // Returns false if a burst is already playing (caller should ignore the drop).
  play: (x: number, y: number) => boolean;
}

const SIZE = 240;
const CELL = 6;
const DURATION = 680;
const RADIUS_MAX = 132;

function dither(gx: number, gy: number): number {
  const v = Math.sin(gx * 12.9898 + gy * 78.233) * 43758.5453;
  return v - Math.floor(v);
}

const ImportDigest = forwardRef<DigestHandle, { color?: string }>(({ color = '#c4633a' }, ref) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const busyRef = useRef(false);
  const rafRef = useRef(0);

  useImperativeHandle(ref, () => ({
    play(x: number, y: number): boolean {
      if (busyRef.current) return false;
      const canvas = canvasRef.current;
      if (!canvas) return false;

      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      busyRef.current = true;
      canvas.style.left = `${x - SIZE / 2}px`;
      canvas.style.top = `${y - SIZE / 2}px`;
      canvas.style.opacity = '1';

      const finish = () => {
        busyRef.current = false;
        canvas.style.opacity = '0';
      };
      if (reduce) {
        // Honor reduced-motion: no flashing pixels, just a brief, calm beat.
        window.setTimeout(finish, 200);
        return true;
      }

      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = SIZE * dpr;
      canvas.height = SIZE * dpr;
      const ctx = canvas.getContext('2d');
      if (!ctx) {
        finish();
        return true;
      }
      ctx.scale(dpr, dpr);
      const cells = Math.ceil(SIZE / CELL);
      const center = SIZE / 2;
      const start = performance.now();

      const frame = () => {
        const t = Math.min(1, (performance.now() - start) / DURATION);
        const eased = 1 - Math.pow(1 - t, 3);
        const ring = eased * RADIUS_MAX;
        ctx.clearRect(0, 0, SIZE, SIZE);
        ctx.fillStyle = color;
        for (let gy = 0; gy < cells; gy++) {
          for (let gx = 0; gx < cells; gx++) {
            const px = gx * CELL + CELL / 2;
            const py = gy * CELL + CELL / 2;
            const dist = Math.hypot(px - center, py - center);
            const band = 1 - Math.abs(dist - ring) / 34; // bright at the expanding front
            if (band <= 0) continue;
            const a = band * (0.35 + 0.65 * dither(gx, gy)) * (1 - t * 0.25);
            if (a <= 0) continue;
            ctx.globalAlpha = a > 1 ? 1 : a;
            ctx.fillRect(gx * CELL, gy * CELL, CELL - 1, CELL - 1);
          }
        }
        if (t < 1) {
          rafRef.current = requestAnimationFrame(frame);
        } else {
          finish();
        }
      };
      rafRef.current = requestAnimationFrame(frame);
      return true;
    },
  }));

  return (
    <canvas
      ref={canvasRef}
      width={SIZE}
      height={SIZE}
      style={{
        position: 'fixed',
        width: SIZE,
        height: SIZE,
        pointerEvents: 'none',
        zIndex: 2100,
        opacity: 0,
        transition: 'opacity 160ms ease',
      }}
    />
  );
});

ImportDigest.displayName = 'ImportDigest';
export default ImportDigest;
