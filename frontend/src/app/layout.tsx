import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Onramp", template: "%s · Onramp" },
  description: "Operations console for task marketplace cases",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col text-sm">{children}</body>
    </html>
  );
}
