import {
  ArrowUpRight,
  CircleAlert,
  Clock3,
  Sparkles,
} from "lucide-react";

const insights = [
  {
    icon: CircleAlert,
    title: "3 high-value deals need attention",
    description:
      "No customer interaction has been recorded in more than 7 days.",
  },
  {
    icon: ArrowUpRight,
    title: "ABC Corp engagement increased 37%",
    description:
      "Email activity and stakeholder participation are accelerating.",
  },
  {
    icon: Clock3,
    title: "XYZ renewal may be at risk",
    description:
      "Renewal is due in 31 days with two unresolved customer issues.",
  },
];

export function AIInsights() {
  return (
    <section className="panel aiPanel">
      <div className="panelHeader">
        <div>
          <div className="aiHeading">
            <Sparkles size={18} />
            <h2>AIVA Insights</h2>
          </div>

          <p>Recommendations generated from CRM activity</p>
        </div>
      </div>

      <div className="insights">
        {insights.map(({ icon: Icon, title, description }) => (
          <button className="insight" type="button" key={title}>
            <div className="insightIcon">
              <Icon size={18} />
            </div>

            <div>
              <strong>{title}</strong>
              <p>{description}</p>
            </div>
          </button>
        ))}
      </div>

      <button className="primaryButton" type="button">
        Review recommendations
      </button>
    </section>
  );
}
