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

export const AgentsIcon = () => (
  <svg {...base}>
    <rect x="5" y="8" width="14" height="11" rx="2" />
    <path d="M12 8V4M9 4h6" />
    <path d="M9.5 13.5h.01M14.5 13.5h.01" strokeWidth={2.4} />
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

export const ArtifactsIcon = () => (
  <svg {...base}>
    <path d="M12 3 4.5 7v10l7.5 4 7.5-4V7z" />
    <path d="M4.5 7 12 11l7.5-4M12 11v10" />
  </svg>
);

export const SkillsIcon = () => (
  <svg {...base}>
    <path d="M12 3.5 14.3 9l5.7.6-4.3 3.8 1.3 5.6-5-3-5 3 1.3-5.6L4 9.6 9.7 9z" />
  </svg>
);

export const DocsIcon = () => (
  <svg {...base}>
    <path d="M12 6.5c-1.4-1.3-3.3-2-5.5-2H4.5v13h2c2.2 0 4.1.7 5.5 2 1.4-1.3 3.3-2 5.5-2h2v-13h-2c-2.2 0-4.1.7-5.5 2z" />
    <path d="M12 6.5v13" />
  </svg>
);

export const MonitoringIcon = () => (
  <svg {...base}>
    <path d="M4 4.5v15h16" />
    <path d="M7.5 14 11 10l3 3 4.5-5.5" />
  </svg>
);

export const BackIcon = () => (
  <svg {...base}>
    <path d="M14.5 6 8.5 12l6 6" />
  </svg>
);
