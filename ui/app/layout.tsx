import type { Metadata, Viewport } from "next";
import "./globals.css";

// No metadata `title`: Next would re-assert it after hydration, clobbering the
// per-page document.title set in app/page.tsx. The static <title> below covers
// the pre-hydration flash instead.
export const metadata: Metadata = {
  description: "Managed agents on Google Cloud",
};

export const viewport: Viewport = {
  interactiveWidget: "resizes-content",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <title>naxos</title>
      </head>
      <body>
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}catch(e){}`,
          }}
        />
        {children}
      </body>
    </html>
  );
}
