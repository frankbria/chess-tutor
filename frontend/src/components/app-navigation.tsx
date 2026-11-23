"use client";

import { ChartLineData01Icon, Home01Icon, Settings01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const NAVIGATION_ITEMS = [
  { href: "/", label: "Home", icon: Home01Icon },
  { href: "/progress", label: "Progress", icon: ChartLineData01Icon },
  { href: "/settings", label: "Settings", icon: Settings01Icon },
] as const;

export function AppNavigation({ currentPath }: { currentPath: string }) {
  return (
    <nav
      aria-label="Main"
      data-slot="app-navigation"
      className="fixed inset-x-0 bottom-0 z-10 border-t bg-card/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:static md:pb-0 md:w-56 md:border-t-0 md:border-r"
    >
      <ul className="flex justify-around md:flex-col md:gap-1 md:p-3">
        {NAVIGATION_ITEMS.map(({ href, label, icon }) => {
          const isCurrent = currentPath === href;
          return (
            <li key={href}>
              <Link
                href={href}
                aria-current={isCurrent ? "page" : undefined}
                className={cn(
                  "flex flex-col items-center gap-1 px-4 py-2 text-xs font-medium text-muted-foreground transition-all outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 md:flex-row md:gap-3 md:rounded-lg md:text-sm",
                  isCurrent && "text-primary md:bg-primary/10",
                )}
              >
                <HugeiconsIcon icon={icon} size={22} aria-hidden="true" />
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function CurrentAppNavigation() {
  return <AppNavigation currentPath={usePathname()} />;
}
