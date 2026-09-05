import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  title: 'Relay — Employee IT support',
  icons: { icon: '/favicon.svg' },
  description:
    'Describe an IT issue, get approved help, and follow a saved support request.',
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
