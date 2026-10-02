import { ActivityFeed } from "@/components/activity-feed";
import { AIInsights } from "@/components/ai-insights";
import { Pipeline } from "@/components/pipeline";
import { Sidebar } from "@/components/sidebar";
import { StatCard } from "@/components/stat-card";
import { Topbar } from "@/components/topbar";
import { ArrowRight, Sparkles } from "lucide-react";

const stats = [
  {
    label: "Revenue",
    value: "$1.28M",
    change: 12.6,
    description: "vs previous quarter",
  },
  {
    label: "Pipeline",
    value: "$4.76M",
    change: 8.2,
    description: "vs previous quarter",
  },
  {
    label: "Win Rate",
    value: "34.8%",
    change: 3.4,
    description: "vs previous quarter",
  },
  {
    label: "Active Opportunities",
    value: "70",
    change: 9.8,
    description: "vs previous quarter",
  },
];

export default function Dashboard() {
  return (
    <div className="appShell">
      <Sidebar />

      <main className="mainArea">
        <Topbar />

        <div className="pageContent">
          <div className="pageHeading">
            <div>
              <p className="eyebrow">Friday, October 2</p>
              <h1>Good morning, Baharath.</h1>
              <p>
                Here&apos;s what is happening across your customer
                relationships.
              </p>
            </div>

            <button className="createButton" type="button">
              + Create
            </button>
          </div>

          <div className="statsGrid">
            {stats.map((stat) => (
              <StatCard key={stat.label} {...stat} />
            ))}
          </div>

          <section className="aivaBrief">
            <div className="briefIcon">
              <Sparkles size={20} />
            </div>

            <div className="briefContent">
              <span>AIVA DAILY BRIEF</span>

              <strong>
                $485K in pipeline requires attention today.
              </strong>

              <p>
                Three high-value deals have gone quiet, one renewal is at
                risk, and five follow-ups are overdue.
              </p>
            </div>

            <button type="button">
              Open briefing
              <ArrowRight size={16} />
            </button>
          </section>

          <div className="dashboardGrid">
            <Pipeline />
            <AIInsights />
          </div>

          <ActivityFeed />
        </div>
      </main>
    </div>
  );
}
