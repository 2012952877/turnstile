import type { SVGProps } from "react"

/** The Turnstile mark: a three-armed rotor and the post it turns against.
 *
 *  The name and the product are the same metaphor — a gate that admits one thing at a
 *  time and counts it — so the mark is the mechanism itself rather than an abstraction of
 *  it. Four other directions were drawn and rejected against real sizes: a rotor between
 *  two posts collapsed into the letter "A" at 16 px, a rotor with one arm leading right
 *  read as an arrow, a framed version fought the sidebar slot's own rounded border, and a
 *  bare tripod risked reading as a badge. The single post keeps the silhouette asymmetric,
 *  which is what stops it looking like one.
 *
 *  Geometry follows lucide so it sits with the rest of the icon set: 24x24 box, 2px
 *  stroke, round caps and joins, `currentColor`. The rotor is offset 2 units below the
 *  box centre on purpose: its own visual mass spans 5.9-18, so that offset is what makes
 *  it sit level against the post (1.4 above, 1.5 below) rather than riding high with the
 *  post's tail hanging beneath it.
 */
export function TurnstileMark({ size = 16, ...props }: SVGProps<SVGSVGElement> & { size?: number }) {
  return <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    {...props}
  >
    <path d="M21 4.5v15" />
    <circle cx="10.3" cy="14" r="2.3" />
    <path d="M10.3 11.7V5.9" />
    <path d="M8.3 15.15 3.4 18" />
    <path d="M12.3 15.15 17.2 18" />
  </svg>
}
