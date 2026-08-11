const base = {
  width: 16,
  height: 16,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

export const SessionsIcon = () => (
  <svg {...base}>
    <path d="M4 5h16v11H8l-4 4z" />
    <path d="M8 9h8M8 12.5h5" />
  </svg>
);

export const DeploymentsIcon = () => (
  <svg {...base}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </svg>
);

export const VaultsIcon = () => (
  <svg {...base}>
    <rect x="5" y="10.5" width="14" height="9" rx="2" />
    <path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" />
    <path d="M12 14.5v1.5" />
  </svg>
);

export const MemoryIcon = () => (
  <svg {...base}>
    <ellipse cx="12" cy="6" rx="7" ry="2.8" />
    <path d="M5 6v12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8V6" />
    <path d="M5 12c0 1.5 3.1 2.8 7 2.8s7-1.3 7-2.8" />
  </svg>
);

export const BackIcon = () => (
  <svg {...base}>
    <path d="M14.5 6 8.5 12l6 6" />
  </svg>
);
