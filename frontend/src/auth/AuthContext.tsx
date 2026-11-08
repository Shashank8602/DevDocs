import { useQueryClient } from "@tanstack/react-query";
import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { authApi, Me } from "../api/auth";
import { previewAs, UNAUTHORIZED_EVENT } from "../api/http";

type Phase = "loading" | "setup" | "signed-out" | "signed-in" | "offline";

interface AuthValue {
  phase: Phase;
  user: Me | null;
  isAdmin: boolean;
  /** True while an admin is looking at the app as another person. */
  previewing: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  setup: (email: string, password: string, name: string) => Promise<void>;
  signOut: () => Promise<void>;
  startPreview: (userId: string) => void;
  stopPreview: () => void;
  retry: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [phase, setPhase] = useState<Phase>("loading");
  const [user, setUser] = useState<Me | null>(null);

  const load = useCallback(async () => {
    try {
      const status = await authApi.status();
      if (status.needs_setup) {
        setUser(null);
        setPhase("setup");
        return;
      }
      try {
        setUser(await authApi.me());
        setPhase("signed-in");
      } catch (e) {
        if (previewAs.get()) {  // a stale preview id (the person was deleted): leave preview and retry once
          previewAs.clear();
          return load();
        }
        setUser(null);
        setPhase("signed-out");
      }
    } catch {
      setPhase("offline");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    const onGone = () => {
      previewAs.clear();
      setUser(null);
      setPhase((p) => (p === "signed-in" ? "signed-out" : p));
      qc.clear();
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onGone);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onGone);
  }, [qc]);

  const signIn = useCallback(async (email: string, password: string) => {
    const me = await authApi.login(email, password);
    qc.clear();
    setUser(me);
    setPhase("signed-in");
  }, [qc]);

  const setup = useCallback(async (email: string, password: string, name: string) => {
    const me = await authApi.setup({ email, password, name });
    qc.clear();
    setUser(me);
    setPhase("signed-in");
  }, [qc]);

  const signOut = useCallback(async () => {
    previewAs.clear();
    await authApi.logout().catch(() => undefined);
    qc.clear();
    setUser(null);
    setPhase("signed-out");
  }, [qc]);

  // Switching preview reloads who we are: every query then runs as that person.
  const startPreview = useCallback((userId: string) => {
    previewAs.set(userId);
    qc.clear();
    void load();
  }, [qc, load]);
  const stopPreview = useCallback(() => {
    previewAs.clear();
    qc.clear();
    void load();
  }, [qc, load]);

  const value = useMemo<AuthValue>(() => ({
    phase, user, isAdmin: user?.role === "admin", previewing: !!user?.previewing,
    signIn, setup, signOut, startPreview, stopPreview, retry: () => void load(),
  }), [phase, user, signIn, setup, signOut, startPreview, stopPreview, load]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
