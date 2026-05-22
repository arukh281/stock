import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Paper sandbox",
  description: "Multi-algo EOD paper trading",
  themeColor: "#1c1f26",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
