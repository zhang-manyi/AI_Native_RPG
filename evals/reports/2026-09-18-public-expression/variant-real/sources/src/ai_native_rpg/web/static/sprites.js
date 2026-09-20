/* Character sprites: inline SVG, one function per key (docs/12 §4.2, §8).
 *
 * A sprite key is a *front-end asset name* assigned by order of appearance, never pack
 * content — a scenario ships no art and must not need to (docs/12 §6.3).
 *
 * Each figure is drawn in a 100x180 box standing on y=180, so ``align-items: flex-end``
 * puts every character on the same floor line regardless of height.
 *
 * ── Why this file is written subtractively ──────────────────────────────────────
 *
 * Four earlier versions all read as "balding", each for its own reason: a hair arc whose
 * cubic peaked below the skull; a hair band whose inner edge left 20px of bare forehead;
 * a hair mass behind the head that could not cover the crown at all; and finally a
 * centre parting plus a grey strand stroked in colours *lighter* than the hair, which on
 * a dark head read as two exposed stripes.
 *
 * The pattern was adding detail and hoping it resolved into a face. So the rules here
 * are deliberately restrictive:
 *
 *   1. **Hair is exactly one opaque closed path.** No bands, no arcs, no second layer.
 *      A single silhouette cannot develop a gap.
 *   2. **Nothing is ever drawn on top of the hair.** No partings, no highlights, no
 *      strands. Anything lighter than hair looks like scalp; anything darker looks like
 *      dirt. So: nothing.
 *   3. **The face is drawn after the hair and smaller**, so the hairline is simply where
 *      the face's own outline ends. It cannot be misplaced independently.
 *   4. **Features are positioned from one anchor** (``faceCy``), never from a second
 *      copy of an offset — that is what let the eyes drift low while the face moved down.
 *
 * The result is plainer than what came before. That is the point: a plain figure that
 * reads correctly beats a detailed one that reads as a bald man.
 */

const PALETTES = {
  sprite_a: {
    cloth: "#6b5f7a",
    cloth2: "#544a61",
    skin: "#e8c9a8",
    hair: "#4a3b32",
  },
  sprite_b: {
    cloth: "#4a5a44",
    cloth2: "#3a4736",
    skin: "#dcbb99",
    hair: "#2e2620",
  },
  sprite_c: {
    cloth: "#5a5060",
    cloth2: "#463e4b",
    skin: "#e3c4a3",
    hair: "#5b4a3f",
  },
  sprite_d: {
    cloth: "#4d5568",
    cloth2: "#3c4252",
    skin: "#dfc0a0",
    hair: "#3a2f28",
  },
};

//: Face geometry. One anchor for everything on the head (rule 4 above).
const FACE_RX = 15;
const FACE_RY = 17;

/** The hair silhouette: one closed path, from the crown down past the ears.
 *
 * ``long`` gives shoulder-length hair that widens below the jaw; otherwise it stops at
 * the ear. Either way it is a single ``<path>`` with one fill — see rule 1.
 *
 * The outline starts *outside* the face on both sides and arcs well above the crown
 * (``cy - 30`` control points, which a cubic pulls to roughly ``cy - 22``), so the top of
 * the head is covered by construction rather than by a separate fringe piece.
 */
function hairShape(hair, cy, long) {
  const crown = `M32 ${cy + 2}
                 C31 ${cy - 30} 69 ${cy - 30} 68 ${cy + 2}`;

  const sides = long
    ? // Falls to below the shoulders, flaring slightly outward.
      `C71 ${cy + 14} 72 ${cy + 26} 70 ${cy + 38}
       L61 ${cy + 38}
       C62 ${cy + 24} 61 ${cy + 12} 59 ${cy + 2}
       L41 ${cy + 2}
       C39 ${cy + 12} 38 ${cy + 24} 39 ${cy + 38}
       L30 ${cy + 38}
       C28 ${cy + 26} 29 ${cy + 14} 32 ${cy + 2}`
    : // Stops just below the ear.
      `C69 ${cy + 8} 68 ${cy + 12} 66 ${cy + 14}
       L58 ${cy + 14}
       L42 ${cy + 14}
       L34 ${cy + 14}
       C32 ${cy + 12} 31 ${cy + 8} 32 ${cy + 2}`;

  return `<path d="${crown} ${sides} Z" fill="${hair}" />`;
}

/** A standing figure. Two silhouettes so characters do not look cloned. */
function figure(key, name) {
  const p = PALETTES[key] || PALETTES.sprite_a;
  const tall = key === "sprite_b" || key === "sprite_d";

  //: The single anchor for the whole head.
  const faceCy = tall ? 47 : 52;
  const shoulder = faceCy + FACE_RY + 10;
  const jaw = faceCy + FACE_RY;

  return `
<svg viewBox="0 0 100 180" role="img" aria-label="${escapeAttr(name)}">
  <title>${escapeHtml(name)}</title>
  <g class="sprite-body">
    <!-- shadow on the floor grounds the figure -->
    <ellipse cx="50" cy="176" rx="23" ry="4.5" fill="rgba(0,0,0,0.4)" />

    <!-- body: a simple tapering garment, wider hem for the shawled figure -->
    ${
      tall
        ? `<path d="M50 ${shoulder - 6}
                    C33 ${shoulder} 30 ${shoulder + 30} 32 176
                    L68 176
                    C70 ${shoulder + 30} 67 ${shoulder} 50 ${shoulder - 6} Z"
             fill="${p.cloth}" />
           <path d="M50 ${shoulder - 6}
                    C42 ${shoulder} 39 ${shoulder + 30} 41 176
                    L32 176
                    C30 ${shoulder + 30} 33 ${shoulder} 50 ${shoulder - 6} Z"
             fill="${p.cloth2}" />`
        : `<path d="M50 ${shoulder - 6}
                    C31 ${shoulder} 25 ${shoulder + 34} 23 176
                    L77 176
                    C75 ${shoulder + 34} 69 ${shoulder} 50 ${shoulder - 6} Z"
             fill="${p.cloth}" />
           <path d="M50 ${shoulder - 6}
                    C41 ${shoulder} 35 ${shoulder + 34} 33 176
                    L23 176
                    C25 ${shoulder + 34} 31 ${shoulder} 50 ${shoulder - 6} Z"
             fill="${p.cloth2}" />`
    }

    <!-- neck: overlaps the jaw so no gap can open between head and body -->
    <rect x="45.5" y="${jaw - 5}" width="9" height="13" fill="${p.skin}" />

    <!-- Head. Order matters and is the whole trick: hair first as one solid shape,
         then the face over it. The hairline is the face's own edge, so it cannot be
         left empty (see the header note). Nothing is drawn on the hair afterwards. -->
    ${hairShape(p.hair, faceCy, !tall)}
    <ellipse cx="50" cy="${faceCy + 3}" rx="${FACE_RX}" ry="${FACE_RY - 3}" fill="${p.skin}" />

    <!-- Features, all from faceCy. Eyes sit high on the face on purpose: the distance
         from the face's top edge to the eyes *is* the visible forehead, so a couple of
         pixels here matter more than anything the hair does. At faceCy+1 it measured 43%
         of the face height, which still read as a high hairline. -->
    <circle class="eye" cx="44" cy="${faceCy - 2}" r="1.8" fill="#2b2118" />
    <circle class="eye" cx="56" cy="${faceCy - 2}" r="1.8" fill="#2b2118" />

    <!-- brows: two short strokes, the only thing separating "asleep" from "present" -->
    <path d="M41 ${faceCy - 7} L47 ${faceCy - 7.5}"
      stroke="${p.hair}" stroke-width="1.4" fill="none" stroke-linecap="round" />
    <path d="M59 ${faceCy - 7} L53 ${faceCy - 7.5}"
      stroke="${p.hair}" stroke-width="1.4" fill="none" stroke-linecap="round" />

    <!-- the mouth only moves while speaking; CSS animates it -->
    <path class="mouth" d="M47 ${faceCy + 6} Q50 ${faceCy + 8} 53 ${faceCy + 6}"
      stroke="#8a6a55" stroke-width="1.5" fill="none" stroke-linecap="round" />
  </g>
</svg>`;
}

function escapeHtml(s) {
  return String(s).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function escapeAttr(s) {
  return escapeHtml(s);
}

/** Markup for one character. Unknown keys fall back rather than rendering nothing. */
export function sprite(key, name) {
  return figure(PALETTES[key] ? key : "sprite_a", name);
}
