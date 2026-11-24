"use client";

import { createContext, useContext, type ReactNode } from "react";

import { httpSettingsApi, type SettingsApi } from "@/lib/settings-api";

const SettingsApiContext = createContext<SettingsApi>(httpSettingsApi);

/** Lets tests replace the HTTP API for everything below. */
export function SettingsApiProvider({ api, children }: { api: SettingsApi; children: ReactNode }) {
  return <SettingsApiContext.Provider value={api}>{children}</SettingsApiContext.Provider>;
}

export const useSettingsApi = () => useContext(SettingsApiContext);
