"use client";

import { createContext, useContext, type ReactNode } from "react";

import { httpProgressApi, type ProgressApi } from "@/lib/progress-api";

const ProgressApiContext = createContext<ProgressApi>(httpProgressApi);

/** Lets tests replace the HTTP API for everything below. */
export function ProgressApiProvider({ api, children }: { api: ProgressApi; children: ReactNode }) {
  return <ProgressApiContext.Provider value={api}>{children}</ProgressApiContext.Provider>;
}

export const useProgressApi = () => useContext(ProgressApiContext);
