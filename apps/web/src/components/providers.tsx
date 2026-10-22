"use client";

import { usePathname, useRouter } from "next/navigation";
import { CheckCircle2, AlertCircle } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { fetchCurrentSession, setCachedSession, type CurrentSession } from "@/lib/auth";

export const PUBLIC_ROUTES = ["/login", "/signup", "/forgot-password", "/reset-password", "/accept-invite"];

export function isPublicRoute(pathname: string | null) {
  return PUBLIC_ROUTES.some((route) => pathname === route || pathname?.startsWith(`${route}/`));
}

// ---------------------------------------------------------------- Session

interface SessionContextValue {
  session: CurrentSession | null;
  loading: boolean;
  refresh: () => Promise<CurrentSession | null>;
  setSession: (session: CurrentSession | null) => void;
  can: (permission: string) => boolean;
}

const SessionContext = createContext<SessionContextValue>({
  session: null,
  loading: true,
  refresh: async () => null,
  setSession: () => undefined,
  can: () => false,
});

function SessionProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [session, setSessionState] = useState<CurrentSession | null>(null);
  const [loading, setLoading] = useState(true);
  const publicRoute = isPublicRoute(pathname);

  const setSession = useCallback((next: CurrentSession | null) => {
    setCachedSession(next);
    setSessionState(next);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchCurrentSession();
      setSession(next);
      return next;
    } catch {
      setSession(null);
      return null;
    } finally {
      setLoading(false);
    }
  }, [setSession]);

  useEffect(() => {
    if (publicRoute) {
      setLoading(false);
      return;
    }
    if (session) return;
    void refresh().then((next) => {
      if (!next) {
        const nextPath = window.location.pathname + window.location.search;
        router.replace(nextPath === "/" ? "/login" : `/login?next=${encodeURIComponent(nextPath)}`);
      }
    });
  }, [publicRoute, refresh, router, session]);

  const value = useMemo<SessionContextValue>(() => {
    const permissions = new Set(session?.permissions ?? []);
    return {
      session,
      loading,
      refresh,
      setSession,
      can: (permission: string) => permissions.has(permission),
    };
  }, [loading, refresh, session, setSession]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  return useContext(SessionContext);
}

// ---------------------------------------------------------------- Toasts

type Toast = { id: number; message: string; tone: "success" | "danger" };

const ToastContext = createContext<(message: string, tone?: Toast["tone"]) => void>(() => undefined);

function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const counter = useRef(0);

  const push = useCallback((message: string, tone: Toast["tone"] = "success") => {
    counter.current += 1;
    const id = counter.current;
    setToasts((current) => [...current.slice(-3), { id, message, tone }]);
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4200);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="ui-toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`ui-toast tone-${toast.tone}`}>
            {toast.tone === "success" ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}
            <span>{toast.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

// ---------------------------------------------------------------- Theme

export type Theme = "light" | "dark";

const ThemeContext = createContext<{ theme: Theme; setTheme: (theme: Theme) => void }>({
  theme: "light",
  setTheme: () => undefined,
});

function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("light");

  // The inline script in layout.tsx already applied the theme before paint.
  useEffect(() => {
    setThemeState(document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("aiva-theme", next);
    } catch {
      // Private mode or blocked storage: the choice lasts for this page only.
    }
  }, []);

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}

// ---------------------------------------------------------------- App shell (mobile nav)

const ShellContext = createContext<{ navOpen: boolean; setNavOpen: (open: boolean) => void }>({
  navOpen: false,
  setNavOpen: () => undefined,
});

function ShellProvider({ children }: { children: ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);
  const pathname = usePathname();
  useEffect(() => setNavOpen(false), [pathname]);
  return <ShellContext.Provider value={{ navOpen, setNavOpen }}>{children}</ShellContext.Provider>;
}

export function useShell() {
  return useContext(ShellContext);
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider>
      <SessionProvider>
        <ToastProvider>
          <ShellProvider>{children}</ShellProvider>
        </ToastProvider>
      </SessionProvider>
    </ThemeProvider>
  );
}
