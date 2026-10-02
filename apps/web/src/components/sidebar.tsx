import Link from "next/link";

import {
  Activity,
  BarChart3,
  Bot,
  Building2,
  CalendarDays,
  ChartNoAxesCombined,
  Contact,
  FileSignature,
  Gauge,
  Handshake,
  Inbox,
  LayoutDashboard,
  Mail,
  Megaphone,
  Package,
  RefreshCcw,
  Settings,
  Sparkles,
  SquareKanban,
  Target,
  Users,
  Workflow,
} from "lucide-react";


type SidebarProps = {
  active?: string;
};


const groups = [
  {
    title: "Workspace",
    items: [
      {
        label: "Dashboard",
        icon: LayoutDashboard,
        href: "/",
      },
      {
        label: "Inbox",
        icon: Inbox,
      },
    ],
  },
  {
    title: "Sales",
    items: [
      {
        label: "Leads",
        icon: Target,
        href: "/leads",
      },
      {
        label: "Accounts",
        icon: Building2,
        href: "/accounts",
      },
      {
        label: "Contacts",
        icon: Contact,
      },
      {
        label: "Opportunities",
        icon: Handshake,
      },
      {
        label: "Pipeline",
        icon: SquareKanban,
      },
    ],
  },
  {
    title: "Engagement",
    items: [
      {
        label: "Activities",
        icon: Activity,
      },
      {
        label: "Tasks",
        icon: Gauge,
      },
      {
        label: "Email",
        icon: Mail,
      },
      {
        label: "Meetings",
        icon: CalendarDays,
      },
    ],
  },
  {
    title: "Operations",
    items: [
      {
        label: "Products",
        icon: Package,
      },
      {
        label: "Quotes",
        icon: FileSignature,
      },
      {
        label: "Contracts",
        icon: FileSignature,
      },
      {
        label: "Renewals",
        icon: RefreshCcw,
      },
    ],
  },
  {
    title: "Intelligence",
    items: [
      {
        label: "AIVA Agents",
        icon: Bot,
      },
      {
        label: "Workflows",
        icon: Workflow,
      },
      {
        label: "AI Insights",
        icon: Sparkles,
      },
    ],
  },
  {
    title: "Growth",
    items: [
      {
        label: "Marketing",
        icon: Megaphone,
      },
      {
        label: "Analytics",
        icon: BarChart3,
      },
      {
        label: "Forecasting",
        icon: ChartNoAxesCombined,
      },
    ],
  },
];


export function Sidebar({
  active = "Dashboard",
}: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brandMark">
          <Sparkles size={19} />
        </div>

        <div>
          <div className="brandName">
            AIVA
          </div>

          <div className="brandProduct">
            CRM
          </div>
        </div>
      </div>

      <nav className="navigation">
        {groups.map((group) => (
          <div
            className="navGroup"
            key={group.title}
          >
            <div className="navGroupTitle">
              {group.title}
            </div>

            {group.items.map((item) => {
              const Icon = item.icon;

              const className =
                `navItem ${
                  active === item.label
                    ? "active"
                    : ""
                }`;

              if (item.href) {
                return (
                  <Link
                    className={className}
                    href={item.href}
                    key={item.label}
                  >
                    <Icon
                      size={18}
                      strokeWidth={1.9}
                    />

                    <span>
                      {item.label}
                    </span>
                  </Link>
                );
              }

              return (
                <button
                  type="button"
                  className={className}
                  key={item.label}
                >
                  <Icon
                    size={18}
                    strokeWidth={1.9}
                  />

                  <span>
                    {item.label}
                  </span>
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="sidebarBottom">
        <button
          className="navItem"
          type="button"
        >
          <Users size={18} />
          <span>Team</span>
        </button>

        <button
          className="navItem"
          type="button"
        >
          <Settings size={18} />
          <span>Settings</span>
        </button>

        <div className="profile">
          <div className="avatar">
            BB
          </div>

          <div>
            <strong>
              Baharath
            </strong>

            <span>
              Administrator
            </span>
          </div>
        </div>
      </div>
    </aside>
  );
}
