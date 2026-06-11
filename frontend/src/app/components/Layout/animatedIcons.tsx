import { motion } from 'framer-motion';

// True path-drawing sidebar glyphs: the SVG strokes themselves animate on hover
// (not just a transform), then settle. Always fully visible at rest, so a hover
// that's interrupted mid-flight never leaves a half-drawn icon. Geometry matches
// the lucide line-icons they replace so the static look is unchanged.

const SPRING = { type: 'spring', stiffness: 380, damping: 20 } as const;

type Props = { size?: number };

const svgBase = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  viewBox: '0 0 24 24',
};

// Panel toggle: the inner divider sweeps left, like the panel collapsing.
export function AnimatedPanelLeft({ size = 18 }: Props) {
  return (
    <motion.svg width={size} height={size} {...svgBase} initial="rest" animate="rest" whileHover="hover">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <motion.line
        x1="9" y1="3" x2="9" y2="21"
        variants={{ rest: { x: 0 }, hover: { x: -2.5 } }}
        transition={SPRING}
      />
    </motion.svg>
  );
}

// Plus: both strokes redraw from nothing, the classic "new" flourish.
export function AnimatedPlus({ size = 16 }: Props) {
  return (
    <motion.svg width={size} height={size} {...svgBase} initial="rest" animate="rest" whileHover="hover">
      <motion.line
        x1="12" y1="5" x2="12" y2="19"
        variants={{ rest: { pathLength: 1 }, hover: { pathLength: [0, 1] } }}
        transition={{ duration: 0.34, ease: 'easeOut' }}
      />
      <motion.line
        x1="5" y1="12" x2="19" y2="12"
        variants={{ rest: { pathLength: 1 }, hover: { pathLength: [0, 1] } }}
        transition={{ duration: 0.34, ease: 'easeOut', delay: 0.06 }}
      />
    </motion.svg>
  );
}
