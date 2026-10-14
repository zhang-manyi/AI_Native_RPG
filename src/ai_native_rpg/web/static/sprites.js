/* Character portraits: inline SVG, one figure per key (docs/12 §4.2, §8).
 *
 * A sprite key is a *front-end asset name* assigned by order of appearance, never pack
 * content — a scenario ships no art and must not need to (docs/12 §6.3). The figures
 * below were designed against the village pack's three characters, but nothing here
 * names them: a pack whose first NPC is a lighthouse keeper gets the shawl and the bun.
 *
 * Style is "clean line + flat colour": a dark contour round every part, one flat fill,
 * and one cel-shaded shadow value. It was picked over a softer painted look because the
 * stage is near-black and the option tray is a translucent blur laid over the lower body
 * (styles.css, `.cast`): a hard contour still separates the figure from the dark, and a
 * flat block still reads through the blur, where a gradient turns to fog.
 *
 * Each figure is a waist-up half body in a 400×600 box, lit from the upper left, in
 * three-quarter view turned toward the viewer's right — so the near side of the face is
 * on the left and the features sit right of the head's centre.
 *
 * ── How a part is shaded ─────────────────────────────────────────────────────
 *
 * Every `part` gets its shadow for free: the shadow is the rim of the part left
 * uncovered when a copy is shifted `k` units toward the light. `k` is how far round the
 * form the shadow reaches — big for a torso, small for a button. Nothing is painted on
 * top of hair (the old rule from the stick-figure era still holds: anything drawn on hair
 * reads as scalp), and the face is drawn after the hair so the hairline is simply where
 * the face ends.
 *
 * Kinds: `part` (filled, outlined, auto-shaded), `shade` (a cast shadow, e.g. under the
 * chin), `fill` (a flat film such as stubble), `ink` (a line), `eye`.
 */

//: The shadow colour leans toward slate rather than black: the cool fill under a warm key.
const SHADOW = [44, 51, 70];
const SHADOW_MIX = 0.38;

const FIGURES = {
  // woman in a shawl
  sprite_a: {
    ink: "#3d2a21",
    palette: {skin: "#e0bc9c", hair: "#6a4f3e", shawl: "#7a5e46", dress: "#7c8794"},
    parts: [
      { part: "M124 186 a23 19 0 1 0 46 0 a23 19 0 1 0 -46 0 Z", c: "hair", k: 6 },
      { part: "M174 196 C176 214 175 232 168 252 L234 252 C227 232 226 214 228 196 Z", c: "skin", k: 8 },
      { shade: "M177 206 C192 226 212 228 225 212 L225 238 C210 243 192 243 177 236 Z", c: "skin" },
      { part: "M118 302 C122 268 150 252 178 246 L224 246 C250 252 272 268 278 300 L292 600 L102 600 Z", c: "dress", k: 26 },
      { part: "M174 246 C188 258 214 258 228 246 L232 255 C214 268 188 268 170 255 Z", c: "#727c88", k: 3 },
      { part: "M180 243 C150 246 118 258 106 300 C96 350 94 410 100 450 C104 476 108 494 112 507 C132 517 152 519 168 513 C184 501 194 488 200 472 C192 400 186 320 180 243 Z", c: "shawl", k: 20 },
      { part: "M222 243 C248 246 270 258 282 296 C290 350 291 410 287 450 C283 476 281 494 277 505 C262 513 248 515 236 511 C222 499 214 487 210 472 C214 400 218 320 222 243 Z", c: "shawl", k: 16 },
      { ink: "M130 300 C126 360 128 420 138 505", w: 1.2, op: 0.35 },
      { ink: "M158 292 C162 360 172 420 182 494", w: 1.2, op: 0.3 },
      { ink: "M258 300 C264 360 266 420 262 504", w: 1.2, op: 0.35 },
      { shade: "M176 488 C186 500 214 500 232 492 L240 510 C220 516 192 516 172 506 Z", c: "dress", op: 0.8 },
      { part: "M174 468 C176 458 190 456 202 460 C212 464 214 476 208 486 C200 494 182 494 176 486 C172 480 172 474 174 468 Z", c: "skin", k: 5 },
      { part: "M204 470 C206 460 220 456 232 462 C240 468 240 480 232 488 C222 494 208 490 204 482 Z", c: "skin", k: 5 },
      { ink: "M214 466 L227 470", w: 1, op: 0.45 },
      { ink: "M212 474 L229 478", w: 1, op: 0.45 },
      { ink: "M212 482 L226 485", w: 1, op: 0.45 },
      { part: "M142 172 C130 110 162 74 202 74 C244 74 268 108 258 168 C240 182 162 182 142 172 Z", c: "hair", k: 9 },
      { ink: "M172 90 C160 100 151 120 147 152", w: 2, op: 0.5, c: "#a0968b" },
      { ink: "M196 80 C174 84 158 98 150 122", w: 1.8, op: 0.45, c: "#a0968b" },
      { ink: "M228 84 C244 92 254 110 256 140", w: 1.6, op: 0.35, c: "#a0968b" },
      { part: "M143 160 a7 13 0 1 0 14 0 a7 13 0 1 0 -14 0 Z", c: "skin", k: 3 },
      { part: "M150 138 C148 176 160 200 188 214 C199 220 212 219 221 212 C237 198 247 174 246 140 C246 110 230 97 200 97 C172 97 152 110 150 138 Z", c: "skin", k: 11 },
      { ink: "M163 140 Q176 133 192 133", w: 2.2 },
      { ink: "M215 133 Q227 131 238 137", w: 2 },
      { eye: [178, 153, 25, 8], iris: "#6a4a32", look: -2, tired: true },
      { eye: [226, 153, 19, 7], iris: "#6a4a32", look: -2, tired: true },
      { ink: "M212 163 C214 170 216 176 218 181", w: 1.3, op: 0.5 },
      { ink: "M218 182 C214 188 206 188 200 184", w: 1.5 },
      { ink: "M199 178 C195 182 196 187 201 187", w: 1.2, op: 0.7 },
      { ink: "M196 188 Q190 196 193 204", w: 1, op: 0.3 },
      { ink: "M193 200 C203 201 214 201 221 200", w: 1.7 },
      { ink: "M199 206 Q208 207.5 216 206", w: 1.2, op: 0.35 },
    ],
  },
  // hunter in a cloak
  sprite_b: {
    ink: "#2f221b",
    palette: {skin: "#cda383", hair: "#3a2b22", shirt: "#a6a299", vest: "#5a3f2b", cloak: "#423a34", belt: "#2e221a", trousers: "#4b423a"},
    parts: [
      { part: "M112 282 C104 268 142 250 180 248 L224 248 C264 252 298 268 304 290 L326 600 L74 600 Z", c: "cloak", k: 20 },
      { part: "M174 198 C177 220 176 240 170 266 L236 266 C230 240 229 220 232 198 Z", c: "skin", k: 8 },
      { shade: "M178 210 C192 228 214 234 228 216 L228 242 C210 248 192 248 178 240 Z", c: "skin" },
      { part: "M122 292 C128 266 156 256 182 252 L220 252 C246 256 272 266 278 292 L282 600 L120 600 Z", c: "shirt", k: 22 },
      { part: "M188 258 L216 258 L202 294 Z", c: "skin", k: 4, outline: false },
      { part: "M124 288 C108 320 103 380 106 425 C110 452 126 472 142 488 L164 476 C150 458 139 440 139 420 C139 390 141 360 147 330 Z", c: "shirt", k: 10 },
      { part: "M276 288 C291 320 295 380 292 425 C288 452 274 470 258 486 L238 474 C250 456 259 440 259 420 C259 390 257 360 252 330 Z", c: "shirt", k: 10 },
      { part: "M147 302 C151 282 170 264 186 258 L197 340 L192 472 L149 472 C145 420 145 360 147 302 Z", c: "vest", k: 14 },
      { part: "M214 258 C230 264 248 282 252 302 C254 360 254 420 251 472 L208 472 L204 340 Z", c: "vest", k: 12 },
      { ink: "M168 300 C166 360 166 420 170 470", w: 1, op: 0.25 },
      { part: "M146 490 L254 490 L264 600 L136 600 Z", c: "trousers", k: 16 },
      { part: "M144 470 L256 470 L256 492 L144 492 Z", c: "belt", k: 4 },
      { part: "M192 472 L210 472 L210 490 L192 490 Z", c: "#8c7a5c", k: 2 },
      { part: "M138 472 C146 464 160 466 166 474 C170 482 168 494 158 498 C148 500 138 494 136 486 Z", c: "skin", k: 4 },
      { part: "M240 470 C248 464 262 466 266 474 C268 484 264 494 254 496 C244 498 238 490 238 482 Z", c: "skin", k: 4 },
      { ink: "M146 484 L160 486", w: 1, op: 0.45 },
      { ink: "M246 482 L260 482", w: 1, op: 0.45 },
      { part: "M178 250 C150 252 120 262 108 290 C104 302 106 314 112 320 C126 302 150 292 172 288 Z", c: "cloak", k: 8 },
      { part: "M224 250 C252 252 280 262 292 290 C296 302 294 314 288 320 C274 302 252 292 230 288 Z", c: "cloak", k: 8 },
      { part: "M180 250 L170 270 L196 282 L200 262 Z", c: "#aba89f", k: 3 },
      { part: "M222 250 L234 270 L208 282 L204 262 Z", c: "#aba89f", k: 3 },
      { part: "M146 168 C134 104 166 66 204 66 C246 68 264 104 254 166 C240 176 160 176 146 168 Z", c: "hair", k: 8 },
      { part: "M145 163 a7 13 0 1 0 14 0 a7 13 0 1 0 -14 0 Z", c: "skin", k: 3 },
      { part: "M154 130 C153 156 156 174 162 186 C170 200 184 212 196 220 C202 223 212 223 218 220 C228 212 238 198 242 182 C245 168 246 150 246 132 C246 104 228 92 200 92 C174 92 156 104 154 130 Z", c: "skin", k: 11 },
      { fill: "M158 172 C161 186 170 200 184 211 C190 216 196 220 202 221.5 C208 222.5 214 222 218 220 C228 212 238 198 242 182 L244 172 C236 190 226 198 212 201 L196 201 C182 199 168 190 158 172 Z", c: "hair", op: 0.22 },
      { fill: "M198 194 Q211 190 225 194 L223 199 Q211 196 200 199 Z", c: "hair", op: 0.22 },
      { shade: "M232 160 C237 176 235 192 226 204 L237 197 C243 181 243 167 239 156 Z", c: "skin", op: 0.7 },
      { part: "M152 134 C154 102 180 86 210 88 C236 90 252 106 250 136 C244 122 236 116 226 114 C214 120 196 120 180 118 C168 120 158 126 152 134 Z", c: "hair", k: 4, outline: false },
      { ink: "M162 142 Q177 139 193 141", w: 3 },
      { ink: "M214 141 Q226 139 239 142", w: 2.7 },
      { eye: [178, 155, 25, 6.5], iris: "#4a3f36", look: -1.5 },
      { eye: [226, 155, 19, 6], iris: "#4a3f36", look: -1.5 },
      { ink: "M214 166 C216 174 218 180 220 186", w: 1.3, op: 0.5 },
      { ink: "M220 186 C215 191 206 191 200 187", w: 1.5 },
      { ink: "M199 181 C195 185 197 190 202 190", w: 1.2, op: 0.7 },
      { ink: "M193 204 C203 204.6 214 204.6 221 204", w: 1.7 },
      { ink: "M199 210 Q208 211.5 216 210", w: 1.2, op: 0.35 },
      { ink: "M162 172 C166 184 172 194 180 200", w: 1, op: 0.25 },
    ],
  },
  // innkeeper in an apron
  sprite_c: {
    ink: "#3a2519",
    palette: {skin: "#deb08e", hair: "#7a4a2d", shirt: "#e4dac4", coat: "#8b4f37", apron: "#76604b", wood: "#8a5a35", cloth: "#d6cfbf"},
    parts: [
      { part: "M166 196 C168 216 166 236 158 256 L244 256 C236 236 234 216 236 196 Z", c: "skin", k: 8 },
      { shade: "M169 208 C186 228 216 232 233 212 L233 238 C214 246 188 246 169 238 Z", c: "skin" },
      { part: "M90 302 C96 268 140 252 178 248 L222 248 C262 252 304 268 310 302 L292 600 L108 600 Z", c: "shirt", k: 24 },
      { part: "M186 252 L216 252 L202 292 Z", c: "skin", k: 4, outline: false },
      { part: "M112 302 C118 280 150 262 180 254 L200 332 L198 520 L110 520 C106 440 106 360 112 302 Z", c: "coat", k: 16 },
      { part: "M220 254 C252 262 284 280 290 302 C294 360 294 440 290 520 L202 520 L202 332 Z", c: "coat", k: 14 },
      { part: "M118 450 L282 450 L294 600 L106 600 Z", c: "apron", k: 18 },
      { ink: "M116 452 L284 452", w: 2.6, op: 0.6 },
      { ink: "M150 470 C148 520 146 560 144 600", w: 1, op: 0.3 },
      { ink: "M246 470 C250 520 254 560 256 600", w: 1, op: 0.3 },
      { part: "M203.8 362 a3.2 3.2 0 1 0 6.4 0 a3.2 3.2 0 1 0 -6.4 0 Z", c: "#c9b48e", k: 1 },
      { part: "M203.8 396 a3.2 3.2 0 1 0 6.4 0 a3.2 3.2 0 1 0 -6.4 0 Z", c: "#c9b48e", k: 1 },
      { part: "M203.8 430 a3.2 3.2 0 1 0 6.4 0 a3.2 3.2 0 1 0 -6.4 0 Z", c: "#c9b48e", k: 1 },
      { part: "M178 246 L166 264 L194 278 L198 256 Z", c: "#e9e1d0", k: 3 },
      { part: "M224 246 L236 264 L208 278 L204 256 Z", c: "#e9e1d0", k: 3 },
      { part: "M92 296 C76 330 72 370 76 400 L119 410 C121 380 123 350 127 320 Z", c: "shirt", k: 10 },
      { part: "M308 296 C324 330 328 370 324 400 L281 410 C279 380 277 350 273 320 Z", c: "shirt", k: 10 },
      { part: "M76 416 C80 446 118 470 166 478 L172 456 C140 450 122 436 119 420 Z", c: "skin", k: 7 },
      { part: "M324 416 C320 446 292 468 254 476 L248 454 C276 448 282 434 281 420 Z", c: "skin", k: 7 },
      { part: "M73 396 L121 404 L119 426 L71 419 Z", c: "#d5cbb5", k: 4 },
      { part: "M327 396 L279 404 L281 426 L329 419 Z", c: "#d5cbb5", k: 4 },
      { part: "M146 450 C160 444 186 442 200 446 L204 470 C190 474 166 478 150 480 Z", c: "cloth", k: 5 },
      { part: "M150 480 C152 500 150 516 146 528 L166 526 C170 510 172 494 174 478 Z", c: "cloth", k: 4 },
      { part: "M160 456 C170 450 186 452 190 462 C194 472 188 482 176 484 C166 486 158 478 158 468 Z", c: "skin", k: 4 },
      { ink: "M262 440 C278 440 278 468 262 468", w: 5, c: "#664b3b" },
      { part: "M228 428 L263 428 L260 482 L231 482 Z", c: "wood", k: 6 },
      { part: "M228.0 428 a17.5 4 0 1 0 35.0 0 a17.5 4 0 1 0 -35.0 0 Z", c: "#583821", k: 1 },
      { ink: "M232 444 L262 444", w: 1, op: 0.35 },
      { ink: "M231 466 L261 466", w: 1, op: 0.35 },
      { part: "M222 452 C232 446 246 448 250 458 C254 470 248 482 236 484 C226 486 218 478 218 468 Z", c: "skin", k: 4 },
      { ink: "M231 458 L248 460", w: 1, op: 0.45 },
      { ink: "M229 467 L249 469", w: 1, op: 0.45 },
      { ink: "M231 476 L246 477", w: 1, op: 0.45 },
      { part: "M138 168 C132 116 160 80 200 80 C240 80 268 116 262 168 C250 178 150 178 138 168 Z", c: "hair", k: 6 },
      { part: "M139 158 a7 13 0 1 0 14 0 a7 13 0 1 0 -14 0 Z", c: "skin", k: 3 },
      { part: "M146 140 C144 180 158 206 186 218 C198 223 214 223 226 218 C246 206 256 182 254 140 C254 108 232 92 200 92 C170 92 148 108 146 140 Z", c: "skin", k: 12 },
      { ink: "M160 133 Q174 126 190 130", w: 2.6 },
      { ink: "M214 129 Q228 126 240 132", w: 2.4 },
      { eye: [176, 148, 25, 8], iris: "#566876", look: -2, smile: true },
      { eye: [228, 148, 20, 7], iris: "#566876", look: -2, smile: true },
      { ink: "M160 151 L154 148", w: 1, op: 0.35 },
      { ink: "M161 156 L155 158", w: 1, op: 0.35 },
      { ink: "M242 152 L247 149", w: 1, op: 0.3 },
      { ink: "M213 158 C215 166 218 172 220 178", w: 1.3, op: 0.5 },
      { ink: "M221 179 C222 187 211 191 203 187 C197 190 191 186 193 180", w: 1.5 },
      { part: "M186 191 C194 182 206 184 211 188 C217 184 227 184 232 191 C225 197 217 196 211 194 C203 198 193 198 186 191 Z", c: "hair", k: 2, outline: false },
      { ink: "M192 201 C199 206 213 207 223 201", w: 1.7 },
      { ink: "M226 196 Q230 201 227 206", w: 1, op: 0.3 },
      { ink: "M189 196 Q185 201 188 206", w: 1, op: 0.3 },
    ],
  },
};

function shadowOf(hex) {
  const n = parseInt(hex.slice(1), 16);
  const rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  const mixed = rgb.map((v, i) => Math.round(v + (SHADOW[i] - v) * SHADOW_MIX));
  return `#${mixed.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

/** One almond eye: white, iris clipped to the lids, lid line heavier at the top. */
function eye(e, ink, uid) {
  const [cx, cy, w, h] = e.eye;
  const l = cx - w / 2;
  const r = cx + w / 2;
  const lower = e.smile ? 0.35 : 0.6;
  const almond =
    `M${l} ${cy} C${cx - w / 4} ${cy - h} ${cx + w / 4} ${cy - h} ${r} ${cy} ` +
    `C${cx + w / 4} ${cy + h * lower} ${cx - w / 4} ${cy + h * lower} ${l} ${cy} Z`;
  const ir = h * 0.82;
  const ix = cx + e.look;
  const iy = cy - h * 0.12;
  let under;
  if (e.tired) {
    // A crease above the lid and a bag below: the "has not slept" read.
    under =
      `<path d="M${l + 2} ${cy - h - 3} Q${cx} ${cy - h - 7} ${r - 1} ${cy - h - 2.5}" fill="none" stroke="${ink}" stroke-width="1" opacity="0.4"/>` +
      `<path d="M${l + 3} ${cy + h * 0.9} Q${cx} ${cy + h * 1.4} ${r - 2} ${cy + h * 0.85}" fill="none" stroke="${ink}" stroke-width="1" opacity="0.35"/>`;
  } else if (e.smile) {
    under = `<path d="M${l + 2} ${cy + h * 0.55} Q${cx} ${cy + h * 0.2} ${r - 2} ${cy + h * 0.5}" fill="none" stroke="${ink}" stroke-width="1.1" opacity="0.5"/>`;
  } else {
    under = `<path d="M${l + 3} ${cy + h * 0.55} Q${cx} ${cy + h * 0.8} ${r - 2} ${cy + h * 0.5}" fill="none" stroke="${ink}" stroke-width="0.9" opacity="0.35"/>`;
  }
  return {
    def: `<clipPath id="${uid}"><path d="${almond}"/></clipPath>`,
    svg:
      `<path d="${almond}" fill="#efe6d9"/>` +
      `<g clip-path="url(#${uid})">` +
      `<circle cx="${ix}" cy="${iy}" r="${ir}" fill="${e.iris}"/>` +
      `<circle cx="${ix}" cy="${iy}" r="${ir * 0.45}" fill="#1b130e"/>` +
      `<path d="M${l} ${cy - h * 0.2} C${cx - w / 4} ${cy - h * 1.1} ${cx + w / 4} ${cy - h * 1.1} ${r} ${cy - h * 0.2}" fill="none" stroke="#3a2a20" stroke-width="${h * 0.5}" opacity="0.18"/>` +
      `</g>` +
      `<path d="M${l - 1} ${cy + 0.5} C${cx - w / 4} ${cy - h - 0.6} ${cx + w / 4} ${cy - h - 0.6} ${r + 1} ${cy}" fill="none" stroke="${ink}" stroke-width="2.1" stroke-linecap="round"/>` +
      under,
  };
}

//: Every rendered portrait needs its own clip/mask ids: the same figure is drawn twice
//: on one page (stage and avatar), and duplicate ids resolve to whichever came first.
let serial = 0;

/** The figure's inner markup. `viewBox` picks what part of it is shown. */
function draw(fig) {
  const pal = fig.palette;
  const colour = (c) => pal[c] || c;
  const tag = `s${(serial += 1)}`;
  const defs = [];
  const body = [];

  fig.parts.forEach((p, i) => {
    const uid = `${tag}-${i}`;
    if (p.part) {
      const c = colour(p.c);
      defs.push(
        `<clipPath id="${uid}c"><path d="${p.part}"/></clipPath>` +
          `<mask id="${uid}m" maskUnits="userSpaceOnUse" x="0" y="0" width="400" height="600">` +
          `<rect width="400" height="600" fill="#fff"/>` +
          `<path d="${p.part}" transform="translate(${-p.k} ${-p.k * 0.8})" fill="#000"/></mask>`,
      );
      body.push(
        `<g clip-path="url(#${uid}c)"><path d="${p.part}" fill="${c}"/>` +
          `<path d="${p.part}" fill="${shadowOf(c)}" mask="url(#${uid}m)"/></g>`,
      );
      if (p.outline !== false) {
        body.push(`<path d="${p.part}" fill="none" stroke="${fig.ink}" stroke-width="2" stroke-linejoin="round"/>`);
      }
    } else if (p.shade) {
      body.push(`<path d="${p.shade}" fill="${shadowOf(colour(p.c))}" opacity="${p.op ?? 1}"/>`);
    } else if (p.fill) {
      body.push(`<path d="${p.fill}" fill="${colour(p.c)}" opacity="${p.op ?? 1}"/>`);
    } else if (p.ink) {
      body.push(
        `<path d="${p.ink}" fill="none" stroke="${p.c ? colour(p.c) : fig.ink}" stroke-width="${p.w}" ` +
          `stroke-linecap="round" stroke-linejoin="round" opacity="${p.op ?? 1}"/>`,
      );
    } else if (p.eye) {
      const e = eye(p, fig.ink, uid);
      defs.push(e.def);
      body.push(e.svg);
    }
  });

  return `<defs>${defs.join("")}</defs>${body.join("")}`;
}

/** Unknown keys fall back rather than rendering nothing. The server already cycles
 * through the keys it has (scene.py, ``SPRITE_KEYS``), so this only catches a stale save. */
function figureFor(key) {
  return FIGURES[key] || FIGURES.sprite_a;
}

function escapeHtml(s) {
  return String(s).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

/** The half-body portrait for the stage. Anchored by the caller, not by the SVG. */
export function sprite(key, name) {
  const label = escapeHtml(name);
  return (
    `<svg class="portrait-art" viewBox="0 0 400 600" preserveAspectRatio="xMidYMin meet" role="img" aria-label="${label}">` +
    `<title>${label}</title>${draw(figureFor(key))}</svg>`
  );
}

/** The same figure cropped to the head, for the small avatar beside the speaker's name.
 *
 * Decorative: the name is printed right beside it, so it is hidden from assistive tech
 * rather than announcing the speaker twice.
 */
export function avatar(key) {
  return (
    `<svg class="avatar-art" viewBox="138 66 124 124" aria-hidden="true" focusable="false">` +
    `${draw(figureFor(key))}</svg>`
  );
}
