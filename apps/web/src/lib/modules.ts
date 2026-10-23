"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/use-api";

export interface CustomModuleSummary {
  id: string;
  key: string;
  name: string;
  singular: string;
  description: string | null;
  icon: string;
  is_active: boolean;
  record_count: number;
  field_count: number;
  steps: number;
}

let cache: Promise<CustomModuleSummary[]> | null = null;
const listeners = new Set<(modules: CustomModuleSummary[]) => void>();

function load() {
  if (!cache) cache = api.get<CustomModuleSummary[]>("/custom-modules").catch(() => []);
  return cache;
}

/** Custom modules for navigation; refreshed after modules are created or changed. */
export function useCustomModules() {
  const [modules, setModules] = useState<CustomModuleSummary[]>([]);
  useEffect(() => {
    let active = true;
    void load().then((rows) => active && setModules(rows));
    listeners.add(setModules);
    return () => {
      active = false;
      listeners.delete(setModules);
    };
  }, []);
  return modules;
}

export async function refreshCustomModules() {
  cache = null;
  const rows = await load();
  listeners.forEach((listener) => listener(rows));
  return rows;
}
