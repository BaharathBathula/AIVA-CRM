import {
  CalendarDays,
  FileSignature,
  Mail,
  Phone,
} from "lucide-react";

const activities = [
  {
    icon: Mail,
    title: "Email sent to Alice Morgan",
    account: "Northstar Technologies",
    time: "12 minutes ago",
  },
  {
    icon: Phone,
    title: "Discovery call completed",
    account: "Vertex Insurance Group",
    time: "48 minutes ago",
  },
  {
    icon: FileSignature,
    title: "Proposal viewed",
    account: "Acme Manufacturing",
    time: "1 hour ago",
  },
  {
    icon: CalendarDays,
    title: "Enterprise demo scheduled",
    account: "Horizon Financial",
    time: "2 hours ago",
  },
];

export function ActivityFeed() {
  return (
    <section className="panel">
      <div className="panelHeader">
        <div>
          <h2>Recent Activity</h2>
          <p>Latest customer interactions</p>
        </div>

        <button className="textButton" type="button">
          View all
        </button>
      </div>

      <div className="activityList">
        {activities.map(({ icon: Icon, title, account, time }) => (
          <div className="activityRow" key={`${title}-${account}`}>
            <div className="activityIcon">
              <Icon size={17} />
            </div>

            <div className="activityContent">
              <strong>{title}</strong>
              <span>{account}</span>
            </div>

            <span className="activityTime">{time}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
