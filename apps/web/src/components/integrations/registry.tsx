"use client";

import type { ComponentType } from "react";

import { PANELS as DOCUMENTS } from "./panels/documents";
import { PANELS as LEADS_AUTOMATION } from "./panels/leads-automation";
import { PANELS as MAIL_CALENDAR } from "./panels/mail-calendar";
import { PANELS as MESSAGING } from "./panels/messaging";
import { PANELS as MARKETING } from "./panels/marketing";
import { PANELS as ACCOUNTING } from "./panels/accounting";
import { PANELS as PAYMENTS } from "./panels/payments";
import { PANELS as SUPPORT } from "./panels/support";
import { PANELS as SALES } from "./panels/sales";
import { PANELS as ANALYTICS } from "./panels/analytics";
import type { PanelProps } from "./types";

/** Provider-specific panels shown on /integrations/[key], by integration key. */
export const INTEGRATION_PANELS: Partial<Record<string, ComponentType<PanelProps>>> = {
  ...MAIL_CALENDAR,
  ...MESSAGING,
  ...DOCUMENTS,
  ...LEADS_AUTOMATION,
  ...MARKETING,
  ...ACCOUNTING,
  ...PAYMENTS,
  ...SUPPORT,
  ...SALES,
  ...ANALYTICS,
};
