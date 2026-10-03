import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { Providers } from "@/components/providers";
import { UserMenu } from "@/components/auth/UserMenu";
import { ThemeToggle } from "@/components/ThemeToggle";

export const metadata: Metadata = {
  title: "AgentChain | Decentralized AI Workforce Platform",
  description:
    "Production-grade decentralized AI workforce platform coordinating autonomous agents with on-chain settlement.",
};

const themeScript = `
  (function() {
    try {
      var saved = localStorage.getItem('agentchain-theme');
      var theme = saved || 'system';
      var resolved = 'dark';
      if (theme === 'system') {
        resolved = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
      } else {
        resolved = theme;
      }
      var root = document.documentElement;
      if (resolved === 'dark') {
        root.classList.add('dark');
        root.classList.remove('light');
      } else {
        root.classList.remove('dark');
        root.classList.add('light');
      }
      root.style.colorScheme = resolved;
    } catch (e) {}
  })();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen bg-background text-foreground font-sans antialiased">
        <Providers>
          <header className="border-b border-border bg-card/80 backdrop-blur-md sticky top-0 z-50 transition-colors">
            <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
              <Link href="/" className="flex items-center space-x-3 group">
                <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center font-bold text-primary-foreground shadow-md shadow-primary/20 group-hover:scale-105 transition-transform">
                  AC
                </div>
                <span className="font-bold text-lg tracking-tight">AgentChain</span>
                <span className="text-xs px-2 py-0.5 rounded bg-secondary text-muted-foreground border border-border">
                  Phase 5 Multi-Agent
                </span>
              </Link>
              <div className="flex items-center space-x-6">
                <nav className="flex items-center space-x-6 text-sm font-medium">
                  <Link
                    href="/orchestrations"
                    className="text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Orchestrations
                  </Link>
                  <Link
                    href="/settlements"
                    className="text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Settlements
                  </Link>
                  <Link
                    href="/notarizations"
                    className="text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Notarizations
                  </Link>
                  <Link
                    href="/agents"
                    className="text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Agents
                  </Link>
                  <Link
                    href="/dashboard"
                    className="text-muted-foreground hover:text-foreground transition-colors"
                  >
                    Dashboard
                  </Link>
                  <a
                    href="http://localhost:8000/docs"
                    target="_blank"
                    rel="noreferrer"
                    className="text-muted-foreground hover:text-foreground transition-colors"
                  >
                    API Docs
                  </a>
                </nav>
                <div className="border-l border-border pl-6 flex items-center gap-3">
                  <ThemeToggle />
                  <UserMenu />
                </div>
              </div>
            </div>
          </header>
          <main>{children}</main>
        </Providers>
      </body>
    </html>
  );
}
