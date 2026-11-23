"use client";

import { createContext, useContext, type ReactNode } from "react";

import { httpLobbyApi, type LobbyApi } from "@/lib/game-api";

const LobbyApiContext = createContext<LobbyApi>(httpLobbyApi);

/** Lets tests (and later, other backends) replace the HTTP API for everything below. */
export function LobbyApiProvider({ api, children }: { api: LobbyApi; children: ReactNode }) {
  return <LobbyApiContext.Provider value={api}>{children}</LobbyApiContext.Provider>;
}

export const useLobbyApi = () => useContext(LobbyApiContext);
