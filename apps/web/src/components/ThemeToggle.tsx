"use client";

import React, { useState, useRef, useEffect } from "react";
import { useTheme, Theme } from "@/context/ThemeContext";
import { Sun, Moon, Laptop, Check } from "lucide-react";

export function ThemeToggle() {
  const { theme, resolvedTheme, setTheme, toggleTheme } = useTheme();
  const [isOpen, setIsOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Close dropdown on outside click
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Avoid hydration mismatch by rendering a placeholder until mounted
  if (!mounted) {
    return (
      <div className="w-9 h-9 rounded-lg bg-secondary/40 border border-border/50 animate-pulse" />
    );
  }

  const options: { value: Theme; label: string; icon: React.ReactNode }[] = [
    {
      value: "light",
      label: "Light",
      icon: <Sun className="w-4 h-4 text-amber-500" />,
    },
    {
      value: "dark",
      label: "Dark",
      icon: <Moon className="w-4 h-4 text-indigo-400" />,
    },
    {
      value: "system",
      label: "System",
      icon: <Laptop className="w-4 h-4 text-muted-foreground" />,
    },
  ];

  return (
    <div className="relative inline-block text-left" ref={dropdownRef}>
      <div className="flex items-center rounded-lg border border-border bg-card/80 p-0.5 shadow-sm hover:border-primary/40 transition-all duration-200">
        {/* Direct Toggle Button */}
        <button
          type="button"
          onClick={toggleTheme}
          className="relative flex items-center justify-center w-8 h-8 rounded-md text-foreground hover:bg-secondary/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary transition-all duration-200"
          title={`Switch to ${resolvedTheme === "dark" ? "light" : "dark"} theme (Currently ${theme})`}
          aria-label="Toggle theme"
        >
          {resolvedTheme === "dark" ? (
            <Moon className="w-4 h-4 text-indigo-400 transition-transform duration-300 rotate-0 hover:-rotate-12" />
          ) : (
            <Sun className="w-4 h-4 text-amber-500 transition-transform duration-300 rotate-0 hover:rotate-45" />
          )}
        </button>

        {/* Dropdown open trigger */}
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className="flex items-center justify-center px-1.5 h-8 text-[11px] font-medium text-muted-foreground hover:text-foreground hover:bg-secondary/80 rounded-r-md transition-colors"
          title="Choose theme mode"
          aria-haspopup="true"
          aria-expanded={isOpen}
        >
          <span className="capitalize hidden sm:inline mr-1 text-xs">
            {theme}
          </span>
          <svg
            className={`w-3 h-3 text-muted-foreground transition-transform duration-200 ${isOpen ? "rotate-180" : ""}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>

      {/* Dropdown Menu */}
      {isOpen && (
        <div className="absolute right-0 mt-2 w-36 rounded-xl bg-card border border-border shadow-xl backdrop-blur-md z-50 py-1 animate-in fade-in zoom-in-95 duration-150">
          <div className="px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
            Appearance
          </div>
          {options.map((opt) => {
            const isActive = theme === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  setTheme(opt.value);
                  setIsOpen(false);
                }}
                className={`w-full flex items-center justify-between px-3 py-1.5 text-xs rounded-lg transition-colors ${
                  isActive
                    ? "bg-primary/10 text-primary font-semibold"
                    : "text-foreground hover:bg-secondary/80"
                }`}
              >
                <div className="flex items-center gap-2">
                  {opt.icon}
                  <span>{opt.label}</span>
                </div>
                {isActive && <Check className="w-3.5 h-3.5 text-primary" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
