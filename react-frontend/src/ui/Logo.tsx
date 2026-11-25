import { useId } from 'react';
import { cn } from '../lib/cn';

/**
 * Inline SVG mark for Awesome Insurance: an orange shield with a chevron, crossed by a blue
 * rising arrow. The colours are fixed (they do not follow the theme); the gap where the arrow
 * crosses the other pieces is cut out with masks, so it is transparent on light and dark pages.
 * The same drawing is public/favicon.svg.
 */
export function Logo({ className }: { className?: string }) {
  // Several logos can be on a page; each needs its own gradient and mask ids.
  const u = `logo${useId().replace(/[^a-zA-Z0-9]/g, '')}`;
  return (
    <svg
      viewBox="57 -30 540 540"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="Awesome Insurance logo"
      className={cn('h-7 w-7 shrink-0', className)}
    >
      <defs>
      <linearGradient id={`${u}-og`} x1="0.2" y1="0" x2="0.5" y2="1">
        <stop offset="0" stopColor="#FFB347"/>
        <stop offset="0.55" stopColor="#F7871E"/>
        <stop offset="1" stopColor="#E4501B"/>
      </linearGradient>
      <linearGradient id={`${u}-og2`} x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#FFA533"/>
        <stop offset="1" stopColor="#D9441A"/>
      </linearGradient>
      <linearGradient id={`${u}-bg`} x1="0" y1="1" x2="1" y2="0">
        <stop offset="0" stopColor="#1D5FA6"/>
        <stop offset="0.6" stopColor="#2A7DC9"/>
        <stop offset="1" stopColor="#3C97E0"/>
      </linearGradient>
      <linearGradient id={`${u}-bg2`} x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stopColor="#2F86D2"/>
        <stop offset="1" stopColor="#18508F"/>
      </linearGradient>
      {/* the arrow's shape, used to cut a clear gap in the pieces behind it */}
      <path id={`${u}-arrow`} d="M70 436 C176 408 238 372 266 336 L306 250 L346 298 L452 182 L500 128 L470 100 L582 18 L546 156 L520 130 L446 214 L346 340 L304 296 L282 344 C246 396 170 424 70 436 Z"/>
      <mask id={`${u}-cutOrange`} maskUnits="userSpaceOnUse" x="0" y="-60" width="700" height="600">
        <rect x="0" y="-60" width="700" height="600" fill="#fff"/>
        <path d="M60 442 C170 410 235 372 262 338 L304 250 L346 296 L440 186 L520 108" fill="none" stroke="#000" strokeWidth="86" strokeLinejoin="miter"/>
      </mask>
      <mask id={`${u}-cutBlue`} maskUnits="userSpaceOnUse" x="0" y="-60" width="700" height="600">
        <rect x="0" y="-60" width="700" height="600" fill="#fff"/>
        <use href={`#${u}-arrow`} fill="#000" stroke="#000" strokeWidth="26" strokeLinejoin="miter"/>
      </mask>
    </defs>
    <g mask={`url(#${u}-cutOrange)`}>
      {/* orange shield outline: top edges meet at an apex, open at the right where the arrow passes */}
      <path d="M108 84 L316 14 L458 74 L452 104 L420 88 L318 52 L150 112 L150 232 C150 268 170 300 196 322 L172 346 C130 308 108 268 108 224 Z" fill={`url(#${u}-og)`}/>
      {/* inner chevron, two-tone */}
      <path d="M186 326 L318 92 L388 214 L350 218 L318 160 L228 332 Z" fill={`url(#${u}-og2)`}/>
      <path d="M318 92 L388 214 L350 218 L318 160 Z" fill="#C93C16" opacity="0.45"/>
      <path d="M186 326 L318 92 L318 160 L228 332 Z" fill="#FFC36B" opacity="0.28"/>
    </g>
    <g mask={`url(#${u}-cutBlue)`}>
      {/* lower crescent */}
      <path d="M236 352 C262 446 384 486 470 334 C500 282 518 222 524 168 L496 176 C486 242 452 316 398 344 C336 374 284 346 250 322 Z" fill={`url(#${u}-bg2)`}/>
    </g>
    {/* the arrow: swoosh tail, zigzag, shaft and head */}
    <use href={`#${u}-arrow`} fill={`url(#${u}-bg)`}/>
    </svg>
  );
}
