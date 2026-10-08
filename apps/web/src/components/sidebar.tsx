"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  Activity,
  BarChart3,
  Bot,
  Box,
  BriefcaseBusiness,
  CalendarDays,
  ChartNoAxesCombined,
  CircleGauge,
  ContactRound,
  FilePenLine,
  Handshake,
  Inbox,
  LayoutDashboard,
  Mail,
  Megaphone,
  RefreshCw,
  Sparkles,
  Target,
  UserRound,
  Workflow,
} from "lucide-react";


type SidebarProps = {
  active?: string;
};


type MenuItem = {
  label: string;
  href: string;
  icon: React.ComponentType<{
    size?: number;
    strokeWidth?: number;
  }>;
};


type MenuSection = {
  title: string;
  items: MenuItem[];
};


const sections: MenuSection[] = [
  {
    title: "Workspace",
    items: [
      {
        label: "Dashboard",
        href: "/",
        icon: LayoutDashboard,
      },
      {
        label: "Inbox",
        href: "/inbox",
        icon: Inbox,
      },
    ],
  },

  {
    title: "Sales",
    items: [
      {
        label: "Leads",
        href: "/leads",
        icon: Target,
      },
      {
        label: "Accounts",
        href: "/accounts",
        icon: BriefcaseBusiness,
      },
      {
        label: "Contacts",
        href: "/contacts",
        icon: ContactRound,
      },
      {
        label: "Opportunities",
        href: "/opportunities",
        icon: Handshake,
      },
      {
        label: "Pipeline",
        href: "/pipeline",
        icon: BarChart3,
      },
    ],
  },

  {
    title: "Engagement",
    items: [
      {
        label: "Activities",
        href: "/activities",
        icon: Activity,
      },
      {
        label: "Tasks",
        href: "/tasks",
        icon: CircleGauge,
      },
      {
        label: "Email",
        href: "/email",
        icon: Mail,
      },
      {
        label: "Meetings",
        href: "/meetings",
        icon: CalendarDays,
      },
    ],
  },

  {
    title: "Operations",
    items: [
      {
        label: "Products",
        href: "/products",
        icon: Box,
      },
      {
        label: "Quotes",
        href: "/quotes",
        icon: FilePenLine,
      },
      {
        label: "Contracts",
        href: "/contracts",
        icon: FilePenLine,
      },
      {
        label: "Renewals",
        href: "/renewals",
        icon: RefreshCw,
      },
    ],
  },

  {
    title: "Intelligence",
    items: [
      {
        label: "AIVA Agents",
        href: "/agents",
        icon: Bot,
      },
      {
        label: "Workflows",
        href: "/workflows",
        icon: Workflow,
      },
      {
        label: "AI Insights",
        href: "/insights",
        icon: Sparkles,
      },
    ],
  },

  {
    title: "Growth",
    items: [
      {
        label: "Marketing",
        href: "/marketing",
        icon: Megaphone,
      },
      {
        label: "Analytics",
        href: "/analytics",
        icon: BarChart3,
      },
      {
        label: "Forecasting",
        href: "/forecasting",
        icon: ChartNoAxesCombined,
      },
    ],
  },
];


export function Sidebar({
  active,
}: SidebarProps) {
  const pathname =
    usePathname();


  function isActive(
    item: MenuItem
  ) {
    if (
      active
      &&
      active.toLowerCase()
      ===
      item.label.toLowerCase()
    ) {
      return true;
    }

    if (
      item.href === "/"
    ) {
      return pathname === "/";
    }

    return (
      pathname === item.href
      ||
      pathname.startsWith(
        `${item.href}/`
      )
    );
  }


  return (
    <aside className="sidebar">
      <div className="sidebarInner">
        {/* =========================================
            AIVA Brand
            ========================================= */}

        <Link
          href="/"
          className="sidebarBrand"
          aria-label="AIVA CRM Home"
        >
          <Image
            src="/aiva-logo.png"
            alt="AIVA CRM"
            width={172}
            height={60}
            priority
            className="sidebarBrandImage"
          />
        </Link>


        {/* =========================================
            Navigation
            ========================================= */}

        <nav className="sidebarNav">
          {sections.map(
            (section) => (
              <div
                className="sidebarSection"
                key={section.title}
              >
                <div className="sidebarSectionTitle">
                  {section.title}
                </div>

                <div className="sidebarSectionItems">
                  {section.items.map(
                    (item) => {
                      const Icon =
                        item.icon;

                      const selected =
                        isActive(item);

                      return (
                        <Link
                          key={item.label}
                          href={item.href}
                          className={
                            `sidebarItem${
                              selected
                                ? " active"
                                : ""
                            }`
                          }
                        >
                          <span className="sidebarItemIcon">
                            <Icon
                              size={18}
                              strokeWidth={1.8}
                            />
                          </span>

                          <span className="sidebarItemLabel">
                            {item.label}
                          </span>
                        </Link>
                      );
                    }
                  )}
                </div>
              </div>
            )
          )}
        </nav>


        {/* =========================================
            Sidebar footer
            ========================================= */}

        <div className="sidebarFooter">
          <div className="sidebarFooterDivider" />

          <div className="sidebarFooterBrand">
            AIVA CRM
          </div>
        </div>
      </div>
    </aside>
  );
}