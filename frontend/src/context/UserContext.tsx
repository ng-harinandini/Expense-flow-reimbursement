"use client";

import * as React from "react";
import { getAccessToken, getStoredUser, saveUser } from "@/lib/authStorage";
import { fetchMe } from "@/api/auth";
import type { AuthenticatedUser } from "@/types";

interface UserContextValue {
  user: AuthenticatedUser | null;
  /** False until the stored session has been read on the client. */
  isLoaded: boolean;
  setUser: (user: AuthenticatedUser | null) => void;
}

const UserContext = React.createContext<UserContextValue | null>(null);

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<AuthenticatedUser | null>(null);
  const [isLoaded, setIsLoaded] = React.useState(false);

  // localStorage is unavailable during SSR, so hydrate on mount instead of in
  // useState's initializer — otherwise server and client markup disagree.
  React.useEffect(() => {
    const storedUser = getStoredUser();
    if (storedUser) {
      setUser(storedUser);
      setIsLoaded(true);
      return;
    }

    // Access token present but no user cookie — fetch user details from the API.
    const accessToken = getAccessToken();
    if (accessToken) {
      fetchMe()
        .then((me) => {
          saveUser(me);
          setUser(me);
        })
        .catch(() => {
          // Token may be expired or invalid; leave user as null so the middleware
          // will redirect to /login on the next navigation.
        })
        .finally(() => {
          setIsLoaded(true);
        });
    } else {
      setIsLoaded(true);
    }
  }, []);

  const value = React.useMemo<UserContextValue>(
    () => ({ user, isLoaded, setUser }),
    [user, isLoaded],
  );

  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
}

export function useUser(): UserContextValue {
  const context = React.useContext(UserContext);
  if (!context) {
    throw new Error("useUser must be used within a UserProvider");
  }
  return context;
}
