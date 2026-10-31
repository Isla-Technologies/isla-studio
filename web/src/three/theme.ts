// The 3D views' colours in each theme. Dark: void black, lines that glow. Light: a pale
// stage, dark ink, no glow, a deeper red so it holds its contrast on a light ground.

export type Theme = 'dark' | 'light';

export interface Palette {
  background: number;
  /** Grid line greys (0 to 1), minor and major. */
  gridMinor: number;
  gridMajor: number;
  shadow: number;
  hemiGround: number;
  hemi: number;
  exposure: number;
  accent: number;
  /** Anatomy: wireframe ink, the dimming fill behind it, the floor rings, the glow. */
  ink: number;
  fill: number;
  fillOpacity: number;
  ring: number;
  tick: number;
  bloom: number;
  /** How much stronger the wireframe's lines are than in the dark, where bloom builds them up. */
  lineGain: number;
}

export const PALETTES: Record<Theme, Palette> = {
  dark: {
    background: 0x050505,
    gridMinor: 0.1,
    gridMajor: 0.2,
    shadow: 0.45,
    hemiGround: 0x080808,
    hemi: 0.35,
    exposure: 1.0,
    accent: 0xff2b2b,
    ink: 0xf2f2f2,
    fill: 0x070707,
    fillOpacity: 0.86,
    ring: 0x3a3a3a,
    tick: 0x5c5c5c,
    bloom: 0.4,
    lineGain: 1,
  },
  light: {
    background: 0xe6e7e9,
    gridMinor: 0.74,
    gridMajor: 0.56,
    shadow: 0.24,
    hemiGround: 0x8f9092,
    hemi: 0.6,
    exposure: 0.92,
    accent: 0xc8161b,
    ink: 0x161616,
    fill: 0xf4f4f2,
    fillOpacity: 0.9,
    ring: 0xbdbdb9,
    tick: 0x8e8e8a,
    bloom: 0,
    lineGain: 2.4,
  },
};
