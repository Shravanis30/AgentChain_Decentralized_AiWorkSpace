"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserMenu } from "@/components/auth/UserMenu";
import { ThemeToggle } from "@/components/ThemeToggle";
import {
  Menu,
  X,
  ExternalLink,
  Layers,
  CircleDollarSign,
  PieChart,
  FileCheck,
  Bot,
  LayoutDashboard,
  Server,
  Code2,
} from "lucide-react";

export function Header() {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const pathname = usePathname();

  // Close mobile menu on route change
  useEffect(() => {
    setMobileMenuOpen(false);
  }, [pathname]);

  // Prevent body scroll when mobile menu is open
  useEffect(() => {
    if (mobileMenuOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [mobileMenuOpen]);

  // Close menu on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && mobileMenuOpen) {
        setMobileMenuOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [mobileMenuOpen]);

  const navLinks = [
    { href: "/orchestrations", label: "Orchestrations", icon: Layers },
    { href: "/settlements", label: "Settlements", icon: CircleDollarSign },
    { href: "/distributions", label: "Distributions", icon: PieChart },
    { href: "/notarizations", label: "Notarizations", icon: FileCheck },
    { href: "/agents", label: "Agents", icon: Bot },
    { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  ];

  const isActive = (href: string) => {
    if (href === "/") return pathname === "/";
    return pathname.startsWith(href);
  };

  return (
    <header className="border-b border-border bg-card/80 backdrop-blur-md sticky top-0 z-50 transition-colors w-full">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-2 sm:gap-4">
        {/* Logo / Brand */}
        <Link href="/" className="flex items-center space-x-2 sm:space-x-3 group shrink-0">
          <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center font-bold text-primary-foreground shadow-md shadow-primary/20 group-hover:scale-105 transition-transform text-sm">
            AC
          </div>
          <span className="font-bold text-base sm:text-lg tracking-tight">AgentChain</span>
          <span className="hidden xl:inline-block text-[11px] px-2 py-0.5 rounded bg-secondary text-muted-foreground border border-border">
            Multi-Agent
          </span>
        </Link>

        {/* Desktop Navigation (>= lg) */}
        <nav className="hidden lg:flex items-center space-x-1 xl:space-x-2 text-sm font-medium">
          {navLinks.map((link) => {
            const active = isActive(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`px-3 py-1.5 rounded-md transition-colors ${
                  active
                    ? "text-foreground bg-secondary font-semibold"
                    : "text-muted-foreground hover:text-foreground hover:bg-secondary/50"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
          <a
            href="http://localhost:8000/docs"
            target="_blank"
            rel="noreferrer"
            className="px-3 py-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-secondary/50 transition-colors inline-flex items-center gap-1"
          >
            <span>API Docs</span>
            <ExternalLink className="w-3 h-3 opacity-70" />
          </a>
        </nav>

        {/* Header Right Cluster: Controls + Hamburger */}
        <div className="flex items-center gap-2 sm:gap-3">
          <ThemeToggle />
          <UserMenu />

          {/* Hamburger toggle button for Mobile/Tablet (< lg) */}
          <button
            type="button"
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="lg:hidden p-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary focus:outline-none focus:ring-2 focus:ring-primary transition-colors"
            aria-label={mobileMenuOpen ? "Close navigation menu" : "Open navigation menu"}
            aria-expanded={mobileMenuOpen}
          >
            {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
          </button>
        </div>
      </div>

      {/* Mobile / Tablet Drawer & Backdrop */}
      {mobileMenuOpen && (
        <div className="fixed inset-0 top-16 z-50 lg:hidden">
          {/* Backdrop */}
          <div
            className="fixed inset-0 bg-background/80 backdrop-blur-sm transition-opacity"
            onClick={() => setMobileMenuOpen(false)}
            aria-hidden="true"
          />

          {/* Drawer content */}
          <div className="relative z-10 w-full max-w-sm bg-card border-r border-b border-border shadow-2xl h-[calc(100vh-4rem)] overflow-y-auto flex flex-col justify-between p-4 sm:p-6 animate-in slide-in-from-left duration-200">
            <div className="space-y-6">
              <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground px-3">
                Navigation
              </div>
              <nav className="space-y-1">
                {navLinks.map((link) => {
                  const Icon = link.icon;
                  const active = isActive(link.href);
                  return (
                    <Link
                      key={link.href}
                      href={link.href}
                      onClick={() => setMobileMenuOpen(false)}
                      className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                        active
                          ? "bg-primary text-primary-foreground font-semibold shadow-sm"
                          : "text-foreground hover:bg-secondary"
                      }`}
                    >
                      <Icon className="w-4 h-4 shrink-0" />
                      <span>{link.label}</span>
                    </Link>
                  );
                })}
              </nav>

              <div className="pt-4 border-t border-border space-y-1">
                <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground px-3 mb-2">
                  Developer & Operator
                </div>
                <Link
                  href="/operator/workers"
                  onClick={() => setMobileMenuOpen(false)}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                    pathname === "/operator/workers"
                      ? "bg-primary text-primary-foreground font-semibold shadow-sm"
                      : "text-foreground hover:bg-secondary"
                  }`}
                >
                  <Server className="w-4 h-4 shrink-0" />
                  <span>Cluster Workers</span>
                </Link>
                <a
                  href="http://localhost:8000/docs"
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center justify-between px-3 py-2.5 rounded-lg text-sm font-medium text-foreground hover:bg-secondary transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <Code2 className="w-4 h-4 shrink-0" />
                    <span>FastAPI Swagger Docs</span>
                  </div>
                  <ExternalLink className="w-3.5 h-3.5 text-muted-foreground" />
                </a>
              </div>
            </div>

            <div className="pt-6 border-t border-border text-xs text-muted-foreground space-y-1">
              <div className="font-semibold text-foreground">AgentChain Platform</div>
              <div>Cryptographic multi-agent orchestration & EVM settlement.</div>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
