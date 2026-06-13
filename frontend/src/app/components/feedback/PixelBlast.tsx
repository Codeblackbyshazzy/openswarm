// Brand-tinted pixel-dither loading background. CSS only, on purpose: this is the
// app-card loading placeholder and it mounts/unmounts every time you open an app, so
// the old WebGL2 version churned GL contexts faster than the GPU could recycle them and
// killed the whole renderer under app-list spam (worst with no chat anchoring the GPU
// process). A tiled dot-grid + gentle pulse reads the same at a glance with zero GPU
// context churn. Same props as before so call sites don't change.

import React from 'react';

interface PixelBlastProps {
  color?: string;
  pixelSize?: number;
  speed?: number;
  edgeFade?: number;
  style?: React.CSSProperties;
}

const PixelBlast: React.FC<PixelBlastProps> = ({
  color = '#cc785c',
  pixelSize = 4,
  speed = 0.5,
  edgeFade = 0.3,
  style,
}) => {
  // 8x the pixel size matches the old shader's cell size; the dot grid reads as the
  // same diffuse pixel haze. Slower speed -> longer, calmer pulse.
  const tile = Math.max(4, pixelSize * 2);
  const dur = `${Math.max(2, 3 / Math.max(speed, 0.1))}s`;
  const fadePct = Math.round((1 - Math.min(Math.max(edgeFade, 0), 0.5)) * 100);

  return (
    <>
      <style>{`@keyframes pixelblast-pulse { 0%,100% { opacity: 0.35 } 50% { opacity: 0.7 } }`}</style>
      <div
        aria-label="OpenSwarm idle background"
        style={{
          position: 'absolute',
          inset: 0,
          backgroundColor: '#1a1a1a',
          backgroundImage: `radial-gradient(${color} 1px, transparent 1.4px)`,
          backgroundSize: `${tile}px ${tile}px`,
          animation: `pixelblast-pulse ${dur} ease-in-out infinite`,
          WebkitMaskImage: `radial-gradient(ellipse at center, #000 ${fadePct}%, transparent 100%)`,
          maskImage: `radial-gradient(ellipse at center, #000 ${fadePct}%, transparent 100%)`,
          ...style,
        }}
      />
    </>
  );
};

export default PixelBlast;
