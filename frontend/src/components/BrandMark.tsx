/**
 * The app mark: the same waveform as `public/favicon.svg`, so the browser tab
 * and the header cannot drift apart.
 */
export default function BrandMark() {
  return (
    <svg
      viewBox="0 0 64 64"
      width="22"
      height="22"
      aria-hidden="true"
      focusable="false"
    >
      <g fill="currentColor">
        <rect x="15.5" y="26" width="4.6" height="12" rx="2.3" />
        <rect x="24.2" y="18.5" width="4.6" height="27" rx="2.3" />
        <rect x="32.9" y="22.5" width="4.6" height="19" rx="2.3" />
        <rect x="41.6" y="29" width="4.6" height="6" rx="2.3" />
      </g>
    </svg>
  );
}
