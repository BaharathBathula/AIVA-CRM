import type {
  Contact,
} from "@/types/contact";

import type {
  Activity,
} from "@/types/activity";

import type {
  EmailThread,
} from "@/types/email";

import type {
  Opportunity,
} from "@/types/opportunity";

import type {
  Task,
} from "@/types/task";


export type ContactHealthLabel =
  | "Strong"
  | "Healthy"
  | "Needs Attention"
  | "At Risk";


export type ContactEngagementTrend =
  | "Improving"
  | "Stable"
  | "Cooling"
  | "Deteriorating"
  | "Insufficient Data";


export type ContactRiskLevel =
  | "Low"
  | "Medium"
  | "High"
  | "Critical";



export type ContactActionPriority =
  | "Immediate"
  | "This Week"
  | "This Month"
  | "Monitor";


export interface ContactHealthResult {
  score: number;

  label: ContactHealthLabel;

  lastInteractionAt:
    string | null;

  daysSinceLastInteraction:
    number | null;

  recentTouchpoints: number;

  emailConversationCount: number;

  openOpportunityCount: number;

  overdueTaskCount: number;

  currentPeriodTouchpoints: number;

  previousPeriodTouchpoints: number;

  touchpointDelta: number;

  engagementTrend:
    ContactEngagementTrend;

  riskLevel:
    ContactRiskLevel;

  actionPriority:
    ContactActionPriority;

  actionType: string;

  followUpDays:
    number | null;

  actionReason: string;

  positiveSignals: string[];

  riskSignals: string[];

  recommendedAction: string;
}


const DAY_MS =
  24
  *
  60
  *
  60
  *
  1000;


function clamp(
  value: number,
  minimum: number,
  maximum: number
) {
  return Math.max(
    minimum,
    Math.min(
      maximum,
      value
    )
  );
}


function getTimestamp(
  value:
    string
    |
    null
    |
    undefined
): number | null {
  if (!value) {
    return null;
  }

  const timestamp =
    new Date(
      value
    ).getTime();

  return Number.isFinite(
    timestamp
  )
    ? timestamp
    : null;
}


export function calculateContactHealth({
  contact,
  activities,
  emailThreads,
  opportunities,
  tasks,
}: {
  contact: Contact;
  activities: Activity[];
  emailThreads: EmailThread[];
  opportunities: Opportunity[];
  tasks: Task[];
}): ContactHealthResult {
  const now =
    Date.now();


  const activityTimes =
    activities
      .map(
        (
          activity
        ) =>
          getTimestamp(
            activity.occurred_at
          )
      )
      .filter(
        (
          value
        ): value is number =>
          value !== null
      );


  const emailTimes =
    emailThreads
      .map(
        (
          thread
        ) =>
          getTimestamp(
            thread.last_message_at
          )
      )
      .filter(
        (
          value
        ): value is number =>
          value !== null
      );


  const interactionTimes = [
    ...activityTimes,
    ...emailTimes,
  ];


  const latestInteractionTime =
    interactionTimes.length
      ? Math.max(
          ...interactionTimes
        )
      : null;


  const lastInteractionAt =
    latestInteractionTime
      ? new Date(
          latestInteractionTime
        ).toISOString()
      : null;


  const daysSinceLastInteraction =
    latestInteractionTime
      ? Math.max(
          0,
          Math.floor(
            (
              now
              -
              latestInteractionTime
            )
            /
            DAY_MS
          )
        )
      : null;


  const currentPeriodStart =
    now
    -
    (
      30
      *
      DAY_MS
    );


  const previousPeriodStart =
    now
    -
    (
      60
      *
      DAY_MS
    );


  const currentPeriodTouchpoints =
    interactionTimes.filter(
      (
        timestamp
      ) =>
        timestamp
        >=
        currentPeriodStart
    ).length;


  const previousPeriodTouchpoints =
    interactionTimes.filter(
      (
        timestamp
      ) =>
        timestamp
        >=
        previousPeriodStart
        &&
        timestamp
        <
        currentPeriodStart
    ).length;


  const touchpointDelta =
    currentPeriodTouchpoints
    -
    previousPeriodTouchpoints;


  let engagementTrend:
    ContactEngagementTrend;


  if (
    currentPeriodTouchpoints === 0
    &&
    previousPeriodTouchpoints === 0
  ) {
    engagementTrend =
      "Insufficient Data";
  } else if (
    previousPeriodTouchpoints === 0
    &&
    currentPeriodTouchpoints > 0
  ) {
    engagementTrend =
      "Improving";
  } else if (
    touchpointDelta >= 2
  ) {
    engagementTrend =
      "Improving";
  } else if (
    touchpointDelta <= -3
  ) {
    engagementTrend =
      "Deteriorating";
  } else if (
    touchpointDelta < 0
  ) {
    engagementTrend =
      "Cooling";
  } else {
    engagementTrend =
      "Stable";
  }


  const recentTouchpoints =
    currentPeriodTouchpoints;


  const openOpportunities =
    opportunities.filter(
      (
        opportunity
      ) =>
        opportunity.closed_at
        === null
    );


  const overdueTasks =
    tasks.filter(
      (
        task
      ) => {
        if (
          !task.due_at
          ||
          task.status
            === "completed"
          ||
          task.status
            === "cancelled"
        ) {
          return false;
        }

        const dueTime =
          getTimestamp(
            task.due_at
          );

        return (
          dueTime !== null
          &&
          dueTime < now
        );
      }
    );


  let score = 10;

  const positiveSignals:
    string[] = [];

  const riskSignals:
    string[] = [];


  // -------------------------------------------------------
  // Interaction recency
  // -------------------------------------------------------

  if (
    daysSinceLastInteraction
    === null
  ) {
    score -= 10;

    riskSignals.push(
      "No recorded interaction history"
    );
  } else if (
    daysSinceLastInteraction
    <= 7
  ) {
    score += 30;

    positiveSignals.push(
      "Interaction within the last 7 days"
    );
  } else if (
    daysSinceLastInteraction
    <= 30
  ) {
    score += 22;

    positiveSignals.push(
      "Interaction within the last 30 days"
    );
  } else if (
    daysSinceLastInteraction
    <= 60
  ) {
    score += 12;

    riskSignals.push(
      "Relationship has not been touched recently"
    );
  } else if (
    daysSinceLastInteraction
    <= 90
  ) {
    score += 5;

    riskSignals.push(
      "Relationship is cooling"
    );
  } else {
    score -= 15;

    riskSignals.push(
      "No interaction in more than 90 days"
    );
  }


  // -------------------------------------------------------
  // Engagement volume
  // -------------------------------------------------------

  if (
    recentTouchpoints > 0
  ) {
    score += Math.min(
      recentTouchpoints
      *
      3,
      15
    );

    positiveSignals.push(
      `${recentTouchpoints} touchpoint${
        recentTouchpoints === 1
          ? ""
          : "s"
      } in the last 30 days`
    );
  }


  if (
    engagementTrend
    === "Improving"
  ) {
    positiveSignals.push(
      "Engagement volume is improving"
    );
  }


  if (
    engagementTrend
    === "Cooling"
  ) {
    riskSignals.push(
      "Engagement volume is declining"
    );
  }


  if (
    engagementTrend
    === "Deteriorating"
  ) {
    riskSignals.push(
      "Engagement has deteriorated significantly"
    );
  }


  // -------------------------------------------------------
  // Email relationship
  // -------------------------------------------------------

  if (
    emailThreads.length > 0
  ) {
    score += Math.min(
      emailThreads.length
      *
      3,
      15
    );

    positiveSignals.push(
      `${emailThreads.length} linked email conversation${
        emailThreads.length === 1
          ? ""
          : "s"
      }`
    );
  } else {
    riskSignals.push(
      "No linked email conversations"
    );
  }


  // -------------------------------------------------------
  // Commercial relationship
  // -------------------------------------------------------

  if (
    openOpportunities.length > 0
  ) {
    score += 10;

    positiveSignals.push(
      `${openOpportunities.length} active sales opportunit${
        openOpportunities.length === 1
          ? "y"
          : "ies"
      }`
    );
  }


  // -------------------------------------------------------
  // Account relationship
  // -------------------------------------------------------

  if (
    contact.account_id
  ) {
    score += 10;

    positiveSignals.push(
      "Linked to an account"
    );
  } else {
    riskSignals.push(
      "No account relationship"
    );
  }


  if (
    contact.is_primary
  ) {
    score += 10;

    positiveSignals.push(
      "Primary account contact"
    );
  }


  // -------------------------------------------------------
  // Profile completeness
  // -------------------------------------------------------

  const profileFields = [
    contact.email,
    contact.phone
      ||
      contact.mobile,
    contact.job_title,
    contact.department,
    contact.linkedin_url,
  ];


  const completedProfileFields =
    profileFields.filter(
      Boolean
    ).length;


  score += Math.round(
    (
      completedProfileFields
      /
      profileFields.length
    )
    *
    10
  );


  if (
    completedProfileFields
    >= 4
  ) {
    positiveSignals.push(
      "Contact profile is well populated"
    );
  }


  // -------------------------------------------------------
  // Task risk
  // -------------------------------------------------------

  if (
    overdueTasks.length > 0
  ) {
    const penalty =
      Math.min(
        overdueTasks.length
        *
        5,
        20
      );

    score -= penalty;

    riskSignals.push(
      `${overdueTasks.length} overdue task${
        overdueTasks.length === 1
          ? ""
          : "s"
      }`
    );
  }


  // -------------------------------------------------------
  // Governance status
  // -------------------------------------------------------

  if (
    !contact.is_active
  ) {
    score -= 20;

    riskSignals.push(
      "Contact is inactive"
    );
  }


  if (
    contact.is_archived
  ) {
    score -= 40;

    riskSignals.push(
      "Contact record is archived"
    );
  }


  score = clamp(
    score,
    0,
    100
  );


  const label:
    ContactHealthLabel =
      score >= 80
        ? "Strong"
        : score >= 60
          ? "Healthy"
          : score >= 40
            ? "Needs Attention"
            : "At Risk";


  let riskLevel:
    ContactRiskLevel =
      "Low";


  if (
    contact.is_archived
  ) {
    riskLevel =
      "Critical";
  } else if (
    !contact.is_active
  ) {
    riskLevel =
      "High";
  } else if (
    (
      daysSinceLastInteraction
      !== null
      &&
      daysSinceLastInteraction
      >
      90
    )
    ||
    overdueTasks.length
    >= 3
  ) {
    riskLevel =
      "Critical";
  } else if (
    daysSinceLastInteraction
    === null
    ||
    (
      daysSinceLastInteraction
      >
      60
    )
    ||
    overdueTasks.length
    >= 2
    ||
    engagementTrend
    === "Deteriorating"
  ) {
    riskLevel =
      "High";
  } else if (
    (
      daysSinceLastInteraction
      >
      30
    )
    ||
    overdueTasks.length > 0
    ||
    engagementTrend
    === "Cooling"
  ) {
    riskLevel =
      "Medium";
  }


  let actionPriority:
    ContactActionPriority =
      "Monitor";

  let actionType =
    "Maintain Relationship";

  let followUpDays:
    number | null =
      30;

  let actionReason =
    "Relationship is currently stable and requires normal monitoring.";


  if (
    contact.is_archived
  ) {
    actionPriority =
      "Monitor";

    actionType =
      "Review Archived Relationship";

    followUpDays =
      null;

    actionReason =
      "The contact record is archived and should not receive normal operational outreach.";
  } else if (
    !contact.is_active
  ) {
    actionPriority =
      "This Month";

    actionType =
      "Review Contact Status";

    followUpDays =
      30;

    actionReason =
      "The contact is inactive and should be reviewed before new outreach.";
  } else if (
    overdueTasks.length > 0
  ) {
    actionPriority =
      "Immediate";

    actionType =
      "Resolve Overdue Tasks";

    followUpDays =
      0;

    actionReason =
      `${overdueTasks.length} overdue task${
        overdueTasks.length === 1
          ? ""
          : "s"
      } require attention before additional follow-up is created.`;
  } else if (
    daysSinceLastInteraction
    === null
  ) {
    actionPriority =
      "This Week";

    actionType =
      "Create First Interaction";

    followUpDays =
      3;

    actionReason =
      "There is no recorded relationship activity for this contact.";
  } else if (
    engagementTrend
    === "Deteriorating"
  ) {
    actionPriority =
      "Immediate";

    actionType =
      "Re-engage Relationship";

    followUpDays =
      1;

    actionReason =
      "Engagement has dropped significantly compared with the previous 30-day period.";
  } else if (
    daysSinceLastInteraction
    > 90
  ) {
    actionPriority =
      "Immediate";

    actionType =
      "Re-engage Relationship";

    followUpDays =
      1;

    actionReason =
      "The relationship has been inactive for more than 90 days.";
  } else if (
    engagementTrend
    === "Cooling"
    ||
    daysSinceLastInteraction
    > 60
  ) {
    actionPriority =
      "This Week";

    actionType =
      "Schedule Follow-up";

    followUpDays =
      3;

    actionReason =
      "Relationship activity is declining and should be refreshed before it deteriorates further.";
  } else if (
    emailThreads.length === 0
  ) {
    actionPriority =
      "This Week";

    actionType =
      "Establish Email Communication";

    followUpDays =
      7;

    actionReason =
      "No email conversation is currently linked to this relationship.";
  } else if (
    openOpportunities.length > 0
  ) {
    actionPriority =
      "This Week";

    actionType =
      "Advance Opportunity";

    followUpDays =
      3;

    actionReason =
      `${openOpportunities.length} active sales opportunit${
        openOpportunities.length === 1
          ? "y"
          : "ies"
      } depend on this relationship.`;
  } else if (
    engagementTrend
    === "Improving"
  ) {
    actionPriority =
      "Monitor";

    actionType =
      "Maintain Engagement";

    followUpDays =
      14;

    actionReason =
      "Engagement is improving and should be maintained without unnecessary outreach.";
  } else if (
    daysSinceLastInteraction
    > 30
  ) {
    actionPriority =
      "This Month";

    actionType =
      "Schedule Relationship Check-in";

    followUpDays =
      14;

    actionReason =
      "The relationship has not been touched in more than 30 days.";
  }


  let recommendedAction =
    "Maintain the current relationship cadence.";


  if (
    contact.is_archived
  ) {
    recommendedAction =
      "Review whether this archived relationship should remain archived.";
  } else if (
    !contact.is_active
  ) {
    recommendedAction =
      "Review the inactive status before further outreach.";
  } else if (
    overdueTasks.length > 0
  ) {
    recommendedAction =
      "Resolve overdue follow-up tasks before creating additional outreach.";
  } else if (
    daysSinceLastInteraction
    === null
  ) {
    recommendedAction =
      "Create the first meaningful interaction with this contact.";
  } else if (
    engagementTrend
    === "Deteriorating"
  ) {
    recommendedAction =
      "Restore the relationship cadence with a high-value re-engagement touchpoint.";
  } else if (
    engagementTrend
    === "Cooling"
  ) {
    recommendedAction =
      "Schedule a follow-up before engagement declines further.";
  } else if (
    daysSinceLastInteraction
    >
    60
  ) {
    recommendedAction =
      "Schedule a relationship re-engagement touchpoint.";
  } else if (
    emailThreads.length === 0
  ) {
    recommendedAction =
      "Establish an email communication thread with this contact.";
  } else if (
    openOpportunities.length > 0
  ) {
    recommendedAction =
      "Review active opportunities and confirm the next commercial action.";
  } else if (
    recentTouchpoints === 0
  ) {
    recommendedAction =
      "Schedule the next relationship follow-up.";
  }


  return {
    score,
    label,

    lastInteractionAt,

    daysSinceLastInteraction,

    recentTouchpoints,

    emailConversationCount:
      emailThreads.length,

    openOpportunityCount:
      openOpportunities.length,

    overdueTaskCount:
      overdueTasks.length,

    currentPeriodTouchpoints,

    previousPeriodTouchpoints,

    touchpointDelta,

    engagementTrend,

    riskLevel,

    actionPriority,

    actionType,

    followUpDays,

    actionReason,

    positiveSignals,

    riskSignals,

    recommendedAction,
  };
}