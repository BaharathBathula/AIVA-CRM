"use client";

import type { ComponentType } from "react";

import type { PanelProps } from "../types";

/**
 * Provider panels for the leads-automation group. Each panel renders the integration's own
 * settings and tools below the shared connection card on /integrations/[key].
 */
export const PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {};
