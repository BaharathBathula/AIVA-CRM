import {
  Activity as ActivityIcon,
  CalendarDays,
  FileText,
  Mail,
  MessageSquare,
  Phone,
  Smartphone,
} from "lucide-react";

import type {
  Activity,
  ActivityType,
} from "@/types/activity";

type Props = {
  activities: Activity[];
  onLogActivity: () => void;
};

function iconForType(
  type: ActivityType
) {
  if (type === "email") {
    return <Mail size={15} />;
  }

  if (type === "call") {
    return <Phone size={15} />;
  }

  if (type === "meeting") {
    return <CalendarDays size={15} />;
  }

  if (type === "note") {
    return <MessageSquare size={15} />;
  }

  if (type === "sms") {
    return <Smartphone size={15} />;
  }

  if (type === "document") {
    return <FileText size={15} />;
  }

  return <ActivityIcon size={15} />;
}

function formatDate(
  value: string
) {
  return new Intl.DateTimeFormat(
    "en-US",
    {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }
  ).format(new Date(value));
}

export function ActivityTimeline({
  activities,
  onLogActivity,
}: Props) {
  return (
    <section className="detailPanel">
      <div className="detailPanelHeader">
        <div>
          <h2>
            Activity Timeline
          </h2>

          <p>
            Calls, emails, meetings,
            notes and engagement history.
          </p>
        </div>

        <button
          type="button"
          className="textButton"
          onClick={onLogActivity}
        >
          + Log activity
        </button>
      </div>

      {activities.length === 0 ? (
        <div className="miniEmptyState">
          <ActivityIcon size={21} />

          <strong>
            No activity yet
          </strong>

          <span>
            Log the first interaction.
          </span>
        </div>
      ) : (
        <div className="timeline">
          {activities.map(
            (activity) => (
              <div
                className="timelineItem"
                key={activity.id}
              >
                <div className="timelineMarker">
                  {iconForType(
                    activity.activity_type
                  )}
                </div>

                <div className="timelineContent">
                  <div className="timelineTop">
                    <div>
                      <strong>
                        {activity.subject}
                      </strong>

                      <span className="activityTypeLabel">
                        {activity.activity_type.replaceAll(
                          "_",
                          " "
                        )}

                        {activity.direction
                          ? ` · ${activity.direction}`
                          : ""}
                      </span>
                    </div>

                    <time>
                      {formatDate(
                        activity.occurred_at
                      )}
                    </time>
                  </div>

                  {activity.body && (
                    <p>
                      {activity.body}
                    </p>
                  )}
                </div>
              </div>
            )
          )}
        </div>
      )}
    </section>
  );
}
