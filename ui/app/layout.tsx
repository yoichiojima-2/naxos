import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "naxos",
  description: "Managed agents on Google Cloud",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
