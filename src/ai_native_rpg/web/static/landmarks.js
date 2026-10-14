/* Landmarks: one foreground object per place *kind*, shown when nobody is there.
 *
 * Keyed by `Location.backdrop` (the pack says `backdrop: square`, the front end owns the
 * pixels), so a scenario ships no art and no story name appears here — the same split
 * `sprites.js` uses for characters (docs/12 §4.2, §6.3). A kind with no landmark simply
 * shows nothing: an empty place is a place, not an error.
 *
 * Drawn in the portraits' style (dark contour, flat fill, one cel shadow) and hung in the
 * same slot as a portrait, so walking from a room with someone in it to an empty square
 * does not change the shape of the screen.
 *
 * Light: the portraits are lit from the upper left, and so is this — but here the light
 * has a source. The lantern hangs to the *left* of the well with its bracket reaching
 * over the well mouth, so the well's shaded right side is the side facing away from it.
 * A lamp on the right would have lit the side drawn in shadow.
 */

const LINE = "#2a2019";

const shape = (d, fill, w = 2) =>
  `<path d="${d}" fill="${fill}" stroke="${LINE}" stroke-width="${w}" stroke-linejoin="round"/>`;
const flat = (d, fill, op = 1) => `<path d="${d}" fill="${fill}" opacity="${op}"/>`;
const ink = (d, w = 1.6, op = 1) =>
  `<path d="${d}" fill="none" stroke="${LINE}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round" opacity="${op}"/>`;
const box = (x, y, w, h, fill, sw = 2) =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${fill}" stroke="${LINE}" stroke-width="${sw}" stroke-linejoin="round"/>`;

/** A well with a little roof, winch and bucket, in a 400×600 frame, offset by `tx`. */
function well(tx) {
  const stone = "#8b8478";
  const stoneDk = "#6a645b";
  const wood = "#6e5038";
  const woodDk = "#523a28";
  return `<g transform="translate(${tx} 0)">
    <ellipse cx="200" cy="560" rx="150" ry="22" fill="#000" opacity="0.45"/>
    ${shape("M78 196 L200 120 L322 196 L304 206 L200 142 L96 206 Z", "#5b3f2f")}
    ${flat("M200 142 L304 206 L322 196 L200 120 Z", "#45301f")}
    ${ink("M120 184 L200 136", 1.1, 0.5)}${ink("M150 192 L200 160", 1.1, 0.4)}${ink("M280 184 L200 136", 1.1, 0.5)}
    ${box(108, 200, 18, 248, wood)}${flat("M116 202 h10 v244 h-10 Z", woodDk)}
    ${box(274, 200, 18, 248, wood)}${flat("M282 202 h10 v244 h-10 Z", woodDk)}
    ${box(118, 248, 164, 14, wood)}${flat("M118 256 h164 v6 h-164 Z", woodDk)}
    ${box(176, 238, 48, 34, "#7a5a40")}
    ${ink("M200 272 L200 330", 2)}
    ${shape("M178 330 L222 330 L216 376 L184 376 Z", "#7c5a3e")}
    ${flat("M200 330 L222 330 L216 376 L200 376 Z", "#634631")}
    ${ink("M180 344 L220 344", 1.6, 0.8)}${ink("M182 362 L218 362", 1.6, 0.8)}
    ${ink("M180 330 Q200 312 220 330", 1.6)}
    ${shape("M70 430 C70 470 330 470 330 430 L330 520 C330 566 70 566 70 520 Z", stone)}
    ${flat("M200 470 C260 470 330 462 330 430 L330 520 C330 552 270 562 200 564 Z", stoneDk)}
    ${shape("M70 430 C70 396 330 396 330 430 C330 464 70 464 70 430 Z", "#9c958a")}
    ${shape("M96 430 C96 410 304 410 304 430 C304 450 96 450 96 430 Z", "#1a1712")}
    ${ink("M72 476 C120 498 280 498 328 476", 1.4, 0.7)}
    ${ink("M74 506 C120 528 280 528 326 506", 1.4, 0.7)}
    ${ink("M110 466 L106 494", 1.4, 0.7)}${ink("M168 474 L166 502", 1.4, 0.7)}
    ${ink("M232 474 L234 502", 1.4, 0.7)}${ink("M290 466 L294 494", 1.4, 0.7)}
    ${ink("M136 500 L134 528", 1.4, 0.7)}${ink("M200 504 L200 534", 1.4, 0.7)}
    ${ink("M264 500 L266 528", 1.4, 0.7)}
  </g>`;
}

/** An iron lantern on a post, bracket reaching right; the glass centre is at (228, 217). */
function lantern() {
  const iron = "#3b332c";
  return `
    <ellipse cx="109" cy="566" rx="60" ry="10" fill="#000" opacity="0.45"/>
    ${box(100, 120, 18, 444, iron)}${flat("M109 122 h9 v440 h-9 Z", "#2c2620")}
    ${shape("M100 150 L242 150 L242 162 L118 162 Z", iron)}
    ${ink("M118 198 C130 172 176 164 222 162", 2.4)}
    ${ink("M228 162 L228 184", 2)}
    ${shape("M208 184 L248 184 L244 196 L212 196 Z", "#2f2924")}
    ${shape("M212 196 L244 196 L240 238 L216 238 Z", "#f0c07a")}
    ${flat("M228 196 L244 196 L240 238 L228 238 Z", "#d9a862")}
    ${ink("M228 196 L228 238", 1.6)}
    ${shape("M210 238 L246 238 L242 248 L214 248 Z", "#2f2924")}`;
}

const LANDMARKS = {
  // The village well, lamplit. The pack's square is "where people gather by the well".
  square: `
    <defs>
      <radialGradient id="landmark-lamp">
        <stop offset="0" stop-color="#f0c07a" stop-opacity="0.26"/>
        <stop offset="1" stop-color="#f0c07a" stop-opacity="0"/>
      </radialGradient>
      <radialGradient id="landmark-pool">
        <stop offset="0" stop-color="#f0c07a" stop-opacity="0.14"/>
        <stop offset="1" stop-color="#f0c07a" stop-opacity="0"/>
      </radialGradient>
    </defs>
    <!-- Halo behind everything, and the pool it throws on the ground toward the well.
         Radii stay inside the frame (left edge x=60): a glow running past it is cut off
         by the SVG's own box and shows as a hard vertical edge beside the post. -->
    <circle cx="228" cy="217" r="160" fill="url(#landmark-lamp)"/>
    <ellipse cx="330" cy="556" rx="230" ry="34" fill="url(#landmark-pool)"/>
    ${well(200)}
    ${lantern()}
    <!-- the same light again, faint, over the well: its near side is the lit side -->
    <circle cx="228" cy="217" r="165" fill="url(#landmark-lamp)" opacity="0.45"/>`,
};

/** The landmark for a place kind, or "" when there is none (or no kind at all). */
export function landmark(kind) {
  const art = LANDMARKS[kind];
  if (!art) return "";
  // Decorative: the place heading already names where the player is.
  return (
    `<svg class="landmark-art" viewBox="60 0 520 600" preserveAspectRatio="xMidYMin meet" ` +
    `aria-hidden="true" focusable="false">${art}</svg>`
  );
}
