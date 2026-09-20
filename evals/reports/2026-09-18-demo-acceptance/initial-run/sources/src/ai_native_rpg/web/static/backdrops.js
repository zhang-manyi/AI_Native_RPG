/* Backdrops: one simple scene per place *kind* (docs/12 §4.2).
 *
 * The pack names the kind (`backdrop: interior`), the front end owns the pixels — so a
 * scenario ships no art and no story name appears here. An unknown or absent kind falls
 * back to the plain lamp gradient rather than failing, which is what lets a pack adopt
 * the field gradually.
 *
 * Drawn in a 100x60 viewBox stretched across the stage, deliberately loose: this sits
 * behind the characters and must never compete with the dialogue for attention. Cool
 * darks, one warm light source, matching the palette in styles.css.
 */

const BACKDROPS = {
  // A room seen from inside: back wall, a shuttered window, floorboards.
  interior: `
    <rect x="0" y="0" width="100" height="42" fill="#161b26" />
    <rect x="0" y="42" width="100" height="18" fill="#10141d" />
    <line x1="0" y1="42" x2="100" y2="42" stroke="#2a3345" stroke-width="0.4" />
    <!-- window, curtains drawn nearly shut: the pack describes this house that way -->
    <rect x="61" y="10" width="22" height="20" fill="#0d1017" stroke="#2f3a4d" stroke-width="0.6" />
    <rect x="61" y="10" width="9" height="20" fill="#1b2130" />
    <rect x="74" y="10" width="9" height="20" fill="#1b2130" />
    <line x1="72" y1="10" x2="72" y2="30" stroke="#2f3a4d" stroke-width="0.5" />
    <!-- lamp glow spilling down the wall -->
    <ellipse cx="30" cy="20" rx="26" ry="18" fill="rgba(240,192,122,0.07)" />
    <!-- floorboards -->
    <line x1="18" y1="42" x2="10" y2="60" stroke="#1a1f2b" stroke-width="0.5" />
    <line x1="44" y1="42" x2="41" y2="60" stroke="#1a1f2b" stroke-width="0.5" />
    <line x1="70" y1="42" x2="73" y2="60" stroke="#1a1f2b" stroke-width="0.5" />
    <line x1="94" y1="42" x2="99" y2="60" stroke="#1a1f2b" stroke-width="0.5" />`,

  // Treeline in mist. Trunks thin out toward the middle so the characters read clearly.
  forest: `
    <rect x="0" y="0" width="100" height="60" fill="#0e131b" />
    <ellipse cx="50" cy="46" rx="60" ry="16" fill="rgba(127,179,213,0.05)" />
    <g fill="#141a24">
      <rect x="4" y="6" width="5" height="42" />
      <rect x="15" y="12" width="4" height="36" />
      <rect x="26" y="2" width="6" height="46" />
      <rect x="80" y="8" width="5" height="40" />
      <rect x="90" y="14" width="4" height="34" />
      <rect x="70" y="4" width="4" height="44" />
    </g>
    <g fill="#111721" opacity="0.85">
      <ellipse cx="7" cy="8" rx="12" ry="9" />
      <ellipse cx="29" cy="4" rx="14" ry="10" />
      <ellipse cx="83" cy="9" rx="13" ry="9" />
      <ellipse cx="71" cy="5" rx="11" ry="8" />
    </g>
    <!-- mist bands: the ground is still wet, it rained that night -->
    <rect x="0" y="40" width="100" height="5" fill="rgba(180,200,220,0.045)" />
    <rect x="0" y="47" width="100" height="4" fill="rgba(180,200,220,0.035)" />
    <line x1="0" y1="48" x2="100" y2="48" stroke="#1b2230" stroke-width="0.4" />`,

  // Open ground with a well and low roofs on the skyline.
  square: `
    <rect x="0" y="0" width="100" height="60" fill="#111621" />
    <rect x="0" y="44" width="100" height="16" fill="#0e131c" />
    <line x1="0" y1="44" x2="100" y2="44" stroke="#2a3345" stroke-width="0.4" />
    <!-- roofs -->
    <g fill="#161c28">
      <path d="M2 44 L2 30 L14 22 L26 30 L26 44 Z" />
      <path d="M74 44 L74 26 L86 18 L98 26 L98 44 Z" />
    </g>
    <!-- the well: villagers gather here, quieter since the disappearance -->
    <ellipse cx="50" cy="45" rx="9" ry="3" fill="#1a212e" />
    <rect x="43" y="36" width="14" height="9" fill="#19202c" />
    <path d="M42 36 L50 30 L58 36 Z" fill="#141a24" />
    <ellipse cx="50" cy="26" rx="20" ry="14" fill="rgba(240,192,122,0.05)" />`,

  // Low room, beams, bottles behind a counter.
  tavern: `
    <rect x="0" y="0" width="100" height="60" fill="#171b22" />
    <rect x="0" y="45" width="100" height="15" fill="#11151c" />
    <rect x="0" y="4" width="100" height="3" fill="#1e242f" />
    <rect x="0" y="16" width="100" height="2.5" fill="#1c2028" />
    <!-- shelf of bottles nobody has touched today -->
    <rect x="8" y="24" width="34" height="1.6" fill="#242a35" />
    <g fill="#2b3340">
      <rect x="10" y="18" width="3" height="6" />
      <rect x="16" y="17" width="3" height="7" />
      <rect x="22" y="19" width="3" height="5" />
      <rect x="28" y="16" width="3" height="8" />
      <rect x="34" y="18" width="3" height="6" />
    </g>
    <!-- counter -->
    <rect x="60" y="30" width="40" height="15" fill="#1b212b" />
    <rect x="60" y="30" width="40" height="1.8" fill="#2a3240" />
    <ellipse cx="50" cy="14" rx="24" ry="14" fill="rgba(240,192,122,0.06)" />`,
};

/** Markup for a place kind, or empty string when the pack named none. */
export function backdrop(kind) {
  const art = BACKDROPS[kind];
  if (!art) return "";
  return `<svg class="backdrop" viewBox="0 0 100 60" preserveAspectRatio="none"
    aria-hidden="true" focusable="false">${art}</svg>`;
}
