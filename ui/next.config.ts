import type { NextConfig } from "next";

const config: NextConfig = {
  output: "export",
  async rewrites() {
    if (process.env.NODE_ENV !== "development") return [];
    return [
      { source: "/v1/:path*", destination: "http://127.0.0.1:8000/v1/:path*" },
    ];
  },
};

export default config;
