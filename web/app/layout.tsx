import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Job Search Assistant",
  description: "UK data-science & analytics job search — live dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
